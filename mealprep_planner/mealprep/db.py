import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import ha

DEFAULT_TAGS = [
    "Fleisch", "Geflügel", "Fisch", "Vegetarisch", "Vegan", "Nudeln", "Reis", "Kartoffeln", "Hülsenfrüchte",
    "Suppe", "Eintopf", "Salat", "Auflauf", "Pfanne", "Bowl", "Deutsch", "Italienisch", "Asiatisch",
    "Mexikanisch", "Orientalisch", "Indisch", "Schnell", "Leicht", "Low Carb", "Deftig",
]

# Append-only: a shipped migration is never edited. user_version = number of applied scripts.
MIGRATIONS = [
    """
    CREATE TABLE settings (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    CREATE TABLE users (
      id           TEXT PRIMARY KEY,
      name         TEXT NOT NULL,
      display_name TEXT NOT NULL,
      lang         TEXT CHECK (lang IN ('de', 'en')),
      first_seen   TEXT NOT NULL,
      last_seen    TEXT NOT NULL
    );
    """,
    f"""
    CREATE TABLE tags (
      id   INTEGER PRIMARY KEY,
      name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );
    CREATE TABLE recipes (
      id               INTEGER PRIMARY KEY,
      title            TEXT NOT NULL,
      source_url       TEXT,
      source_kind      TEXT NOT NULL CHECK (source_kind IN ('manual','web','tiktok','youtube','instagram','text')),
      image            TEXT,
      servings         INTEGER NOT NULL CHECK (servings BETWEEN 1 AND 50),
      total_minutes    INTEGER CHECK (total_minutes BETWEEN 1 AND 1440),
      for_lunch        INTEGER NOT NULL DEFAULT 1,
      for_dinner       INTEGER NOT NULL DEFAULT 1,
      steps            TEXT NOT NULL DEFAULT '[]',
      kcal REAL, protein_g REAL, fat_g REAL, carbs_g REAL,
      nutrition_source TEXT CHECK (nutrition_source IN ('page','ai','manual')),
      archived         INTEGER NOT NULL DEFAULT 0,
      created_by       TEXT REFERENCES users(id),
      created_at       TEXT NOT NULL,
      updated_at       TEXT NOT NULL,
      CHECK (for_lunch OR for_dinner)
    );
    CREATE TABLE ingredients (
      recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
      pos       INTEGER NOT NULL,
      amount    REAL,
      unit      TEXT,
      name      TEXT NOT NULL,
      note      TEXT,
      PRIMARY KEY (recipe_id, pos)
    );
    CREATE TABLE recipe_tags (
      recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
      tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
      PRIMARY KEY (recipe_id, tag_id)
    );
    INSERT INTO tags (name) VALUES {", ".join(f"('{n}')" for n in DEFAULT_TAGS)};
    """,
    """
    CREATE TABLE import_jobs (
      id         INTEGER PRIMARY KEY,
      url        TEXT,
      text       TEXT,
      origin     TEXT NOT NULL CHECK (origin IN ('single','bulk','inbox')),
      status     TEXT NOT NULL CHECK (status IN ('queued','running','review','failed','done','discarded')),
      error      TEXT,
      draft      TEXT,
      recipe_id  INTEGER REFERENCES recipes(id),
      created_by TEXT REFERENCES users(id),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      CHECK (url IS NOT NULL OR text IS NOT NULL)
    );
    """,
]

DEFAULTS = {"bring_entity": None, "ai_enabled": True, "ai_entity": None, "default_portions": 2}
ENTITY_DOMAIN = {"bring_entity": "todo", "ai_entity": "ai_task"}


class InvalidField(ValueError):
    def __init__(self, field):
        super().__init__(field)
        self.field = field


def connect(data_dir):
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Path(data_dir) / "mealprep.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn):
    done = conn.execute("PRAGMA user_version").fetchone()[0]
    for n, script in enumerate(MIGRATIONS[done:], start=done + 1):
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {n};\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise


def get_settings(conn):
    settings = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in settings:
            settings[row["key"]] = json.loads(row["value"])
    return settings


def _validate(key, value):
    if key == "ai_enabled":
        if not isinstance(value, bool):
            raise InvalidField(key)
    elif key == "default_portions":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 12:
            raise InvalidField(key)
    elif key in ENTITY_DOMAIN:
        if value is None:
            return
        if not isinstance(value, str) or not value.startswith(ENTITY_DOMAIN[key] + "."):
            raise InvalidField(key)
        try:
            ha.get_state(value)
        except ha.HAError as e:
            if e.status == 404:
                raise InvalidField(key) from e
            raise
    else:
        raise InvalidField(key)


def set_settings(conn, patch):
    """Validate the whole patch first, then store it; defaults live in code, so only changes are stored."""
    for key, value in patch.items():
        _validate(key, value)
    with conn:
        for key, value in patch.items():
            if value == DEFAULTS[key]:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
    return get_settings(conn)


def upsert_user(conn, user):
    """Register/refresh the user; returns the stored language override (None = browser language)."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        row = conn.execute(
            "INSERT INTO users (id, name, display_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, display_name = excluded.display_name, "
            "last_seen = excluded.last_seen RETURNING lang",
            (user["id"], user["name"], user["display_name"], now, now),
        ).fetchone()
    return row["lang"]


def set_user_lang(conn, user_id, lang):
    with conn:
        conn.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))
