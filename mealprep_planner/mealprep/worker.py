import json
import re
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import db, ha, importer, plans, planner, recipes

INBOX_INTERVAL = 60  # seconds between inbox polls
INBOX_MAX_URLS = 10  # per inbox item
INBOX_MIN_TEXT = 20  # shorter text without a link is not a recipe
INBOX_DEDUPE = timedelta(hours=1)
SENSOR_INTERVAL = 300  # seconds between sensor posts (and right after a plan change)
SENSOR_ENTITY = "sensor.essensplan"
MAX_TEXT = 20000
URL_RE = re.compile(r"""https?://[^\s<>"']+""")
plan_changed = threading.Event()  # set by the API after a plan change; the worker then posts the sensor


def _now():
    return datetime.now().isoformat(timespec="seconds")


def enqueue(conn, urls, origin, user_id):
    """Create one queued job per URL; returns the job ids."""
    now = _now()
    with conn:
        return [conn.execute(
            "INSERT INTO import_jobs (url, origin, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, 'queued', ?, ?, ?)", (url, origin, user_id, now, now)).lastrowid for url in urls]


def enqueue_text(conn, text, origin, user_id):
    """Create one queued job for pasted recipe text; returns its id."""
    now = _now()
    with conn:
        return conn.execute(
            "INSERT INTO import_jobs (text, origin, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, 'queued', ?, ?, ?)", (text, origin, user_id, now, now)).lastrowid


def reset_running(conn):
    """Jobs left `running` by a stopped process go back to the queue."""
    with conn:
        conn.execute("UPDATE import_jobs SET status = 'queued', updated_at = ? WHERE status = 'running'", (_now(),))


def process_one(data_dir):
    """Run the oldest queued job. Returns False if there was none."""
    conn = db.connect(data_dir)
    try:
        job = conn.execute("SELECT * FROM import_jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if job is None:
            return False
        with conn:
            conn.execute("UPDATE import_jobs SET status = 'running', updated_at = ? WHERE id = ?", (_now(), job["id"]))
        try:
            draft, error = importer.build_draft(job, conn, data_dir), None
        except importer.FetchError as e:
            draft, error = None, e.code
        except Exception:  # untrusted pages: whatever goes wrong fails this job, never the worker
            traceback.print_exc()
            draft, error = None, "fetch_failed"
        with conn:  # a job discarded meanwhile is no longer `running` and stays discarded
            conn.execute(
                "UPDATE import_jobs SET status = ?, error = ?, draft = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                ("failed" if error else "review", error, json.dumps(draft) if draft else None, _now(), job["id"]))
        return True
    finally:
        conn.close()


def extract_urls(text):
    """http(s) URLs in a shared text (trailing punctuation stripped, ≤ INBOX_MAX_URLS, no repeats)."""
    urls = (u.rstrip(".,;:!?)") for u in URL_RE.findall(text))
    return list(dict.fromkeys(u for u in urls if recipes.http_url(u)))[:INBOX_MAX_URLS]


def _recent_inbox_job(conn, column, value):
    since = (datetime.now() - INBOX_DEDUPE).isoformat(timespec="seconds")
    return conn.execute(f"SELECT 1 FROM import_jobs WHERE origin = 'inbox' AND {column} = ? AND created_at >= ?",
                        (value, since)).fetchone() is not None


def poll_inbox(data_dir):
    """Turn the open items of the Rezept-Inbox into jobs and remove them; an item without link or text stays."""
    conn = db.connect(data_dir)
    try:
        entity = db.get_settings(conn)["inbox_entity"]
        if not entity:
            return
        try:
            resp = ha.call_service("todo", "get_items", {"entity_id": entity, "status": ["needs_action"]}, True)
            items = resp["service_response"][entity]["items"]
        except ha.HAError as e:
            print(f"inbox poll failed: {e}", flush=True)
            return
        except (KeyError, TypeError):  # untrusted response shape
            print("inbox poll failed: unexpected response", flush=True)
            return
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            summary, description = (item.get(k) if isinstance(item.get(k), str) else "" for k in ("summary", "description"))
            urls = extract_urls(summary + "\n" + description)
            text = (description.strip() or summary.strip())[:MAX_TEXT]
            if urls:
                fresh = [u for u in urls if not _recent_inbox_job(conn, "url", u)]
                if fresh:
                    enqueue(conn, fresh, "inbox", None)
            elif len(text) >= INBOX_MIN_TEXT:
                if not _recent_inbox_job(conn, "text", text):
                    enqueue_text(conn, text, "inbox", None)
            else:
                continue
            ref = item.get("uid") if isinstance(item.get("uid"), str) and item["uid"] else summary
            try:
                ha.call_service("todo", "remove_item", {"entity_id": entity, "item": ref})
            except ha.HAError as e:  # the job exists; the dedupe window covers the next poll
                print(f"inbox remove failed: {e}", flush=True)
    finally:
        conn.close()


def notify_plan_changed():
    plan_changed.set()


def post_sensor(data_dir):
    """Post sensor.essensplan; a failure is logged, never raised."""
    try:
        conn = db.connect(data_dir)
        try:
            now = datetime.now()
            state, attributes = planner.sensor_payload(plans.sensor_slots(conn, now.date()), now)
        finally:
            conn.close()
        ha.post_state(SENSOR_ENTITY, state, attributes)
    except ha.HAError as e:
        print(f"sensor post failed: {e}", flush=True)
    except Exception:
        traceback.print_exc()


def run(data_dir):
    conn = db.connect(data_dir)
    try:
        reset_running(conn)
    finally:
        conn.close()
    next_poll = next_sensor = 0
    while True:
        try:
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + INBOX_INTERVAL
                poll_inbox(data_dir)
            if plan_changed.is_set() or time.monotonic() >= next_sensor:
                plan_changed.clear()
                next_sensor = time.monotonic() + SENSOR_INTERVAL
                post_sensor(data_dir)
            if process_one(data_dir):
                continue
        except Exception:
            traceback.print_exc()
        time.sleep(1)  # idle or error


def start(data_dir):
    thread = threading.Thread(target=run, args=(data_dir,), daemon=True, name="worker")
    thread.start()
    return thread
