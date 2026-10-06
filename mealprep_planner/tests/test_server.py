import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from mealprep import VERSION, db, server

STATIC = Path(__file__).resolve().parent.parent / "static"


def headers(**kw):
    h = http.client.HTTPMessage()
    for k, v in kw.items():
        h[k.replace("_", "-")] = v
    return h


class DevServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = server.make_server("127.0.0.1", 0, STATIC, cls.tmp.name)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def send(self, method, path, body=b"{}", ctype="application/json", length=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path)
        c.putheader("Content-Type", ctype)
        c.putheader("Content-Length", str(len(body) if length is None else length))
        c.endheaders(body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_non_json_write_is_415(self):
        r, body = self.send("PUT", "/api/me", b"lang=de", "text/plain")
        self.assertEqual(r.status, 415)
        self.assertEqual(json.loads(body)["error"], "unsupported_media_type")

    def test_oversized_write_is_413(self):
        r, body = self.send("PUT", "/api/me", b"{}", length=1024 * 1024 + 1)
        self.assertEqual(r.status, 413)
        self.assertEqual(json.loads(body)["error"], "too_large")

    def test_put_me_accepts_only_de_en_null(self):
        for lang in ("de", "en", None):
            r, body = self.send("PUT", "/api/me", json.dumps({"lang": lang}).encode())
            self.assertEqual((r.status, json.loads(body)["lang"]), (200, lang))
        for bad in ("fr", 1, ""):
            r, body = self.send("PUT", "/api/me", json.dumps({"lang": bad}).encode())
            self.assertEqual(r.status, 400)
            self.assertEqual(json.loads(body), {"error": "invalid_field", "field": "lang"})

    def test_settings_roundtrip_and_validation(self):
        r, body = self.send("PUT", "/api/settings", b'{"ai_enabled": false}')
        self.assertEqual(json.loads(body)["ai_enabled"], False)
        r, body = self.send("PUT", "/api/settings", b'{"ai_enabled": "yes"}')
        self.assertEqual((r.status, json.loads(body)["field"]), (400, "ai_enabled"))
        r, body = self.get("/api/settings")
        self.assertEqual(json.loads(body)["ai_enabled"], False)

    def call(self, method, path, obj=None):
        if method == "GET":
            r, body = self.get(path)
        else:
            r, body = self.send(method, path, json.dumps(obj if obj is not None else {}).encode())
        return r.status, json.loads(body)

    def test_default_portions_setting(self):
        self.assertEqual(self.call("PUT", "/api/settings", {"default_portions": 4})[1]["default_portions"], 4)
        for bad in (0, 13, "2", True, 2.5):
            self.assertEqual(self.call("PUT", "/api/settings", {"default_portions": bad})[0], 400)
        self.call("PUT", "/api/settings", {"default_portions": 2})

    def test_recipe_lifecycle(self):
        draft = {"format_version": 1, "title": "API Suppe", "servings": 2, "tags": ["suppe"],
                 "ingredients": [{"amount": 2, "unit": "Dose", "name": "Tomaten"}], "steps": ["Kochen."]}
        status, created = self.call("POST", "/api/recipes", draft)
        self.assertEqual(status, 201)
        rid = created["id"]
        self.assertEqual(created["tags"], ["Suppe"])
        self.assertEqual(self.call("GET", f"/api/recipes/{rid}")[1]["ingredients"][0]["unit"], "Dose")

        status, updated = self.call("PUT", f"/api/recipes/{rid}", {**draft, "title": "API Suppe 2"})
        self.assertEqual((status, updated["title"]), (200, "API Suppe 2"))

        self.assertEqual(self.call("POST", f"/api/recipes/{rid}/archive")[1]["archived"], True)
        listed = lambda arch: [r["id"] for r in self.call("GET", f"/api/recipes?archived={arch}")[1]]
        self.assertEqual((rid in listed(0), rid in listed(1)), (False, True))
        self.assertEqual(self.call("POST", f"/api/recipes/{rid}/restore")[1]["archived"], False)
        self.assertEqual(self.call("GET", "/api/recipes?q=tomaten&tag=Suppe")[1][0]["id"], rid)
        self.assertIn("Tomaten", self.call("GET", "/api/ingredient-names")[1])

    def test_recipe_errors(self):
        self.assertEqual(self.call("POST", "/api/recipes", {"format_version": 1, "title": "", "servings": 2})[1],
                         {"error": "invalid_field", "field": "title"})
        self.assertEqual(self.call("GET", "/api/recipes/999999")[0], 404)
        self.assertEqual(self.call("PUT", "/api/recipes/999999", {"format_version": 1, "title": "x", "servings": 1})[0], 404)
        self.assertEqual(self.call("POST", "/api/recipes/999999/archive")[0], 404)
        self.assertEqual(self.call("GET", "/api/recipes?archived=x")[1]["field"], "archived")
        # a draft may not reference an image that is not stored
        bad = {"format_version": 1, "title": "x", "servings": 1, "image": "a" * 64 + ".jpg"}
        self.assertEqual(self.call("POST", "/api/recipes", bad)[1]["field"], "image")

    def test_rating_lifecycle_and_recipe_fields(self):
        mk = lambda title, tags: self.call("POST", "/api/recipes", {"format_version": 1, "title": title, "servings": 2, "tags": tags})[1]["id"]
        a, b, c, d = mk("Rating A", ["Nudeln"]), mk("Rating B", ["Nudeln"]), mk("Rating C", ["Suppe"]), mk("Rating D", ["Suppe"])
        detail = lambda rid: self.call("GET", f"/api/recipes/{rid}")[1]
        self.assertEqual((detail(a)["ratings"], detail(a)["my_stars"], detail(a)["vetoed"]), ([], None, False))
        self.assertIsNotNone(detail(a)["my_prediction"])

        status, info = self.call("PUT", f"/api/recipes/{a}/rating", {"stars": 5})
        self.assertEqual((status, info["my_stars"], info["my_prediction"], info["vetoed"]), (200, 5, None, False))
        self.assertEqual([(r["user_id"], r["stars"]) for r in info["ratings"]], [("dev", 5)])
        self.call("PUT", f"/api/recipes/{d}/rating", {"stars": 1})
        self.assertGreater(detail(b)["my_prediction"], detail(c)["my_prediction"])  # b shares the tag of a well-rated recipe
        self.assertEqual(self.call("PUT", f"/api/recipes/{a}/rating", {"stars": 3})[1]["my_stars"], 3)  # one current rating
        self.assertEqual(len(detail(a)["ratings"]), 1)

        info = self.call("PUT", f"/api/recipes/{b}/rating", {"stars": 0})[1]
        self.assertEqual((info["my_stars"], info["vetoed"]), (0, True))  # 0 is a veto, not "unrated"
        listed = {r["id"]: r for r in self.call("GET", "/api/recipes")[1]}
        self.assertEqual((listed[a]["my_stars"], listed[b]["my_stars"], listed[c]["my_stars"]), (3, 0, None))
        self.assertEqual((listed[b]["vetoed"], listed[a]["vetoed"]), (True, False))
        self.assertEqual(listed[a]["household_score"], 3)
        ids = lambda qs: [r["id"] for r in self.call("GET", "/api/recipes?" + qs)[1] if r["id"] in (a, b, c, d)]
        self.assertEqual(ids("filter=unrated_by_me"), [c])
        self.assertEqual(ids("sort=score"), [a, c, d, b])
        self.assertEqual(ids("sort=new"), [d, c, b, a])
        self.assertEqual(ids("sort=title"), [a, b, c, d])

        info = self.call("PUT", f"/api/recipes/{a}/rating", {"stars": None})[1]  # tap again: cleared
        self.assertEqual((info["my_stars"], info["ratings"]), (None, []))
        self.assertEqual(ids("filter=unrated_by_me"), [a, c])

    def test_rating_errors(self):
        rid = self.call("POST", "/api/recipes", {"format_version": 1, "title": "Rating Err", "servings": 2})[1]["id"]
        for bad in ({}, {"stars": 6}, {"stars": -1}, {"stars": "3"}, {"stars": True}, {"stars": 2.5}):
            self.assertEqual(self.call("PUT", f"/api/recipes/{rid}/rating", bad), (400, {"error": "invalid_field", "field": "stars"}))
        self.assertEqual(self.call("PUT", "/api/recipes/999999/rating", {"stars": 3})[0], 404)
        self.assertEqual(self.call("GET", "/api/recipes?sort=best")[1]["field"], "sort")
        self.assertEqual(self.call("GET", "/api/recipes?filter=x")[1]["field"], "filter")

    def plan_slot(self, plan, day, meal):
        return next(x for x in plan["slots"] if (x["day"], x["meal"]) == (day, meal))

    def test_plan_week_validation_and_draft_from_slot_pattern(self):
        for bad in ("2026-W54", "2027-W53", "2026-W00", "abc", "2026-1"):
            self.assertEqual(self.call("GET", f"/api/plans/{bad}"), (400, {"error": "invalid_field", "field": "week"}))
            self.assertEqual(self.call("POST", f"/api/plans/{bad}/generate")[1]["field"], "week")
            self.assertEqual(self.call("POST", f"/api/plans/{bad}/confirm")[1]["field"], "week")
        pattern = [i not in (0, 13) for i in range(14)]  # Monday lunch and Sunday dinner off
        self.call("PUT", "/api/settings", {"slot_pattern": pattern, "default_portions": 3})
        try:
            status, plan = self.call("GET", "/api/plans/2031-W01")
        finally:
            self.call("PUT", "/api/settings", {"slot_pattern": [True] * 14, "default_portions": 2})
        self.assertEqual((status, plan["status"], plan["confirmed_at"], len(plan["slots"])), (200, "draft", None, 14))
        self.assertEqual(plan["dates"][0], "2030-12-30")
        self.assertEqual([s["active"] for s in plan["slots"]], pattern)
        self.assertEqual({s["portions"] for s in plan["slots"]}, {3})
        self.assertEqual(len(plan["totals"]), 7)
        self.assertEqual(self.call("GET", "/api/plans/2031-W01")[1]["slots"], plan["slots"])  # stored, not recreated

    def test_plan_generate_actions_and_confirm(self):
        week = "2030-W10"
        mk = lambda title, **extra: self.call("POST", "/api/recipes", {"format_version": 1, "title": title, "servings": 2, **extra})[1]["id"]
        ids = [mk(f"Plan {i}") for i in range(16)]
        nutri = mk("Plan Nutri", nutrition={"kcal": 600, "protein_g": 30, "fat_g": 20, "carbs_g": 70, "source": "ai"})
        never = mk("Plan Never")
        self.call("PUT", f"/api/recipes/{never}/rating", {"stars": 0})
        post = lambda path, body=None: self.call("POST", f"/api/plans/{week}/{path}", body)

        status, plan = post("generate")
        self.assertEqual(status, 200)
        picked = [s["recipe"]["id"] for s in plan["slots"]]
        self.assertEqual(len(set(picked)), 14)
        self.assertNotIn(never, picked)  # vetoed recipes are never suggested
        self.assertTrue(all(s["reason"]["kind"] in ("rated", "predicted", "new") for s in plan["slots"]))

        # lock survives a new suggestion
        locked = plan["slots"][0]["recipe"]["id"]
        self.assertTrue(post("slots/0/lunch", {"action": "lock"})[1]["slots"][0]["locked"])
        again = post("generate")[1]
        self.assertEqual((again["slots"][0]["recipe"]["id"], again["slots"][0]["locked"]), (locked, True))

        # reroll gives another recipe; inactive slots cannot be rerolled; locked slot recipe not reused elsewhere
        before = self.plan_slot(again, 1, "dinner")["recipe"]["id"]
        rerolled = post("slots/1/dinner", {"action": "reroll"})[1]
        self.assertNotEqual(self.plan_slot(rerolled, 1, "dinner")["recipe"]["id"], before)
        self.assertEqual(len({s["recipe"]["id"] for s in rerolled["slots"]}), 14)
        off = post("slots/2/lunch", {"action": "deactivate"})[1]
        self.assertFalse(self.plan_slot(off, 2, "lunch")["active"])
        self.assertEqual(post("slots/2/lunch", {"action": "reroll"}), (400, {"error": "bad_request"}))
        self.assertTrue(self.plan_slot(post("slots/2/lunch", {"action": "activate"})[1], 2, "lunch")["active"])

        # portions, set (also vetoed), clear, validation
        self.assertEqual(self.plan_slot(post("slots/3/lunch", {"action": "portions", "portions": 5})[1], 3, "lunch")["portions"], 5)
        for bad in (0, 13, "2", True, None):
            self.assertEqual(post("slots/3/lunch", {"action": "portions", "portions": bad})[1]["field"], "portions")
        slot = self.plan_slot(post("slots/3/lunch", {"action": "set", "recipe_id": never})[1], 3, "lunch")
        self.assertEqual((slot["recipe"]["id"], slot["reason"]), (never, {"kind": "manual"}))
        slot = self.plan_slot(post("slots/4/dinner", {"action": "set", "recipe_id": nutri})[1], 4, "dinner")
        self.assertEqual(slot["recipe"]["title"], "Plan Nutri")
        totals = self.call("GET", f"/api/plans/{week}")[1]["totals"]
        self.assertEqual((totals[4]["kcal"], totals[4]["estimated"], totals[4]["incomplete"]), (600, True, True))  # lunch has no nutrition
        self.call("POST", f"/api/recipes/{ids[0]}/archive")
        for bad in (ids[0], 999999, "x", None, True):
            self.assertEqual(post("slots/0/dinner", {"action": "set", "recipe_id": bad})[1]["field"], "recipe_id")
        cleared = self.plan_slot(post("slots/3/lunch", {"action": "clear"})[1], 3, "lunch")
        self.assertEqual((cleared["recipe"], cleared["reason"], cleared["locked"]), (None, None, False))
        self.assertEqual(post("slots/0/lunch", {"action": "fly"})[1]["field"], "action")
        self.assertEqual(post("slots/0/lunch", {})[1]["field"], "action")
        self.assertEqual(post("slots/7/lunch", {"action": "lock"})[1]["field"], "day")
        self.assertEqual(post("slots/0/brunch", {"action": "lock"})[1]["field"], "meal")

        # skip needs a confirmed plan and a date <= today
        self.assertEqual(post("slots/0/lunch", {"action": "skip"})[0], 400)
        status, plan = post("confirm")
        self.assertEqual((status, plan["status"]), (200, "confirmed"))
        self.assertTrue(plan["confirmed_at"])
        self.assertEqual(post("slots/0/lunch", {"action": "skip"})[0], 400)  # future week
        self.assertEqual(post("slots/5/lunch", {"action": "set", "recipe_id": nutri})[0], 200)  # editing stays allowed

    def test_plan_skip_in_the_past(self):
        week = "2020-W10"
        rid = self.call("POST", "/api/recipes", {"format_version": 1, "title": "Past meal", "servings": 2})[1]["id"]
        post = lambda path, body=None: self.call("POST", f"/api/plans/{week}/{path}", body)
        post("slots/0/lunch", {"action": "set", "recipe_id": rid})
        self.assertEqual(post("slots/0/lunch", {"action": "skip"})[0], 400)  # still a draft
        self.assertEqual(post("confirm")[1]["status"], "confirmed")
        plan = post("slots/0/lunch", {"action": "skip"})[1]
        self.assertTrue(self.plan_slot(plan, 0, "lunch")["skipped"])
        self.assertEqual(plan["totals"][0]["kcal"], 0)
        self.assertFalse(self.plan_slot(post("slots/0/lunch", {"action": "unskip"})[1], 0, "lunch")["skipped"])

    def test_tag_crud(self):
        status, tag = self.call("POST", "/api/tags", {"name": "ServerTag"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("POST", "/api/tags", {"name": "servertag"})[1]["field"], "name")
        self.assertEqual(self.call("PUT", f"/api/tags/{tag['id']}", {"name": "Renamed"})[1]["name"], "Renamed")
        self.assertIn("Renamed", [t["name"] for t in self.call("GET", "/api/tags")[1]])
        self.assertEqual(self.call("DELETE", f"/api/tags/{tag['id']}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/tags/{tag['id']}")[0], 404)
        self.assertEqual(self.call("PUT", "/api/tags/999999", {"name": "x"})[0], 404)

    def test_parse_ingredients_and_units(self):
        status, parsed = self.call("POST", "/api/parse-ingredients", {"text": "500 g Mehl\n2 Eier"})
        self.assertEqual((status, [p["name"] for p in parsed]), (200, ["Mehl", "Eier"]))
        self.assertEqual(self.call("POST", "/api/parse-ingredients", {"text": 5})[1]["field"], "text")
        units = self.call("GET", "/api/units")[1]
        self.assertIn({"unit": "Dose", "plural": "Dosen"}, units)

    def make_job(self, status, draft=None):
        conn = db.connect(self.tmp.name)
        with conn:
            job_id = conn.execute(
                "INSERT INTO import_jobs (url, origin, status, draft, created_at, updated_at) "
                "VALUES ('https://example.com/x', 'single', ?, ?, 'now', 'now')",
                (status, json.dumps(draft) if draft else None)).lastrowid
        conn.close()
        return job_id

    def test_create_imports_validates_and_queues(self):
        status, body = self.call("POST", "/api/imports", {"url": " https://example.com/a "})
        self.assertEqual((status, len(body["ids"])), (201, 1))
        job = self.call("GET", f"/api/imports/{body['ids'][0]}")[1]
        self.assertEqual((job["url"], job["origin"], job["status"], job["draft"]), ("https://example.com/a", "single", "queued", None))
        for bad in ({}, {"url": "ftp://example.com/"}, {"url": 5}, {"url": "javascript:alert(1)"}):
            self.assertEqual(self.call("POST", "/api/imports", bad), (400, {"error": "invalid_field", "field": "url"}))

        status, body = self.call("POST", "/api/imports", {"urls": [
            "https://example.com/1", "bad", 5, "https://example.com/1", "http://example.com/2"]})
        self.assertEqual((status, len(body["ids"])), (201, 2))  # junk dropped, duplicates merged
        origin = self.call("GET", f"/api/imports/{body['ids'][0]}")[1]["origin"]
        self.assertEqual(origin, "bulk")
        for bad in ({"urls": []}, {"urls": ["nope"]}, {"urls": "x"},
                    {"urls": [f"https://example.com/{i}" for i in range(51)]}):
            self.assertEqual(self.call("POST", "/api/imports", bad)[1]["field"], "urls")

    def test_import_list_filter_and_404(self):
        job_id = self.make_job("failed")
        ids = [j["id"] for j in self.call("GET", "/api/imports?status=failed")[1]]
        self.assertIn(job_id, ids)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertNotIn(job_id, [j["id"] for j in self.call("GET", "/api/imports?status=queued,review")[1]])
        self.assertEqual(self.call("GET", "/api/imports?status=bogus")[1]["field"], "status")
        self.assertEqual(self.call("GET", "/api/imports/999999")[0], 404)
        self.assertEqual(self.call("POST", "/api/imports/999999/save", {})[0], 404)

    def test_import_retry_and_discard(self):
        job_id = self.make_job("failed")
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/retry")[1]["status"], "queued")
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/retry")[0], 400)  # queued jobs cannot be retried
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/discard")[1]["status"], "discarded")
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/discard")[0], 400)

    def test_import_save_creates_recipe_once(self):
        draft = {"format_version": 1, "title": "Importiert", "servings": 3, "source_kind": "web",
                 "source_url": "https://example.com/x", "image_url": "https://example.com/i.jpg", "warnings": ["image_failed"]}
        job_id = self.make_job("review", draft)
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/save", {**draft, "title": ""})[1]["field"], "title")
        status, recipe = self.call("POST", f"/api/imports/{job_id}/save", {**draft, "title": "Geprüft"})
        self.assertEqual((status, recipe["title"], recipe["source_kind"]), (201, "Geprüft", "web"))
        job = self.call("GET", f"/api/imports/{job_id}")[1]
        self.assertEqual((job["status"], job["recipe_id"]), ("done", recipe["id"]))
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/save", draft)[0], 400)  # already saved
        self.assertEqual(self.call("POST", f"/api/imports/{self.make_job('queued')}/save", draft)[0], 400)

    def test_create_text_import(self):
        status, body = self.call("POST", "/api/imports", {"text": "  Pfannkuchen\n3 Eier  "})
        self.assertEqual((status, len(body["ids"])), (201, 1))
        job = self.call("GET", f"/api/imports/{body['ids'][0]}")[1]
        self.assertEqual((job["url"], job["origin"], job["status"], job["title"]), (None, "single", "queued", None))
        conn = db.connect(self.tmp.name)
        self.assertEqual(conn.execute("SELECT text FROM import_jobs WHERE id = ?", (job["id"],)).fetchone()[0],
                         "Pfannkuchen\n3 Eier")
        conn.close()
        self.assertEqual(self.call("POST", f"/api/imports/{job['id']}/retry")[0], 400)  # queued
        for bad in ({"text": ""}, {"text": "   \n"}, {"text": 5}, {"text": None}, {"text": "x" * 20001}):
            self.assertEqual(self.call("POST", "/api/imports", bad), (400, {"error": "invalid_field", "field": "text"}))
        self.assertEqual(self.call("POST", "/api/imports", {"text": "x" * 20000})[0], 201)

    def test_pasted_caption_requeues_a_draft_and_keeps_it(self):
        draft = {"format_version": 1, "title": "instagram.com", "servings": 2, "source_kind": "instagram",
                 "source_url": "https://example.com/x", "warnings": ["paste_caption"]}
        job_id = self.make_job("review", draft)
        status, job = self.call("POST", f"/api/imports/{job_id}/text", {"text": " 200 g Mehl "})
        self.assertEqual((status, job["status"]), (200, "queued"))
        conn = db.connect(self.tmp.name)
        row = conn.execute("SELECT text, draft FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        self.assertEqual((row["text"], json.loads(row["draft"])), ("200 g Mehl", draft))  # the worker needs link and image
        conn.close()
        self.assertEqual(self.call("POST", f"/api/imports/{job_id}/text", {"text": "x"})[0], 400)  # queued, not in review
        # a retry starts from the link again, without the pasted caption
        conn = db.connect(self.tmp.name)
        with conn:
            conn.execute("UPDATE import_jobs SET status = 'failed' WHERE id = ?", (job_id,))
        conn.close()
        self.call("POST", f"/api/imports/{job_id}/retry")
        conn = db.connect(self.tmp.name)
        row = conn.execute("SELECT text, draft FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        self.assertEqual((row["text"], row["draft"]), (None, None))

    def test_pasted_caption_validation_and_retry_of_text_jobs(self):
        job_id = self.make_job("review", {"format_version": 1, "title": "x", "servings": 1})
        for bad in ({}, {"text": ""}, {"text": 5}, {"text": "x" * 20001}):
            self.assertEqual(self.call("POST", f"/api/imports/{job_id}/text", bad)[1]["field"], "text")
        self.assertEqual(self.call("POST", "/api/imports/999999/text", {"text": "x"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/imports/{self.make_job('failed')}/text", {"text": "x"})[0], 400)
        # a text-only job keeps its text on retry (it has no link to start from)
        text_id = self.call("POST", "/api/imports", {"text": "Kuchen"})[1]["ids"][0]
        conn = db.connect(self.tmp.name)
        with conn:
            conn.execute("UPDATE import_jobs SET status = 'failed' WHERE id = ?", (text_id,))
        conn.close()
        self.assertEqual(self.call("POST", f"/api/imports/{text_id}/retry")[1]["status"], "queued")
        conn = db.connect(self.tmp.name)
        self.assertEqual(conn.execute("SELECT text FROM import_jobs WHERE id = ?", (text_id,)).fetchone()[0], "Kuchen")
        conn.close()

    def test_images_are_served_only_for_valid_names(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
        name = "ab" * 32 + ".png"
        (Path(self.tmp.name) / "images").mkdir(exist_ok=True)
        (Path(self.tmp.name) / "images" / name).write_bytes(png)
        r, body = self.get("/images/" + name)
        self.assertEqual((r.status, body, r.getheader("Content-Type")), (200, png, "image/png"))
        self.assertIn("immutable", r.getheader("Cache-Control"))
        for bad in ("/images/" + "cd" * 32 + ".png", "/images/../mealprep.db", "/images/abc.png", "/images/" + "AB" * 32 + ".png",
                    "/images/" + "ab" * 32 + ".gif"):
            self.assertEqual(self.get(bad)[0].status, 404, bad)

    def test_entities_requires_valid_domain(self):
        r, body = self.get("/api/ha/entities?domain=light")
        self.assertEqual((r.status, json.loads(body)["field"]), (400, "domain"))

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_health(self):
        r, body = self.get("/api/health")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "version": VERSION})

    def test_me_is_dev_user(self):
        r, body = self.get("/api/me")
        self.assertEqual(r.status, 200)
        user = json.loads(body)
        self.assertEqual(user["id"], "dev")
        self.assertTrue(user["display_name"])

    def test_path_traversal_is_404(self):
        r, _ = self.get("/../mealprep/__init__.py")
        self.assertEqual(r.status, 404)

    def test_unknown_api_is_json_404(self):
        r, body = self.get("/api/nope")
        self.assertEqual(r.status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_index_and_security_headers(self):
        r, body = self.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"<html", body)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")


class ProdIdentityTest(unittest.TestCase):
    def test_missing_header_is_rejected(self):
        self.assertIsNone(server.current_user(headers(), "prod"))

    def test_invalid_id_is_rejected(self):
        self.assertIsNone(server.current_user(headers(X_Remote_User_Id="a b"), "prod"))

    def test_headers_give_user(self):
        h = headers(X_Remote_User_Id="abc123", X_Remote_User_Name="u", X_Remote_User_Display_Name="User One")
        self.assertEqual(
            server.current_user(h, "prod"), {"id": "abc123", "name": "u", "display_name": "User One"}
        )

    def test_ip_allow_list(self):
        self.assertTrue(server.ip_allowed("172.30.32.2", "prod"))
        self.assertFalse(server.ip_allowed("192.168.1.5", "prod"))


if __name__ == "__main__":
    unittest.main()
