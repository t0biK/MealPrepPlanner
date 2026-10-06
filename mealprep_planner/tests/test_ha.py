import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from mealprep import ha

SEEN = []


class Stub(BaseHTTPRequestHandler):
    def reply(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        SEEN.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/api/states":
            self.reply(200, [
                {"entity_id": "todo.b", "attributes": {"friendly_name": "B list"}},
                {"entity_id": "todo.a", "attributes": {}},
                {"entity_id": "light.x", "attributes": {"friendly_name": "Lamp"}},
            ])
        elif self.path == "/api/missing":
            self.reply(404)
        else:
            self.reply(200, {"ok": True})

    def do_POST(self):
        SEEN.append((self.command, self.path, self.headers.get("Authorization")))
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.reply(200, {})

    def log_message(self, *a):
        pass


class HATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        env = {"MEALPREP_HA_URL": f"http://127.0.0.1:{cls.httpd.server_address[1]}", "MEALPREP_HA_TOKEN": "tok"}
        cls.patch = mock.patch.dict(os.environ, env)
        cls.patch.start()
        os.environ.pop("SUPERVISOR_TOKEN", None)

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        SEEN.clear()

    def test_auth_header_sent(self):
        ha.get_config()
        self.assertEqual(SEEN[0][2], "Bearer tok")

    def test_return_response_only_when_requested(self):
        ha.call_service("todo", "get_items", {})
        ha.call_service("todo", "get_items", {}, return_response=True)
        self.assertEqual(SEEN[0][1], "/api/services/todo/get_items")
        self.assertEqual(SEEN[1][1], "/api/services/todo/get_items?return_response")

    def test_non_2xx_is_ha_error_with_status(self):
        with self.assertRaises(ha.HAError) as cm:
            ha.request("GET", "missing")
        self.assertEqual((cm.exception.status, cm.exception.code), (404, "ha_error"))

    def test_connection_refused_is_unavailable(self):
        with mock.patch.dict(os.environ, {"MEALPREP_HA_URL": "http://127.0.0.1:1"}):
            with self.assertRaises(ha.HAError) as cm:
                ha.get_config()
        self.assertEqual(cm.exception.code, "ha_unavailable")

    def test_missing_config_is_unavailable(self):
        with mock.patch.dict(os.environ, {"MEALPREP_HA_TOKEN": ""}):
            with self.assertRaises(ha.HAError) as cm:
                ha.get_config()
        self.assertEqual(cm.exception.code, "ha_unavailable")

    def test_list_entities_filters_and_sorts(self):
        self.assertEqual(
            ha.list_entities("todo"),
            [{"entity_id": "todo.b", "friendly_name": "B list"}, {"entity_id": "todo.a", "friendly_name": "todo.a"}],
        )


if __name__ == "__main__":
    unittest.main()
