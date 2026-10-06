import http.client
import json
import threading
import unittest
from pathlib import Path

from mealprep import VERSION, server

STATIC = Path(__file__).resolve().parent.parent / "static"


def headers(**kw):
    h = http.client.HTTPMessage()
    for k, v in kw.items():
        h[k.replace("_", "-")] = v
    return h


class DevServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server("127.0.0.1", 0, STATIC)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

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
