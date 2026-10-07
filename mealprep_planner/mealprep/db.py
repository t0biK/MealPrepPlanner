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
DEFAULT_PANTRY = [
    "Salz", "Pfeffer", "Zucker", "Mehl", "Wasser", "Öl", "Olivenöl", "Rapsöl", "Sonnenblumenöl", "Essig", "Paprikapulver",
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
    """
    CREATE TABLE ratings (
      user_id    TEXT NOT NULL REFERENCES users(id),
      recipe_id  INTEGER NOT NULL REFERENCES recipes(id),
      stars      INTEGER NOT NULL CHECK (stars BETWEEN 0 AND 5),
      updated_at TEXT NOT NULL,
      PRIMARY KEY (user_id, recipe_id)
    );
    """,
    """
    CREATE TABLE plans (
      week         TEXT PRIMARY KEY,
      status       TEXT NOT NULL CHECK (status IN ('draft','confirmed')),
      confirmed_at TEXT
    );
    CREATE TABLE plan_slots (
      week      TEXT NOT NULL REFERENCES plans(week),
      day       INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
      meal      TEXT NOT NULL CHECK (meal IN ('lunch','dinner')),
      active    INTEGER NOT NULL,
      recipe_id INTEGER REFERENCES recipes(id),
      portions  INTEGER NOT NULL CHECK (portions BETWEEN 1 AND 12),
      locked    INTEGER NOT NULL DEFAULT 0,
      skipped   INTEGER NOT NULL DEFAULT 0,
      reason    TEXT,
      PRIMARY KEY (week, day, meal)
    );
    """,
    f"""
    CREATE TABLE pantry (
      name TEXT PRIMARY KEY COLLATE NOCASE
    );
    INSERT INTO pantry (name) VALUES {", ".join(f"('{n}')" for n in DEFAULT_PANTRY)};
    CREATE TABLE pushed_items (
      week      TEXT NOT NULL,
      name      TEXT NOT NULL COLLATE NOCASE,
      unit_key  TEXT NOT NULL,
      amount    REAL,
      pushed_at TEXT NOT NULL,
      PRIMARY KEY (week, name, unit_key)
    );
    """,
    """
    ALTER TABLE users ADD COLUMN eats INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE plan_slots ADD COLUMN guests INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE slot_eaters (
      week    TEXT NOT NULL,
      day     INTEGER NOT NULL,
      meal    TEXT NOT NULL,
      user_id TEXT NOT NULL REFERENCES users(id),
      PRIMARY KEY (week, day, meal, user_id),
      FOREIGN KEY (week, day, meal) REFERENCES plan_slots(week, day, meal) ON DELETE CASCADE
    );
    INSERT INTO slot_eaters (week, day, meal, user_id)
      SELECT s.week, s.day, s.meal, u.id FROM plan_slots s CROSS JOIN users u WHERE s.active = 1;
    ALTER TABLE plan_slots DROP COLUMN portions;
    """,
    """
    ALTER TABLE users ADD COLUMN kcal_target INTEGER;
    ALTER TABLE users ADD COLUMN protein_target_g INTEGER;
    ALTER TABLE users ADD COLUMN canteen_kcal INTEGER NOT NULL DEFAULT 700;
    ALTER TABLE users ADD COLUMN canteen_days TEXT NOT NULL DEFAULT '[]';
    CREATE TABLE plan_canteen (
      week    TEXT NOT NULL REFERENCES plans(week),
      day     INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
      user_id TEXT NOT NULL REFERENCES users(id),
      PRIMARY KEY (week, day, user_id)
    );
    """,
    """
    ALTER TABLE tags ADD COLUMN category INTEGER NOT NULL DEFAULT 0;
    INSERT OR IGNORE INTO tags (name) VALUES
      ('Meal Prep'), ('Sonntagsessen'), ('Proteinreich'), ('Lunchbox'), ('Ofengericht'), ('Gäste');
    UPDATE tags SET category = 1 WHERE name IN
      ('Schnell', 'Meal Prep', 'Sonntagsessen', 'Leicht', 'Proteinreich', 'Lunchbox', 'Ofengericht', 'Gäste');
    ALTER TABLE plan_slots ADD COLUMN rule_tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL;
    """,
    """
    ALTER TABLE plan_slots ADD COLUMN leftover_day INTEGER;
    ALTER TABLE plan_slots ADD COLUMN leftover_meal TEXT;
    """,
]

DEFAULTS = {"bring_entity": None, "ai_enabled": True, "ai_entity": None, "default_portions": 2, "inbox_entity": None,
            "slot_pattern": [True] * 14, "repeat_window_days": 14, "new_per_week": 2, "slot_rules": [None] * 14}
INT_RANGES = {"default_portions": (1, 12), "repeat_window_days": (0, 60), "new_per_week": (0, 14)}
HOUSEHOLD_INTS = {"kcal_target": (300, 5000), "protein_target_g": (10, 400), "canteen_kcal": (0, 2000)}  # the targets may be null
ENTITY_DOMAIN = {"bring_entity": "todo", "ai_entity": "ai_task", "inbox_entity": "todo"}


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
    settings = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()}
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in settings:
            settings[row["key"]] = json.loads(row["value"])
    categories = category_ids(conn)
    settings["slot_rules"] = [v if v in categories else None for v in settings["slot_rules"]]  # a deleted tag reads as none
    return settings


def category_ids(conn):
    return {r["id"] for r in conn.execute("SELECT id FROM tags WHERE category = 1")}


def _validate(conn, key, value):
    if key == "ai_enabled":
        if not isinstance(value, bool):
            raise InvalidField(key)
    elif key in INT_RANGES:
        if not isinstance(value, int) or isinstance(value, bool) or not INT_RANGES[key][0] <= value <= INT_RANGES[key][1]:
            raise InvalidField(key)
    elif key == "slot_pattern":
        if not isinstance(value, list) or len(value) != 14 or not all(isinstance(v, bool) for v in value):
            raise InvalidField(key)
    elif key == "slot_rules":
        categories = category_ids(conn)
        if not isinstance(value, list) or len(value) != 14 or not all(v is None or (_is_int(v, 1, 2**63) and v in categories) for v in value):
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
        _validate(conn, key, value)
    merged = {**get_settings(conn), **patch}
    if merged["inbox_entity"] is not None and merged["inbox_entity"] == merged["bring_entity"]:
        raise InvalidField("inbox_entity" if "inbox_entity" in patch else "bring_entity")
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


def _is_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def household(conn):
    """[{user_id, display_name, eats, kcal_target, protein_target_g, canteen_kcal, canteen_days}] of every registered
    user, sorted by name."""
    rows = conn.execute("SELECT id, display_name, eats, kcal_target, protein_target_g, canteen_kcal, canteen_days FROM users")
    return sorted(({"user_id": r["id"], "display_name": r["display_name"], "eats": bool(r["eats"]),
                    "kcal_target": r["kcal_target"], "protein_target_g": r["protein_target_g"],
                    "canteen_kcal": r["canteen_kcal"], "canteen_days": json.loads(r["canteen_days"])} for r in rows),
                  key=lambda p: (p["display_name"].casefold(), p["user_id"]))


def set_household(conn, user_id, patch):
    """Validate the whole patch (`eats`: bool; `kcal_target`, `protein_target_g`: null or in range; `canteen_kcal`: in range;
    `canteen_days`: distinct weekdays 0-6), then store it. Returns the person's household entry, None for an unknown user."""
    if not isinstance(patch, dict):
        raise InvalidField("body")
    for key, value in patch.items():
        if key == "eats":
            ok = isinstance(value, bool)
        elif key in HOUSEHOLD_INTS:
            ok = _is_int(value, *HOUSEHOLD_INTS[key]) or (value is None and key != "canteen_kcal")
        elif key == "canteen_days":
            ok = isinstance(value, list) and all(_is_int(d, 0, 6) for d in value) and len(set(value)) == len(value)
        else:
            ok = False
        if not ok:
            raise InvalidField(key)
    with conn:
        for key, value in patch.items():  # keys are validated above, so they are safe column names
            stored = int(value) if key == "eats" else json.dumps(sorted(value)) if key == "canteen_days" else value
            conn.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (stored, user_id))
    return next((p for p in household(conn) if p["user_id"] == user_id), None)
