import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from mealprep import db, ha, importer, recipes

TAGS = ["Nudeln", "Italienisch", "Schnell", "Suppe"]


def ld_page(*objs, extra_head=""):
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in objs)
    return f"<html><head><title>Seite</title>{extra_head}{scripts}</head><body>x</body></html>"


RECIPE = {
    "@type": "Recipe",
    "name": "Spaghetti &amp; <b>Sauce</b>",
    "recipeYield": "4 Portionen",
    "totalTime": "PT1H30M",
    "recipeIngredient": ["500 g Spaghetti", "2 Dosen Tomaten, gehackt", "Salz"],
    "recipeInstructions": [{"@type": "HowToStep", "text": "Kochen."}, {"@type": "HowToStep", "text": "Essen."}],
    "image": ["/img/a.jpg", "/img/b.jpg"],
    "keywords": "nudeln, Schnell, unbekannt",
    "recipeCategory": ["Suppe"],
    "recipeCuisine": "italienisch",
    "nutrition": {"calories": "520 kcal", "proteinContent": "25,5 g", "fatContent": "10 g", "carbohydrateContent": "x"},
}


def from_ld(*objs, **kw):
    return importer.recipe_from_jsonld(list(objs), 2, TAGS, **kw)


class CheckUrlTest(unittest.TestCase):
    def blocked(self, url, code="fetch_blocked"):
        with self.assertRaises(importer.FetchError) as cm:
            importer.check_url(url)
        self.assertEqual(cm.exception.code, code, url)

    def test_rejects_bad_urls(self):
        for url in [
            "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "http://user:pw@example.com/",
            "http://@example.com/", "http://example.com:8080/", "http://example.com:22/",
            "http://localhost/", "http://127.0.0.1/", "http://10.0.0.1/", "http://172.16.0.1/", "http://192.168.0.1/",
            "http://169.254.1.1/", "http://[::1]/", "http://[fc00::1]/", "http://[fe80::1]/", "http://[::ffff:10.0.0.1]/",
            "http:///nohost", "not a url",
        ]:
            self.blocked(url)

    def test_hostname_resolving_to_private_ip_is_blocked(self):
        def fake(ip):
            return lambda *a, **k: [(2, 1, 6, "", (ip, 80))]
        with mock.patch("socket.getaddrinfo", fake("10.1.2.3")):
            self.blocked("http://intranet.example/")
        # one private answer among public ones is enough to block
        both = [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("192.168.1.1", 80))]
        with mock.patch("socket.getaddrinfo", lambda *a, **k: both):
            self.blocked("http://mixed.example/")

    def test_public_hostname_is_accepted(self):
        with mock.patch("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]):
            importer.check_url("https://www.example.com/rezepte/1")
            importer.check_url("http://www.example.com/")

    def test_unresolvable_host_is_fetch_failed(self):
        import socket
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror):
            self.blocked("http://nope.example/", "fetch_failed")


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path
        if path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/secret")
            self.end_headers()
        elif path == "/redirect-ok":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        elif path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        elif path == "/big":
            body = b"a" * 5000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/missing":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/img.png":
            self.reply(b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png")
        elif path == "/img.txt":
            self.reply(b"not an image", "image/png")
        else:
            self.server.seen.append(self.headers.get("User-Agent"))
            self.reply(b"<html><body>hallo</body></html>", "text/html; charset=utf-8")

    def reply(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), StubHandler)
        cls.httpd.seen = []
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        # test-only: let the stub's loopback address and port through; redirects to 10.0.0.1 stay blocked
        cls.patches = [
            mock.patch.object(importer, "_is_global", lambda ip: ip == "127.0.0.1"),
            mock.patch.object(importer, "ALLOWED_PORTS", (80, 443, cls.port)),
        ]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def code(self, path, limit=1000):
        with self.assertRaises(importer.FetchError) as cm:
            importer.fetch(self.url(path), limit, "*/*")
        return cm.exception.code

    def test_fetch_ok_sends_honest_user_agent(self):
        final, ctype, body = importer.fetch(self.url("/page"), 1000, "text/html")
        self.assertEqual((final, body), (self.url("/page"), b"<html><body>hallo</body></html>"))
        self.assertIn("text/html", ctype)
        self.assertTrue(self.httpd.seen[-1].startswith("MealPrepPlanner/"))

    def test_redirect_is_followed_and_rechecked(self):
        self.assertEqual(importer.fetch(self.url("/redirect-ok"), 1000, "*/*")[0], self.url("/page"))
        self.assertEqual(self.code("/redirect-private"), "fetch_blocked")

    def test_redirect_loop_and_http_error_fail(self):
        self.assertEqual(self.code("/loop"), "fetch_failed")
        self.assertEqual(self.code("/missing"), "fetch_failed")

    def test_body_over_limit_is_rejected(self):
        self.assertEqual(self.code("/big", 4999), "fetch_too_large")
        self.assertEqual(len(importer.fetch(self.url("/big"), 5000, "*/*")[2]), 5000)

    def test_download_image_checks_magic_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = importer.download_image(self.url("/img.png"), tmp)
            self.assertRegex(name, r"^[0-9a-f]{64}\.png$")
            self.assertTrue((Path(tmp) / "images" / name).is_file())
            self.assertEqual(importer.download_image(self.url("/img.png"), tmp), name)  # same content, same file
            self.assertIsNone(importer.download_image(self.url("/img.txt"), tmp))
            self.assertIsNone(importer.download_image(self.url("/missing"), tmp))

    def test_build_draft_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(tmp)
            db.migrate(conn)
            draft = importer.build_draft({"url": self.url("/page")}, conn, tmp)
            self.assertEqual(draft["warnings"], ["no_recipe_data", "ai_disabled"])
            self.assertEqual(draft["title"], "127.0.0.1")
            conn.close()


class ImageExtTest(unittest.TestCase):
    def test_magic_bytes(self):
        self.assertEqual(importer.image_ext(b"\xff\xd8\xff\xe0abc"), "jpg")
        self.assertEqual(importer.image_ext(b"\x89PNG\r\n"), "png")
        self.assertEqual(importer.image_ext(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        for bad in (b"", b"GIF89a", b"<svg", b"RIFF\x00\x00\x00\x00WAVE", b"\xff\xd8"):
            self.assertIsNone(importer.image_ext(bad))


class JsonLdTest(unittest.TestCase):
    def test_plain_recipe(self):
        d = from_ld(RECIPE, base_url="https://example.com/rezepte/1")
        self.assertEqual(d["title"], "Spaghetti & Sauce")  # entities decoded, tags stripped
        self.assertEqual((d["servings"], d["total_minutes"]), (4, 90))
        self.assertEqual(d["ingredients"][1], {"amount": 2, "unit": "Dose", "name": "Tomaten", "note": "gehackt"})
        self.assertEqual(d["steps"], ["Kochen.", "Essen."])
        self.assertEqual(d["image_url"], "https://example.com/img/a.jpg")
        self.assertEqual(d["tags"], ["Nudeln", "Schnell", "Suppe", "Italienisch"])
        self.assertEqual(d["nutrition"], {"kcal": 520.0, "protein_g": 25.5, "fat_g": 10.0, "carbs_g": None, "source": "page"})

    def test_graph_list_and_type_list(self):
        for objs in ([{"@graph": [{"@type": "WebSite"}, RECIPE]}], [[{"@type": "Thing"}, RECIPE]],
                     [{**RECIPE, "@type": ["Recipe", "Article"]}], [{**RECIPE, "@type": "http://schema.org/Recipe"}]):
            self.assertEqual(importer.recipe_from_jsonld(objs, 2, TAGS)["servings"], 4)
        self.assertIsNone(importer.recipe_from_jsonld([{"@type": "WebSite"}, "x", 5, None], 2, TAGS))

    def test_instruction_shapes(self):
        cases = [
            ("Eins\nZwei\r\n\nDrei", ["Eins", "Zwei", "Drei"]),
            ("Eins<br>Zwei<br/>Drei", ["Eins", "Zwei", "Drei"]),
            (["Eins", "Zwei"], ["Eins", "Zwei"]),
            ([{"@type": "HowToSection", "name": "Teig", "itemListElement": [
                {"@type": "HowToStep", "text": "Eins"}, {"@type": "HowToStep", "text": "Zwei"}]},
              {"@type": "HowToStep", "text": "Drei"}], ["Eins", "Zwei", "Drei"]),
            ([{"@type": "HowToStep", "name": "Nur Name"}], ["Nur Name"]),
            (None, []),
        ]
        for value, expected in cases:
            self.assertEqual(from_ld({"@type": "Recipe", "name": "x", "recipeInstructions": value})["steps"], expected)

    def test_image_shapes(self):
        base = "https://example.com/p/"
        cases = [("a.jpg", "https://example.com/p/a.jpg"), (["https://cdn.example.com/x.png", "y"], "https://cdn.example.com/x.png"),
                 ({"@type": "ImageObject", "url": "https://cdn.example.com/o.jpg"}, "https://cdn.example.com/o.jpg"),
                 ([{"@type": "ImageObject", "url": "/o.jpg"}], "https://example.com/o.jpg"),
                 ("javascript:alert(1)", None), (None, None), ([], None), ({"url": 5}, None)]
        for value, expected in cases:
            self.assertEqual(from_ld({"@type": "Recipe", "name": "x", "image": value}, base_url=base)["image_url"], expected, value)

    def test_yield_and_durations(self):
        def d(**kw):
            return from_ld({"@type": "Recipe", "name": "x", **kw})
        for value, expected in [("4 Portionen", 4), (6, 6), (["8", "8 servings"], 8), ("ergibt 12 Stück", 12),
                                ("viele", 2), (None, 2), (0, 2), (99, 2)]:
            self.assertEqual(d(recipeYield=value)["servings"], expected, value)
        for value, expected in [({"totalTime": "PT45M"}, 45), ({"totalTime": "PT1H30M"}, 90), ({"totalTime": "P0DT2H"}, 120),
                                ({"prepTime": "PT10M", "cookTime": "PT20M"}, 30), ({"totalTime": "PT0S"}, None),
                                ({"totalTime": "P2D"}, None), ({"totalTime": "irgendwann"}, None), ({}, None)]:
            self.assertEqual(d(**value)["total_minutes"], expected, value)

    def test_hostile_values_stay_valid(self):
        hostile = {"@type": "Recipe", "name": "x" * 500, "recipeIngredient": ["1 " + "a" * 300, "999999999 g Mehl", "   ", 5] * 80,
                   "recipeInstructions": ["s" * 5000] * 80, "nutrition": {"calories": "99999 kcal"}, "keywords": [1, None, {}]}
        draft, errors = recipes.validate_draft(
            {"format_version": 1, "source_kind": "web", **from_ld(hostile)}, TAGS)
        self.assertEqual(errors, {})
        self.assertEqual((len(draft["title"]), len(draft["ingredients"]), len(draft["steps"]), draft["nutrition"]),
                         (200, 100, 50, None))


class ExtractTest(unittest.TestCase):
    def test_jsonld_and_opengraph(self):
        head = ('<meta property="og:title" content="OG Titel"><meta name="og:image" content="/bild.jpg">'
                '<meta property="og:description" content="Beschreibung">')
        page = ld_page(RECIPE, extra_head=head) + '<script type="application/ld+json">{kaputt</script>'
        got = importer.extract(page, "https://example.com/a/b")
        self.assertEqual(got["jsonld"], [RECIPE])  # the broken block is skipped
        self.assertEqual((got["og_title"], got["og_image"], got["og_description"], got["title"]),
                         ("OG Titel", "https://example.com/bild.jpg", "Beschreibung", "Seite"))

    def test_missing_everything(self):
        got = importer.extract("<html><<<", "https://example.com/")
        self.assertEqual(got, {"jsonld": [], "og_title": "", "og_image": None, "og_description": "", "title": ""})

    def test_invalid_og_image_is_dropped(self):
        got = importer.extract('<meta property="og:image" content="javascript:x">', "https://example.com/")
        self.assertIsNone(got["og_image"])


class BuildDraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def build(self, html, url="https://example.com/r", final=None, image=None):
        with mock.patch.object(importer, "fetch", return_value=(final or url, "text/html; charset=utf-8", html.encode())), \
             mock.patch.object(importer, "download_image", return_value=image):
            return importer.build_draft({"url": url}, self.conn, self.tmp.name)

    def test_jsonld_draft_is_valid(self):
        draft = self.build(ld_page(RECIPE), image="a" * 64 + ".jpg")
        self.assertEqual(draft["source_url"], "https://example.com/r")
        self.assertEqual((draft["title"], draft["image"], draft["warnings"]), ("Spaghetti & Sauce", "a" * 64 + ".jpg", ["ai_disabled"]))
        self.assertEqual(draft["tags"], ["Nudeln", "Schnell", "Suppe", "Italienisch"])

    def test_og_image_fills_in_when_jsonld_has_none(self):
        page = ld_page({**RECIPE, "image": {"@id": "https://example.com/r#primaryimage"}},
                       extra_head='<meta property="og:image" content="https://example.com/og.jpg">')
        self.assertEqual(self.build(page)["image_url"], "https://example.com/og.jpg")

    def test_image_failure_warns(self):
        self.assertEqual(self.build(ld_page(RECIPE), image=None)["warnings"], ["ai_disabled", "image_failed"])

    def test_page_without_recipe_gives_opengraph_prefill(self):
        html = '<title>Titel</title><meta property="og:title" content="OG"><meta property="og:image" content="https://example.com/i.jpg">'
        draft = self.build(html)
        self.assertEqual((draft["title"], draft["image_url"], draft["ingredients"], draft["steps"]),
                         ("OG", "https://example.com/i.jpg", [], []))
        self.assertEqual(draft["warnings"], ["no_recipe_data", "ai_disabled", "image_failed"])
        self.assertEqual(self.build("<title>Nur Titel</title>")["title"], "Nur Titel")
        self.assertEqual(self.build("<p>leer</p>")["title"], "example.com")

    def test_unusable_recipe_falls_back_to_prefill(self):
        draft = self.build(ld_page({"@type": "Recipe", "name": ""}, extra_head='<meta property="og:title" content="OG">'))
        self.assertEqual((draft["title"], draft["warnings"]), ("OG", ["no_recipe_data", "ai_disabled"]))

    def test_already_imported_warning(self):
        recipes.create_recipe(self.conn, recipes.validate_draft(
            {"format_version": 1, "title": "Alt", "servings": 2, "source_url": "https://example.com/final"}, [])[0], None)
        draft = self.build(ld_page(RECIPE), url="https://example.com/short", final="https://example.com/final")
        self.assertEqual(draft["warnings"], ["already_imported", "ai_disabled", "image_failed"])
        self.assertEqual(draft["source_url"], "https://example.com/final")

    def test_default_portions_setting_is_used(self):
        db.set_settings(self.conn, {"default_portions": 5})
        recipe = {k: v for k, v in RECIPE.items() if k != "recipeYield"}
        self.assertEqual(self.build(ld_page(recipe))["servings"], 5)



class SourceKindTest(unittest.TestCase):
    def test_hosts(self):
        cases = {
            "https://www.tiktok.com/@a/video/1": "tiktok", "https://tiktok.com/@a/video/1": "tiktok",
            "https://vm.tiktok.com/ZMabc/": "tiktok", "https://m.tiktok.com/v/1": "tiktok",
            "https://www.youtube.com/watch?v=x": "youtube", "https://youtube.com/shorts/x": "youtube",
            "https://m.youtube.com/watch?v=x": "youtube", "https://youtu.be/x": "youtube",
            "https://www.instagram.com/reel/x/": "instagram", "https://instagram.com/p/x/": "instagram",
            "https://www.chefkoch.de/rezepte/1": "web", "https://example.com/": "web",
            "https://nottiktok.com/x": "web", "https://tiktok.com.evil.example/x": "web",
            "https://example.com/?u=https://youtube.com/": "web",
        }
        for url, kind in cases.items():
            self.assertEqual(importer.source_kind(url), kind, url)


class OEmbedTest(unittest.TestCase):
    def oembed(self, kind, payload, url="https://www.tiktok.com/@a/video/1?x=1&y=2"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with mock.patch.object(importer, "fetch", return_value=(url, "application/json", body)) as fetch:
            return importer.oembed(url, kind), fetch

    def test_tiktok_and_youtube_endpoints(self):
        _, fetch = self.oembed("tiktok", {})
        self.assertEqual(fetch.call_args.args[0],
                         "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40a%2Fvideo%2F1%3Fx%3D1%26y%3D2")
        self.assertEqual(fetch.call_args.args[1], 256 * 1024)
        _, fetch = self.oembed("youtube", {}, "https://youtu.be/abc")
        self.assertEqual(fetch.call_args.args[0], "https://www.youtube.com/oembed?url=https%3A%2F%2Fyoutu.be%2Fabc&format=json")

    def test_fields(self):
        info, _ = self.oembed("tiktok", {"title": "Pasta  \n 200 g Nudeln\n\n\n2 Eier ", "author_name": " Koch  Max ",
                                         "thumbnail_url": "https://cdn.example.com/t.jpg", "html": "<iframe>"})
        self.assertEqual(info, {"caption": "Pasta\n200 g Nudeln\n2 Eier", "author": "Koch Max",
                                "thumbnail": "https://cdn.example.com/t.jpg"})

    def test_missing_and_bad_fields(self):
        info, _ = self.oembed("youtube", {})
        self.assertEqual(info, {"caption": "", "author": "", "thumbnail": None})
        info, _ = self.oembed("youtube", {"title": 5, "author_name": [], "thumbnail_url": "javascript:alert(1)"})
        self.assertEqual(info, {"caption": "", "author": "", "thumbnail": None})
        info, _ = self.oembed("tiktok", {"title": "t" * 6000, "author_name": "a" * 300})
        self.assertEqual((len(info["caption"]), len(info["author"])), (5000, 200))

    def test_non_object_json_is_a_fetch_failure(self):
        for payload in (b"not json", b"[1]", b'"x"', b"null"):
            with self.assertRaises(importer.FetchError) as cm:
                self.oembed("tiktok", payload)
            self.assertEqual(cm.exception.code, "fetch_failed")


class PageTextTest(unittest.TestCase):
    def test_drops_scripts_and_styles(self):
        html = ("<html><head><style>p {color: red}</style><script>var x = 'geheim';</script></head><body>"
                "<h1>Titel</h1><noscript>Bitte JS aktivieren</noscript><p>Erster   Absatz\n mit &amp; Umbruch</p>"
                "<script type='application/ld+json'>{\"name\": \"json\"}</script><p>Ende</p></body></html>")
        self.assertEqual(importer.page_text(html), "Titel Erster Absatz mit & Umbruch Ende")

    def test_limit_and_garbage(self):
        self.assertEqual(len(importer.page_text("<p>" + "wort " * 10000 + "</p>")), 20000)
        self.assertIsInstance(importer.page_text("<<< <p"), str)  # broken markup does not raise
        self.assertEqual(importer.page_text("<script>nur script"), "")
        self.assertEqual(importer.page_text(""), "")


class CaptionFieldsTest(unittest.TestCase):
    def test_lines_with_an_amount_are_ingredients(self):
        text = "Schnelle Pasta 🍝\n\n200 g Nudeln\n- 2 Zwiebeln\n1. Zwiebeln schneiden\nSalz\n½ TL Pfeffer"
        got = importer._caption_fields(text)
        self.assertEqual(got["title"], "Schnelle Pasta 🍝")
        self.assertEqual([(i["amount"], i["name"]) for i in got["ingredients"]],
                         [(200, "Nudeln"), (2, "Zwiebeln"), (0.5, "Pfeffer")])

    def test_title_is_the_first_line_without_an_amount(self):
        got = importer._caption_fields("200 g Mehl\n2 Eier\nPfannkuchen")
        self.assertEqual((got["title"], len(got["ingredients"])), ("Pfannkuchen", 2))
        self.assertEqual(importer._caption_fields("200 g Mehl")["title"], "200 g Mehl")  # nothing else to use
        self.assertEqual(len(importer._caption_fields("x" * 300)["title"]), 200)


class BuildDraftAiTest(unittest.TestCase):
    CAPTION = "Pasta Pomodoro\n200 g Spaghetti\n1 Dose Tomaten\nSalz"
    OEMBED = {"title": CAPTION, "author_name": "Koch", "thumbnail_url": "https://cdn.example.com/t.jpg"}
    AI = {
        "title": "Spaghetti Pomodoro", "servings": 2, "total_minutes": 20, "for_lunch": True, "for_dinner": True,
        "tags": ["Nudeln"], "steps": [], "nutrition": {"kcal": 600, "protein_g": 20, "fat_g": 10, "carbs_g": 100},
        "ingredients": [{"amount": 200, "unit": "g", "name": "Spaghetti", "note": None}],
    }
    IMAGE = "c" * 64 + ".jpg"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        self.set("ai_entity", "ai_task.test")
        self.calls = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def set(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def ai_reply(self, data):
        """Stub ha.call_service; data is the AI's answer (dict -> fenced JSON, str -> raw) or an exception."""
        def call(domain, service, payload, return_response=False, timeout=10):
            self.calls.append(payload["instructions"])
            if isinstance(data, Exception):
                raise data
            text = "```json\n" + json.dumps(data) + "\n```" if isinstance(data, dict) else data
            return {"service_response": {"data": text}}
        return mock.patch.object(ha, "call_service", call)

    def build(self, job, pages=None, image=IMAGE):
        """pages: url prefix -> (final_url, content type, body bytes); every other fetch fails the test."""
        self.fetched = []

        def fake_fetch(url, max_bytes, accept):
            self.fetched.append(url)
            for key, value in (pages or {}).items():
                if url.startswith(key):
                    return value
            raise AssertionError("unexpected fetch " + url)

        with mock.patch.object(importer, "fetch", fake_fetch), \
             mock.patch.object(importer, "download_image", return_value=image) as dl:
            self.download = dl
            return importer.build_draft(job, self.conn, self.tmp.name)

    def tiktok_pages(self, oembed=None, final="https://www.tiktok.com/@koch/video/1"):
        return {"https://www.tiktok.com/oembed": (final, "application/json", json.dumps(oembed or self.OEMBED).encode()),
                "https://vm.tiktok.com/": (final, "text/html", b"<html></html>")}

    def test_tiktok_with_ai(self):
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://www.tiktok.com/@koch/video/1"}, self.tiktok_pages())
        self.assertEqual((d["title"], d["source_kind"], d["source_url"]),
                         ("Spaghetti Pomodoro", "tiktok", "https://www.tiktok.com/@koch/video/1"))
        self.assertEqual((d["image"], d["image_url"], d["tags"]), (self.IMAGE, "https://cdn.example.com/t.jpg", ["Nudeln"]))
        self.assertEqual(d["nutrition"]["source"], "ai")
        self.assertEqual(d["warnings"], [])
        self.assertIn("1 Dose Tomaten", self.calls[0])  # the caption is what the AI reads
        self.assertEqual(len(self.fetched), 1)  # oEmbed only

    def test_tiktok_without_ai_uses_the_caption_lines(self):
        for enabled, entity in ((False, "ai_task.test"), (True, None)):  # switched off / no entity chosen
            self.set("ai_enabled", enabled)
            self.set("ai_entity", entity)
            d = self.build({"url": "https://www.tiktok.com/@koch/video/1"}, self.tiktok_pages())
            self.assertEqual((d["title"], d["warnings"]), ("Pasta Pomodoro", ["ai_disabled"]))
            self.assertEqual([i["name"] for i in d["ingredients"]], ["Spaghetti", "Tomaten"])
            self.assertEqual(d["image"], self.IMAGE)
        self.assertEqual(self.calls, [])

    def test_ai_failure_keeps_the_rule_based_draft(self):
        for failure in (ha.HAError(None, "ha_unavailable"), "kein JSON", {"title": ""}):
            with self.ai_reply(failure):
                d = self.build({"url": "https://www.tiktok.com/@koch/video/1"}, self.tiktok_pages())
            self.assertEqual((d["title"], d["warnings"], len(d["ingredients"])), ("Pasta Pomodoro", ["ai_failed"], 2))

    def test_youtube(self):
        info = {"title": "Cremige Suppe in 10 Minuten", "author_name": "Koch", "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg"}
        pages = {"https://www.youtube.com/oembed": ("u", "application/json", json.dumps(info).encode())}
        self.set("ai_enabled", False)
        d = self.build({"url": "https://www.youtube.com/watch?v=x"}, pages)
        self.assertEqual((d["title"], d["source_kind"], d["ingredients"]), ("Cremige Suppe in 10 Minuten", "youtube", []))
        self.assertEqual(d["image"], self.IMAGE)

    def test_video_without_caption_asks_for_one(self):
        pages = self.tiktok_pages({"title": "", "thumbnail_url": "https://cdn.example.com/t.jpg"})
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://www.tiktok.com/@koch/video/1"}, pages)
        self.assertEqual((d["title"], d["warnings"], self.calls), ("www.tiktok.com", ["paste_caption"], []))

    def test_short_link_is_resolved_first(self):
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://vm.tiktok.com/ZMabc/"}, self.tiktok_pages())
        self.assertEqual(d["source_url"], "https://www.tiktok.com/@koch/video/1")
        self.assertEqual(self.fetched[0], "https://vm.tiktok.com/ZMabc/")
        self.assertIn("oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40koch%2Fvideo%2F1", self.fetched[1])

    def test_instagram_is_not_fetched(self):
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://www.instagram.com/reel/abc/"})
        self.assertEqual((self.fetched, self.calls), ([], []))
        self.assertEqual((d["source_kind"], d["source_url"], d["warnings"], d["ingredients"]),
                         ("instagram", "https://www.instagram.com/reel/abc/", ["paste_caption"], []))
        self.assertIsNone(d["image"])

    def test_already_imported_applies_to_links_too(self):
        recipes.create_recipe(self.conn, recipes.validate_draft(
            {"format_version": 1, "title": "Alt", "servings": 2, "source_url": "https://www.instagram.com/reel/abc/"}, [])[0], None)
        d = self.build({"url": "https://www.instagram.com/reel/abc/"})
        self.assertEqual(d["warnings"], ["already_imported", "paste_caption"])

    def test_pasted_text_job(self):
        with self.ai_reply(self.AI):
            d = self.build({"url": None, "text": "Pasta\n200 g Spaghetti"})
        self.assertEqual((d["source_kind"], d["source_url"], d["title"], d["image"], d["warnings"]),
                         ("text", None, "Spaghetti Pomodoro", None, []))
        self.assertEqual(self.fetched, [])
        self.download.assert_not_called()

    def test_pasted_text_job_without_ai(self):
        self.set("ai_enabled", False)
        d = self.build({"url": None, "text": "Eierkuchen\n3 Eier\n250 g Mehl"})
        self.assertEqual((d["title"], d["warnings"], [i["name"] for i in d["ingredients"]]),
                         ("Eierkuchen", ["ai_disabled"], ["Eier", "Mehl"]))
        self.assertEqual(d["servings"], 2)  # default portions

    def test_caption_pasted_onto_a_draft_keeps_link_and_image(self):
        prior = {"format_version": 1, "title": "instagram.com", "source_url": "https://www.instagram.com/reel/abc/",
                 "source_kind": "instagram", "image": self.IMAGE, "image_url": "https://cdn.example.com/t.jpg",
                 "servings": 2, "warnings": ["already_imported", "paste_caption", "image_failed"]}
        job = {"url": prior["source_url"], "text": "Pasta\n200 g Spaghetti", "draft": json.dumps(prior)}
        with self.ai_reply(self.AI):
            d = self.build(job)
        self.assertEqual((d["source_url"], d["source_kind"], d["image"], d["title"]),
                         (prior["source_url"], "instagram", self.IMAGE, "Spaghetti Pomodoro"))
        self.assertEqual(d["warnings"], ["already_imported", "image_failed"])  # paste_caption no longer applies
        self.assertEqual(self.fetched, [])
        self.download.assert_not_called()

    def test_caption_pasted_with_ai_off_still_clears_paste_caption(self):
        self.set("ai_enabled", False)
        prior = {"format_version": 1, "title": "x", "source_url": "https://www.instagram.com/reel/abc/",
                 "source_kind": "instagram", "image": None, "servings": 2, "warnings": ["paste_caption"]}
        d = self.build({"url": prior["source_url"], "text": "Kuchen\n3 Eier", "draft": json.dumps(prior)})
        self.assertEqual((d["warnings"], [i["name"] for i in d["ingredients"]]), (["ai_disabled"], ["Eier"]))

    def test_web_with_jsonld_is_enriched(self):
        recipes.create_recipe(self.conn, recipes.validate_draft({
            "format_version": 1, "title": "Alt", "servings": 2,
            "ingredients": [{"amount": 1, "unit": None, "name": "Zwiebeln", "note": None}]}, [])[0], None)
        page = {**RECIPE, "nutrition": {"calories": "520 kcal"}, "recipeIngredient": ["1 Zwiebel", "Salz und Pfeffer"]}
        answer = {**self.AI, "ingredients": [{"amount": 1, "unit": None, "name": "Zwiebeln", "note": None},
                                              {"amount": None, "unit": None, "name": "Salz", "note": None},
                                              {"amount": None, "unit": None, "name": "Pfeffer", "note": None}]}
        pages = {"https://example.com/": ("https://example.com/r", "text/html", ld_page(page).encode())}
        with self.ai_reply(answer):
            d = self.build({"url": "https://example.com/r"}, pages)
        self.assertEqual([i["name"] for i in d["ingredients"]], ["Zwiebeln", "Salz", "Pfeffer"])
        self.assertEqual((d["title"], d["servings"]), ("Spaghetti & Sauce", 4))  # rule-based fields stay
        self.assertEqual(d["nutrition"]["source"], "page")  # an AI estimate never replaces page nutrition
        self.assertEqual(d["nutrition"]["kcal"], 520.0)
        self.assertEqual(d["warnings"], [])
        self.assertIn("Zwiebeln", self.calls[0])  # known names reach the prompt
        self.assertIn("1 Zwiebel", self.calls[0])

    def test_web_with_jsonld_estimates_missing_nutrition(self):
        page = {k: v for k, v in RECIPE.items() if k != "nutrition"}
        pages = {"https://example.com/": ("https://example.com/r", "text/html", ld_page(page).encode())}
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://example.com/r"}, pages)
        self.assertEqual(d["nutrition"], {"kcal": 600, "protein_g": 20, "fat_g": 10, "carbs_g": 100, "source": "ai"})

    def test_web_with_jsonld_survives_ai_failure(self):
        pages = {"https://example.com/": ("https://example.com/r", "text/html", ld_page(RECIPE).encode())}
        with self.ai_reply("Entschuldigung, das kann ich nicht."):
            d = self.build({"url": "https://example.com/r"}, pages)
        self.assertEqual((d["title"], d["warnings"]), ("Spaghetti & Sauce", ["ai_failed"]))
        self.assertEqual(d["ingredients"][0]["name"], "Spaghetti")

    def test_web_without_jsonld_uses_the_page_text(self):
        html = "<html><head><title>Omas Kuchen</title><script>var geheim=1</script></head><body><p>Backe 250 g Mehl und 3 Eier.</p></body></html>"
        pages = {"https://example.com/": ("https://example.com/k", "text/html", html.encode())}
        with self.ai_reply(self.AI):
            d = self.build({"url": "https://example.com/k"}, pages)
        self.assertEqual((d["title"], d["warnings"], d["source_kind"]), ("Spaghetti Pomodoro", [], "web"))  # no_recipe_data is resolved
        self.assertIn("Backe 250 g Mehl und 3 Eier.", self.calls[0])
        self.assertNotIn("geheim", self.calls[0])

    def test_web_without_jsonld_and_failing_ai_gives_the_prefill(self):
        html = '<html><head><meta property="og:title" content="OG Kuchen"></head><body><p>Text</p></body></html>'
        pages = {"https://example.com/": ("https://example.com/k", "text/html", html.encode())}
        with self.ai_reply(ha.HAError(500, "ha_error")):
            d = self.build({"url": "https://example.com/k"}, pages)
        self.assertEqual((d["title"], d["warnings"]), ("OG Kuchen", ["no_recipe_data", "ai_failed"]))

    def test_known_names_are_most_used_first(self):
        def recipe(*names):
            return {"format_version": 1, "title": "R", "servings": 1,
                    "ingredients": [{"amount": None, "unit": None, "name": n, "note": None} for n in names]}
        for names in (("Salz", "Zwiebeln"), ("zwiebeln", "Mehl"), ("Zwiebeln",)):
            recipes.create_recipe(self.conn, recipes.validate_draft(recipe(*names), [])[0], None)
        self.assertEqual(recipes.top_ingredient_names(self.conn, 10)[0].casefold(), "zwiebeln")
        self.assertEqual(recipes.top_ingredient_names(self.conn, 10)[1:], ["Mehl", "Salz"])
        self.assertEqual(len(recipes.top_ingredient_names(self.conn, 2)), 2)


if __name__ == "__main__":
    unittest.main()
