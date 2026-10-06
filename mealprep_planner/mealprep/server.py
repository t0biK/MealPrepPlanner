import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import VERSION

INGRESS_IP = "172.30.32.2"
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_NAME = 100

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


ROUTES = [
    ("GET", re.compile(r"^/api/health$"), api_health),
    ("GET", re.compile(r"^/api/me$"), api_me),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "MealPrepPlanner/" + VERSION
    user = None

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

    def dispatch(self, method):
        mode = self.server.mode
        if not ip_allowed(self.client_address[0], mode):
            return self.send_error(403, "forbidden")
        path = urlsplit(self.path).path
        self.user = current_user(self.headers, mode)
        if self.user is None:
            return self.send_error(403, "forbidden")
        for m, rx, fn in ROUTES:
            match = rx.match(path)
            if m == method and match:
                return fn(self, match)
        if path.startswith("/api/") or method != "GET":
            return self.send_error(404, "not_found")
        self.send_static(path)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def make_server(host, port, static_dir, mode="dev"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.static_dir = Path(static_dir).resolve()
    httpd.mode = mode
    return httpd
