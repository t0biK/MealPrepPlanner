import json
import os
import random
import re
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import VERSION, db, ha, ingredients, plans, planner, recipes, worker

INGRESS_IP = "172.30.32.2"
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_NAME = 100
MAX_BODY = 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class ApiError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def ip_allowed(addr, mode):
    """Prod accepts only the Ingress proxy; dev is bound to 127.0.0.1 already."""
    return mode == "dev" or addr == INGRESS_IP


def current_user(headers, mode):
    """Return {id, name, display_name}, or None (-> 403) if the identity is missing/invalid."""
    if mode == "dev":
        name = os.environ.get("MEALPREP_DEV_USER", "Dev")[:MAX_NAME] or "Dev"
        return {"id": "dev", "name": name, "display_name": name}
    uid = headers.get("X-Remote-User-Id", "")
    if not USER_ID_RE.match(uid):
        return None
    name = headers.get("X-Remote-User-Name") or uid
    display = headers.get("X-Remote-User-Display-Name") or name
    if len(name) > MAX_NAME or len(display) > MAX_NAME:
        return None
    return {"id": uid, "name": name, "display_name": display}


def api_health(h, m):
    h.send_json(200, {"ok": True, "version": VERSION})


def api_me(h, m):
    h.send_json(200, h.user)


def api_put_me(h, m):
    body = h.read_json()
    if not isinstance(body, dict) or body.get("lang", "?") not in ("de", "en", None):
        raise db.InvalidField("lang")
    db.set_user_lang(h.conn, h.user["id"], body["lang"])
    h.user["lang"] = body["lang"]
    h.send_json(200, h.user)


def api_get_settings(h, m):
    h.send_json(200, db.get_settings(h.conn))


def api_put_settings(h, m):
    body = h.read_json()
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request")
    h.send_json(200, db.set_settings(h.conn, body))


def api_entities(h, m):
    domain = parse_qs(urlsplit(h.path).query).get("domain", [""])[0]
    if domain not in ("todo", "ai_task"):
        raise db.InvalidField("domain")
    h.send_json(200, ha.list_entities(domain))


# ---- Systemcheck (M1 spike): each check returns {ok, details} ----

TEST_ITEM = "MealPrep Test"
SAMPLE_CAPTION = (
    "Spaghetti mit Tomatensauce für 2 Personen: 200 g Spaghetti, 1 Dose gehackte Tomaten, "
    "2 Knoblauchzehen, 1 Zwiebel, etwas Olivenöl, Salz. Zwiebel und Knoblauch in Öl anbraten, "
    "Tomaten dazugeben, 15 Minuten köcheln lassen und mit den Nudeln mischen."
)


def _part(resp, *path):
    """Dig into an HA response (untrusted shape); None if anything is missing."""
    for key in path:
        resp = resp.get(key) if isinstance(resp, dict) else None
    return resp


def check_ha(conn):
    cfg = ha.get_config()
    now = datetime.now().astimezone()
    return {"ok": True, "details": {
        "ha_version": _part(cfg, "version"),
        "ha_time_zone": _part(cfg, "time_zone"),
        "ha_language": _part(cfg, "language"),
        "app_local_time": now.isoformat(timespec="seconds"),
        "app_tz_name": now.tzname(),
        "app_TZ_env": os.environ.get("TZ"),
    }}


def check_bring(conn):
    entity = db.get_settings(conn)["bring_entity"]
    if not entity:
        return {"ok": False, "details": {"error": "bring_entity not set"}}
    steps = []

    def call(step, service, data, rr=False):
        resp = ha.call_service("todo", service, {"entity_id": entity, **data}, return_response=rr)
        steps.append({"step": step, "response": resp})
        return resp

    def read(step):
        items = _part(call(step, "get_items", {}, True), "service_response", entity, "items")
        found = [i for i in items if isinstance(i, dict) and i.get("summary") == TEST_ITEM] if isinstance(items, list) else []
        steps.append({"step": f"{step}: items named {TEST_ITEM}", "count": len(found),
                      "descriptions": [i.get("description") for i in found]})
        return found

    ok = False
    try:
        call("add (Test 1)", "add_item", {"item": TEST_ITEM, "description": "Test 1"})
        call("add again (Test 2)", "add_item", {"item": TEST_ITEM, "description": "Test 2"})
        found = read("read after adding")
        if found:
            call("update first to Test 3", "update_item",
                 {"item": found[0].get("uid") or TEST_ITEM, "description": "Test 3"})
        read("read after update")
        ok = True
    except ha.HAError as e:
        steps.append({"step": "error", "code": e.code, "status": e.status})
    finally:
        try:
            for _ in range(10):
                found = read("cleanup read")
                if not found:
                    break
                call("cleanup remove", "remove_item", {"item": found[0].get("uid") or TEST_ITEM})
            else:
                ok = False
                steps.append({"step": "cleanup incomplete: test items remain in Bring!"})
        except ha.HAError as e:
            ok = False
            steps.append({"step": "cleanup error", "code": e.code, "status": e.status})
    return {"ok": ok, "details": steps}


def check_ai(conn):
    entity = db.get_settings(conn)["ai_entity"]
    if not entity:
        return {"ok": False, "details": {"error": "ai_entity not set"}}
    variants = {
        "A (structure)": {
            "instructions": "Extrahiere aus der Rezeptbeschreibung den Titel und die Zutaten (eine Zutat pro Eintrag).\n\n"
            + SAMPLE_CAPTION,
            "structure": {
                "title": {"required": True, "selector": {"text": None}},
                "ingredients": {"required": True, "selector": {"text": {"multiple": True}}},
            },
        },
        "B (JSON in instructions)": {
            "instructions": "Extrahiere aus der Rezeptbeschreibung den Titel und die Zutaten. Antworte ausschliesslich mit "
            'einem JSON-Objekt der Form {"title": "...", "ingredients": ["..."]} und ohne weiteren Text.\n\n'
            + SAMPLE_CAPTION,
        },
    }
    results = []
    for name, extra in variants.items():
        t0 = time.monotonic()
        entry = {"variant": name}
        try:
            resp = ha.call_service("ai_task", "generate_data",
                                   {"task_name": "mealprep_check", "entity_id": entity, **extra},
                                   return_response=True, timeout=120)
            data = _part(resp, "service_response", "data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = None
            good = isinstance(data, dict) and isinstance(data.get("title"), str) and isinstance(data.get("ingredients"), list)
            entry.update(raw=resp, parsed_ok=good, parsed=data if good else None)
        except ha.HAError as e:
            entry.update(error=e.code, status=e.status, parsed_ok=False)
        entry["duration_ms"] = round((time.monotonic() - t0) * 1000)
        results.append(entry)
    return {"ok": any(r["parsed_ok"] for r in results), "details": results}


def check_sensor(conn):
    ha.post_state("sensor.essensplan", "Systemcheck",
                  {"friendly_name": "Essensplan", "icon": "mdi:silverware-fork-knife"})
    back = ha.get_state("sensor.essensplan")
    return {"ok": _part(back, "state") == "Systemcheck", "details": back}


CHECKS = {"ha": check_ha, "bring": check_bring, "ai": check_ai, "sensor": check_sensor}


def api_check(h, m):
    body = h.read_json()
    name = body.get("check") if isinstance(body, dict) else None
    if name not in CHECKS:
        raise db.InvalidField("check")
    try:
        result = CHECKS[name](h.conn)
    except ha.HAError as e:
        result = {"ok": False, "details": {"error": e.code, "status": e.status}}
    h.send_json(200, result)


# ---- recipes, tags, ingredients (M2) ----

def _body_dict(h):
    body = h.read_json()
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request")
    return body


def _valid_draft(h):
    draft, errors = recipes.validate_draft(
        h.read_json(), [t["name"] for t in recipes.list_tags(h.conn)], h.server.data_dir / "images")
    if errors:
        raise db.InvalidField(next(iter(errors)))
    return draft


def _recipe_or_404(h, m):
    recipe = recipes.get_recipe(h.conn, int(m.group(1)))
    if recipe is None:
        raise ApiError(404, "not_found")
    return recipe


def api_list_recipes(h, m):
    qs = parse_qs(urlsplit(h.path).query)
    archived = qs.get("archived", ["0"])[0]
    if archived not in ("0", "1"):
        raise db.InvalidField("archived")
    sort = qs.get("sort", ["title"])[0]
    if sort not in recipes.SORTS:
        raise db.InvalidField("sort")
    unrated = qs.get("filter", [""])[0]
    if unrated not in ("", "unrated_by_me"):
        raise db.InvalidField("filter")
    h.send_json(200, recipes.list_recipes(
        h.conn, qs.get("q", [""])[0], qs.get("tag", [""])[0], archived == "1",
        user_id=h.user["id"], sort=sort, unrated_by_me=unrated == "unrated_by_me"))


def api_create_recipe(h, m):
    draft = _valid_draft(h)
    h.send_json(201, recipes.get_recipe(h.conn, recipes.create_recipe(h.conn, draft, h.user["id"])))


def api_get_recipe(h, m):
    recipe = _recipe_or_404(h, m)
    h.send_json(200, {**recipe, **recipes.rating_info(h.conn, recipe["id"], h.user["id"])})


def api_put_rating(h, m):
    stars = _body_dict(h).get("stars", "?")
    if stars is not None and (not isinstance(stars, int) or isinstance(stars, bool) or not 0 <= stars <= 5):
        raise db.InvalidField("stars")
    recipe_id = int(m.group(1))
    if not recipes.set_rating(h.conn, h.user["id"], recipe_id, stars):
        raise ApiError(404, "not_found")
    h.send_json(200, recipes.rating_info(h.conn, recipe_id, h.user["id"]))


def api_update_recipe(h, m):
    draft = _valid_draft(h)
    if not recipes.update_recipe(h.conn, int(m.group(1)), draft):
        raise ApiError(404, "not_found")
    h.send_json(200, recipes.get_recipe(h.conn, int(m.group(1))))


def _set_archived(archived):
    def handler(h, m):
        if not recipes.set_archived(h.conn, int(m.group(1)), archived):
            raise ApiError(404, "not_found")
        h.send_json(200, recipes.get_recipe(h.conn, int(m.group(1))))
    return handler


def api_list_tags(h, m):
    h.send_json(200, recipes.list_tags(h.conn))


def api_create_tag(h, m):
    h.send_json(201, recipes.create_tag(h.conn, _body_dict(h).get("name")))


def api_rename_tag(h, m):
    tag = recipes.rename_tag(h.conn, int(m.group(1)), _body_dict(h).get("name"))
    if tag is None:
        raise ApiError(404, "not_found")
    h.send_json(200, tag)


def api_delete_tag(h, m):
    if not recipes.delete_tag(h.conn, int(m.group(1))):
        raise ApiError(404, "not_found")
    h.send_json(200, {"ok": True})


def api_ingredient_names(h, m):
    h.send_json(200, recipes.known_ingredient_names(h.conn))


def api_units(h, m):
    h.send_json(200, [{"unit": u, "plural": plural} for u, (plural, _) in ingredients.UNITS.items()])


def api_parse_ingredients(h, m):
    text = _body_dict(h).get("text")
    if not isinstance(text, str) or len(text) > 20000:
        raise db.InvalidField("text")
    h.send_json(200, ingredients.parse_lines(text))


# ---- web import (M3) ----

JOB_STATUSES = ("queued", "running", "review", "failed", "done", "discarded")
MAX_BULK = 50
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _job_row(h, m):
    row = h.conn.execute("SELECT * FROM import_jobs WHERE id = ?", (int(m.group(1)),)).fetchone()
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _job_json(row, with_draft=False):
    draft = json.loads(row["draft"]) if row["draft"] else None
    out = {k: row[k] for k in ("id", "url", "origin", "status", "error", "recipe_id", "created_at", "updated_at")}
    out["title"] = draft["title"] if draft else None
    if with_draft:
        out["draft"] = draft
    return out


def _set_job(h, job_id, status, allowed_from):
    """Move a job to `status` if it is currently in `allowed_from`; else 400."""
    marks = ", ".join("?" * len(allowed_from))
    with h.conn:
        cur = h.conn.execute(
            f"UPDATE import_jobs SET status = ?, error = NULL, draft = CASE WHEN ? = 'queued' THEN NULL ELSE draft END, "
            f"text = CASE WHEN ? = 'queued' AND url IS NOT NULL THEN NULL ELSE text END, "  # a pasted caption is not kept on retry
            f"updated_at = ? WHERE id = ? AND status IN ({marks})",
            (status, status, status, datetime.now().isoformat(timespec="seconds"), job_id, *allowed_from))
    if cur.rowcount == 0:
        raise ApiError(400, "bad_request")


MAX_TEXT = worker.MAX_TEXT


def _pasted_text(body):
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise db.InvalidField("text")
    return text.strip()


def api_create_imports(h, m):
    body = _body_dict(h)
    if "text" in body:
        h.send_json(201, {"ids": [worker.enqueue_text(h.conn, _pasted_text(body), "single", h.user["id"])]})
        return
    if "urls" in body:
        if not isinstance(body["urls"], list):
            raise db.InvalidField("urls")
        urls = list(dict.fromkeys(u.strip() for u in body["urls"] if isinstance(u, str) and recipes.http_url(u.strip())))
        if not urls or len(urls) > MAX_BULK:
            raise db.InvalidField("urls")
        origin = "bulk"
    else:
        url = body.get("url")
        if not isinstance(url, str) or not recipes.http_url(url.strip()):
            raise db.InvalidField("url")
        urls, origin = [url.strip()], "single"
    h.send_json(201, {"ids": worker.enqueue(h.conn, urls, origin, h.user["id"])})


def api_list_imports(h, m):
    raw = parse_qs(urlsplit(h.path).query).get("status", [""])[0]
    statuses = raw.split(",") if raw else list(JOB_STATUSES)
    if any(s not in JOB_STATUSES for s in statuses):
        raise db.InvalidField("status")
    rows = h.conn.execute(
        f"SELECT * FROM import_jobs WHERE status IN ({', '.join('?' * len(statuses))}) ORDER BY id DESC LIMIT 100",
        statuses).fetchall()
    h.send_json(200, [_job_json(r) for r in rows])


def api_get_import(h, m):
    h.send_json(200, _job_json(_job_row(h, m), with_draft=True))


def api_save_import(h, m):
    job = _job_row(h, m)
    if job["status"] != "review":
        raise ApiError(400, "bad_request")
    recipe_id = recipes.create_recipe(h.conn, _valid_draft(h), h.user["id"])
    with h.conn:
        h.conn.execute("UPDATE import_jobs SET status = 'done', recipe_id = ?, updated_at = ? WHERE id = ?",
                       (recipe_id, datetime.now().isoformat(timespec="seconds"), job["id"]))
    h.send_json(201, recipes.get_recipe(h.conn, recipe_id))


def api_discard_import(h, m):
    _set_job(h, _job_row(h, m)["id"], "discarded", ("queued", "running", "review", "failed"))
    h.send_json(200, _job_json(_job_row(h, m)))


def api_retry_import(h, m):
    _set_job(h, _job_row(h, m)["id"], "queued", ("review", "failed"))
    h.send_json(200, _job_json(_job_row(h, m)))


def api_import_text(h, m):
    """Re-run the import of a draft on a pasted caption; link and image stay."""
    job, text = _job_row(h, m), _pasted_text(_body_dict(h))
    with h.conn:
        cur = h.conn.execute(
            "UPDATE import_jobs SET text = ?, status = 'queued', error = NULL, updated_at = ? "
            "WHERE id = ? AND status = 'review'", (text, datetime.now().isoformat(timespec="seconds"), job["id"]))
    if cur.rowcount == 0:
        raise ApiError(400, "bad_request")
    h.send_json(200, _job_json(_job_row(h, m)))


def api_image(h, m):
    try:
        body = (h.server.data_dir / "images" / m.group(1)).read_bytes()
    except OSError:
        raise ApiError(404, "not_found")
    h.send_response(200)
    h.send_header("Content-Type", IMAGE_TYPES[m.group(2)])
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "max-age=31536000, immutable")
    h.end_headers()
    h.wfile.write(body)


# ---- week planner (M7) ----

def _week(m):
    """Week id from the path: 'YYYY-Www' and an existing ISO week, else 400 invalid_field."""
    try:
        planner.week_dates(m.group(1))
    except ValueError:
        raise db.InvalidField("week")
    return m.group(1)


def _plan_json(h, week):
    return plans.view(h.conn, week, db.get_settings(h.conn), datetime.now().date())


def api_get_plan(h, m):
    h.send_json(200, _plan_json(h, _week(m)))


def api_generate_plan(h, m):
    week = _week(m)
    plans.generate(h.conn, week, db.get_settings(h.conn), random.Random())
    h.send_json(200, _plan_json(h, week))


def api_plan_slot(h, m):
    week = _week(m)
    day, meal = int(m.group(2)), m.group(3)
    if not 0 <= day <= 6:
        raise db.InvalidField("day")
    if meal not in planner.MEALS:
        raise db.InvalidField("meal")
    try:
        plans.slot_action(h.conn, week, day, meal, _body_dict(h), db.get_settings(h.conn), random.Random(),
                          datetime.now().date())
    except plans.Refused:
        raise ApiError(400, "bad_request")
    h.send_json(200, _plan_json(h, week))


def api_confirm_plan(h, m):
    week = _week(m)
    plans.confirm(h.conn, week, db.get_settings(h.conn))
    h.send_json(200, _plan_json(h, week))


ROUTES = [
    ("GET", re.compile(r"^/api/plans/([^/]+)$"), api_get_plan),
    ("POST", re.compile(r"^/api/plans/([^/]+)/generate$"), api_generate_plan),
    ("POST", re.compile(r"^/api/plans/([^/]+)/slots/(\d+)/([a-z]+)$"), api_plan_slot),
    ("POST", re.compile(r"^/api/plans/([^/]+)/confirm$"), api_confirm_plan),
    ("GET", re.compile(r"^/images/([0-9a-f]{64}\.(jpg|png|webp))$"), api_image),
    ("POST", re.compile(r"^/api/imports$"), api_create_imports),
    ("GET", re.compile(r"^/api/imports$"), api_list_imports),
    ("GET", re.compile(r"^/api/imports/(\d+)$"), api_get_import),
    ("POST", re.compile(r"^/api/imports/(\d+)/save$"), api_save_import),
    ("POST", re.compile(r"^/api/imports/(\d+)/discard$"), api_discard_import),
    ("POST", re.compile(r"^/api/imports/(\d+)/retry$"), api_retry_import),
    ("POST", re.compile(r"^/api/imports/(\d+)/text$"), api_import_text),
    ("GET", re.compile(r"^/api/recipes$"), api_list_recipes),
    ("POST", re.compile(r"^/api/recipes$"), api_create_recipe),
    ("GET", re.compile(r"^/api/recipes/(\d+)$"), api_get_recipe),
    ("PUT", re.compile(r"^/api/recipes/(\d+)$"), api_update_recipe),
    ("PUT", re.compile(r"^/api/recipes/(\d+)/rating$"), api_put_rating),
    ("POST", re.compile(r"^/api/recipes/(\d+)/archive$"), _set_archived(True)),
    ("POST", re.compile(r"^/api/recipes/(\d+)/restore$"), _set_archived(False)),
    ("GET", re.compile(r"^/api/tags$"), api_list_tags),
    ("POST", re.compile(r"^/api/tags$"), api_create_tag),
    ("PUT", re.compile(r"^/api/tags/(\d+)$"), api_rename_tag),
    ("DELETE", re.compile(r"^/api/tags/(\d+)$"), api_delete_tag),
    ("GET", re.compile(r"^/api/ingredient-names$"), api_ingredient_names),
    ("GET", re.compile(r"^/api/units$"), api_units),
    ("POST", re.compile(r"^/api/parse-ingredients$"), api_parse_ingredients),
    ("GET", re.compile(r"^/api/health$"), api_health),
    ("GET", re.compile(r"^/api/me$"), api_me),
    ("PUT", re.compile(r"^/api/me$"), api_put_me),
    ("GET", re.compile(r"^/api/settings$"), api_get_settings),
    ("PUT", re.compile(r"^/api/settings$"), api_put_settings),
    ("GET", re.compile(r"^/api/ha/entities$"), api_entities),
    ("POST", re.compile(r"^/api/system/check$"), api_check),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "MealPrepPlanner/" + VERSION
    user = None
    conn = None
    body_len = 0

    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, status, code=None, field=None, *args):
        # also called by http.server internals as send_error(code, message): then `code` is a message
        err = {"error": code if isinstance(code, str) and re.fullmatch(r"[a-z_]+", code) else "bad_request"}
        if field:
            err["field"] = field
        self.send_json(status, err)

    def send_static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        root = self.server.static_dir
        try:
            target = (root / rel).resolve()
            ok = target.is_relative_to(root) and target.suffix in CONTENT_TYPES and target.is_file()
            body = target.read_bytes() if ok else None
        except (OSError, ValueError):
            body = None
        if body is None:
            return self.send_error(404, "not_found")
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[target.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            return json.loads(self.rfile.read(self.body_len))
        except ValueError:
            raise ApiError(400, "bad_request")

    def run_api(self, fn, match, method):
        try:
            if method != "GET":
                if self.headers.get_content_type() != "application/json":
                    raise ApiError(415, "unsupported_media_type")
                try:
                    self.body_len = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise ApiError(400, "bad_request")
                if self.body_len < 0:
                    raise ApiError(400, "bad_request")
                if self.body_len > MAX_BODY:
                    raise ApiError(413, "too_large")
            fn(self, match)
        except ApiError as e:
            self.send_error(e.status, e.code)
        except db.InvalidField as e:
            self.send_error(400, "invalid_field", e.field)
        except ha.HAError as e:
            self.send_error(503 if e.code == "ha_unavailable" else 502, e.code)

    def dispatch(self, method):
        mode = self.server.mode
        if not ip_allowed(self.client_address[0], mode):
            return self.send_error(403, "forbidden")
        path = urlsplit(self.path).path
        self.user = current_user(self.headers, mode)
        if self.user is None:
            return self.send_error(403, "forbidden")
        self.conn = db.connect(self.server.data_dir)
        try:
            self.user["lang"] = db.upsert_user(self.conn, self.user)
            for m, rx, fn in ROUTES:
                match = rx.match(path)
                if m == method and match:
                    return self.run_api(fn, match, method)
            if path.startswith("/api/") or method != "GET":
                return self.send_error(404, "not_found")
            self.send_static(path)
        finally:
            self.conn.close()

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def make_server(host, port, static_dir, data_dir, mode="dev"):
    conn = db.connect(data_dir)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.static_dir = Path(static_dir).resolve()
    httpd.data_dir = Path(data_dir)
    httpd.mode = mode
    return httpd
