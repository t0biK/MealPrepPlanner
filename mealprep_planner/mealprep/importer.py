import hashlib
import html
import http.client
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import VERSION, db, recipes
from .ingredients import parse_line

USER_AGENT = f"MealPrepPlanner/{VERSION} (+https://github.com/t0biK/MealPrepPlanner)"
ALLOWED_PORTS = (80, 443)
TIMEOUT = 15
MAX_REDIRECTS = 5
MAX_HTML = 3 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024


class FetchError(Exception):
    """`code` is an API error code: fetch_blocked, fetch_failed or fetch_too_large."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- fetching (SSRF-guarded) ----

def _is_global(ip):
    addr = ipaddress.ip_address(ip)
    return (getattr(addr, "ipv4_mapped", None) or addr).is_global


def check_url(url):
    """Raise FetchError unless url is a plain http(s) URL on port 80/443 whose host resolves only to global IPs."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FetchError("fetch_blocked")
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc or port not in ALLOWED_PORTS:
        raise FetchError("fetch_blocked")
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        blocked = not addrs or not all(_is_global(a) for a in addrs)
    except (socket.gaierror, UnicodeError):
        raise FetchError("fetch_failed")
    except ValueError:
        blocked = True
    if blocked:
        raise FetchError("fetch_blocked")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_url(newurl)
        except FetchError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# no cookie processor and no proxies
_opener = urllib.request.build_opener(_CheckedRedirects, urllib.request.ProxyHandler({}))


def fetch(url, max_bytes, accept):
    """GET url -> (final_url, content_type, body). Raises FetchError."""
    check_url(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "de-DE,de;q=0.9"})
    try:
        # ponytail: the timeout applies per socket read, not to the whole download; add a deadline if a slow server hangs the worker
        with _opener.open(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise FetchError("fetch_too_large")
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError("fetch_too_large")
            return resp.geturl(), resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        e.close()
        raise FetchError("fetch_failed")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        raise FetchError("fetch_failed")


def decode(body, content_type):
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    try:
        return body.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---- images ----

def image_ext(data):
    """File extension by magic bytes (JPEG, PNG, WebP), else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"\x89PNG":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def download_image(url, data_dir):
    """Store the image as images/<sha256>.<ext>; returns the file name or None."""
    try:
        data = fetch(url, MAX_IMAGE, "image/jpeg,image/png,image/webp")[2]
        ext = image_ext(data)
        if ext is None:
            return None
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        folder = Path(data_dir) / "images"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if not target.exists():
            tmp = folder / (name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return name
    except (FetchError, OSError):
        return None


# ---- HTML extraction ----

class _Extractor(HTMLParser):
    META = ("og:title", "og:image", "og:description")

    def __init__(self):
        super().__init__()
        self.jsonld = []
        self.meta = {}
        self.title = ""
        self._script = None
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").strip().lower() == "application/ld+json":
            self._script = []
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            if key in self.META and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "title" and not self._title_done:
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try:
                self.jsonld.append(json.loads("".join(self._script), strict=False))
            except (ValueError, RecursionError):
                pass
            self._script = None
        elif tag == "title" and self._in_title:
            self._in_title, self._title_done = False, True

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._in_title:
            self.title += data


def extract(page_html, base_url):
    """JSON-LD objects plus OpenGraph/title fallbacks of an HTML page."""
    p = _Extractor()
    p.feed(page_html)
    p.close()
    image = p.meta.get("og:image", "").strip()
    image = urljoin(base_url, image) if image else None
    return {
        "jsonld": p.jsonld,
        "og_title": _line(p.meta.get("og:title", "")),
        "og_image": image if image and recipes.http_url(image) else None,
        "og_description": _line(p.meta.get("og:description", "")),
        "title": _line(p.title),
    }


# ---- schema.org Recipe -> draft ----

def _line(s):
    return " ".join(s.split())


def _clean(v):
    """Plain text of an untrusted string: tags dropped, entities decoded, whitespace collapsed."""
    return _line(html.unescape(re.sub(r"<[^>]*>", "", v))) if isinstance(v, str) else ""


def _as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def _find_recipe(node):
    if isinstance(node, list):
        return next((r for r in map(_find_recipe, node) if r), None)
    if not isinstance(node, dict):
        return None
    types = [str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] for t in _as_list(node.get("@type"))]
    return node if "Recipe" in types else _find_recipe(node.get("@graph"))


def _first_int(v):
    for item in _as_list(v):
        m = re.search(r"\d+", str(item))
        if m:
            return int(m.group())
    return None


_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+(?:\.\d+)?S)?)?$")


def _minutes(v):
    m = _DURATION.match(v.strip()) if isinstance(v, str) else None
    return int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) if m else 0


def _steps(v):
    if isinstance(v, str):
        lines = re.split(r"\r?\n|<br\s*/?>|</p>|</li>", v, flags=re.I)
        return [s for s in map(_clean, lines) if s]
    if isinstance(v, list):
        return [s for item in v for s in _steps(item)]
    if isinstance(v, dict):
        if "itemListElement" in v:  # HowToSection
            return _steps(v["itemListElement"])
        return _steps(v.get("text") or v.get("name"))
    return []


def _image_url(v, base_url):
    first = _as_list(v)[:1]
    v = first[0] if first else None
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl")
    if not isinstance(v, str) or not v.strip():
        return None
    url = urljoin(base_url, v.strip())
    return url if recipes.http_url(url) else None


def _nutrition(v):
    if not isinstance(v, dict):
        return None
    out = {}
    for key, field in (("kcal", "calories"), ("protein_g", "proteinContent"),
                       ("fat_g", "fatContent"), ("carbs_g", "carbohydrateContent")):
        raw = v.get(field)
        m = re.search(r"\d+(?:[.,]\d+)?", raw) if isinstance(raw, str) else None
        num = float(m.group().replace(",", ".")) if m else raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        out[key] = num if num is not None and 0 <= num <= recipes.NUTRITION_MAX[key] else None
    return {**out, "source": "page"} if any(x is not None for x in out.values()) else None


def recipe_from_jsonld(objs, default_portions, tag_names, base_url=""):
    """First schema.org Recipe in the JSON-LD objects -> draft fields (not yet validated), or None."""
    node = _find_recipe(objs)
    if node is None:
        return None
    servings = _first_int(node.get("recipeYield"))
    minutes = _minutes(node.get("totalTime")) or _minutes(node.get("prepTime")) + _minutes(node.get("cookTime"))
    ingredients = []
    for line in _as_list(node.get("recipeIngredient")):
        line = _clean(line)
        if line:
            i = parse_line(line)
            if i["amount"] is not None and not 0 < i["amount"] <= 100000:
                i["amount"] = None
            ingredients.append({**i, "name": i["name"][:100], "note": i["note"][:200] if i["note"] else None})
    known = {n.casefold(): n for n in tag_names}
    words = []
    for field in ("keywords", "recipeCategory", "recipeCuisine"):
        for item in _as_list(node.get(field)):
            words += str(item).split(",") if isinstance(item, str) else []
    tags = list(dict.fromkeys(known[w.strip().casefold()] for w in words if w.strip().casefold() in known))
    return {
        "title": _clean(node.get("name"))[:200],
        "servings": servings if servings and 1 <= servings <= 50 else default_portions,
        "total_minutes": minutes if 1 <= minutes <= 1440 else None,
        "tags": tags[:15],
        "ingredients": ingredients[:100],
        "steps": [s[:2000] for s in _steps(node.get("recipeInstructions"))][:50],
        "image_url": _image_url(node.get("image"), base_url),
        "nutrition": _nutrition(node.get("nutrition")),
    }


# ---- import job -> draft ----

def build_draft(job, conn, data_dir):
    """Fetch the job's page and build a validated recipe draft (§6). Raises FetchError."""
    url = job["url"]
    final_url, content_type, body = fetch(url, MAX_HTML, "text/html,application/xhtml+xml")
    page = extract(decode(body, content_type), final_url)
    default_portions = db.get_settings(conn)["default_portions"]
    tag_names = [t["name"] for t in recipes.list_tags(conn)]

    warnings = []
    if conn.execute("SELECT 1 FROM recipes WHERE source_url IN (?, ?)", (url, final_url)).fetchone():
        warnings.append("already_imported")
    base = {"format_version": 1, "source_kind": "web", "source_url": final_url if len(final_url) <= 2048 else url}

    draft = None
    fields = recipe_from_jsonld(page["jsonld"], default_portions, tag_names, final_url)
    if fields:
        fields["image_url"] = fields["image_url"] or page["og_image"]  # JSON-LD often only references the image by @id
        draft, _ = recipes.validate_draft({**base, **fields, "warnings": warnings}, tag_names)
    if draft is None:
        warnings.append("no_recipe_data")
        host = urlsplit(final_url).hostname or url
        draft, _ = recipes.validate_draft({
            **base, "title": (page["og_title"] or page["title"] or host)[:200], "servings": default_portions,
            "image_url": page["og_image"], "warnings": warnings}, tag_names)

    if draft.get("image_url"):
        name = download_image(draft["image_url"], data_dir)
        if name:
            draft["image"] = name
        else:
            draft["warnings"].append("image_failed")
    return draft
