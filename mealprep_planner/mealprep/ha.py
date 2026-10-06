import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote


class HAError(Exception):
    """code is 'ha_unavailable' (no config / no connection, status None) or 'ha_error' (non-2xx)."""

    def __init__(self, status, code):
        super().__init__(f"{code} ({status})")
        self.status = status
        self.code = code


def _base():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        return "http://supervisor/core/api", token
    url, token = os.environ.get("MEALPREP_HA_URL"), os.environ.get("MEALPREP_HA_TOKEN")
    if not url or not token:
        raise HAError(None, "ha_unavailable")
    return url.rstrip("/") + "/api", token


def request(method, path, body=None, timeout=10):
    base, token = _base()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{base}/{path}", data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise HAError(e.code, "ha_error") from e
    except (urllib.error.URLError, OSError) as e:  # URLError, timeouts, refused connections
        raise HAError(None, "ha_unavailable") from e
    try:
        return json.loads(raw) if raw else None
    except ValueError as e:
        raise HAError(None, "ha_error") from e


def get_config():
    return request("GET", "config")


def get_state(entity_id):
    return request("GET", "states/" + quote(entity_id, safe=""))


def list_entities(domain):
    states = request("GET", "states")
    out = []
    for s in states if isinstance(states, list) else []:
        eid = s.get("entity_id") if isinstance(s, dict) else None
        if isinstance(eid, str) and eid.startswith(domain + "."):
            name = (s.get("attributes") or {}).get("friendly_name")
            out.append({"entity_id": eid, "friendly_name": name if isinstance(name, str) else eid})
    return sorted(out, key=lambda e: e["friendly_name"].lower())


def call_service(domain, service, data, return_response=False, timeout=10):
    path = f"services/{domain}/{service}" + ("?return_response" if return_response else "")
    return request("POST", path, data, timeout)


def post_state(entity_id, state, attributes):
    return request("POST", "states/" + quote(entity_id, safe=""), {"state": state, "attributes": attributes})
