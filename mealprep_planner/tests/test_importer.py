import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from mealprep import db, importer, recipes

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
            self.assertEqual(draft["warnings"], ["no_recipe_data"])
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
        self.assertEqual((draft["title"], draft["image"], draft["warnings"]), ("Spaghetti & Sauce", "a" * 64 + ".jpg", []))
        self.assertEqual(draft["tags"], ["Nudeln", "Schnell", "Suppe", "Italienisch"])

    def test_og_image_fills_in_when_jsonld_has_none(self):
        page = ld_page({**RECIPE, "image": {"@id": "https://example.com/r#primaryimage"}},
                       extra_head='<meta property="og:image" content="https://example.com/og.jpg">')
        self.assertEqual(self.build(page)["image_url"], "https://example.com/og.jpg")

    def test_image_failure_warns(self):
        self.assertEqual(self.build(ld_page(RECIPE), image=None)["warnings"], ["image_failed"])

    def test_page_without_recipe_gives_opengraph_prefill(self):
        html = '<title>Titel</title><meta property="og:title" content="OG"><meta property="og:image" content="https://example.com/i.jpg">'
        draft = self.build(html)
        self.assertEqual((draft["title"], draft["image_url"], draft["ingredients"], draft["steps"]),
                         ("OG", "https://example.com/i.jpg", [], []))
        self.assertEqual(draft["warnings"], ["no_recipe_data", "image_failed"])
        self.assertEqual(self.build("<title>Nur Titel</title>")["title"], "Nur Titel")
        self.assertEqual(self.build("<p>leer</p>")["title"], "example.com")

    def test_unusable_recipe_falls_back_to_prefill(self):
        draft = self.build(ld_page({"@type": "Recipe", "name": ""}, extra_head='<meta property="og:title" content="OG">'))
        self.assertEqual((draft["title"], draft["warnings"]), ("OG", ["no_recipe_data"]))

    def test_already_imported_warning(self):
        recipes.create_recipe(self.conn, recipes.validate_draft(
            {"format_version": 1, "title": "Alt", "servings": 2, "source_url": "https://example.com/final"}, [])[0], None)
        draft = self.build(ld_page(RECIPE), url="https://example.com/short", final="https://example.com/final")
        self.assertEqual(draft["warnings"], ["already_imported", "image_failed"])
        self.assertEqual(draft["source_url"], "https://example.com/final")

    def test_default_portions_setting_is_used(self):
        db.set_settings(self.conn, {"default_portions": 5})
        recipe = {k: v for k, v in RECIPE.items() if k != "recipeYield"}
        self.assertEqual(self.build(ld_page(recipe))["servings"], 5)


if __name__ == "__main__":
    unittest.main()
