import json
import threading
import time
import traceback
from datetime import datetime

from . import db, importer


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


def run(data_dir):
    conn = db.connect(data_dir)
    try:
        reset_running(conn)
    finally:
        conn.close()
    while True:
        try:
            if process_one(data_dir):
                continue
        except Exception:
            traceback.print_exc()
        time.sleep(1)  # idle or error


def start(data_dir):
    thread = threading.Thread(target=run, args=(data_dir,), daemon=True, name="worker")
    thread.start()
    return thread
