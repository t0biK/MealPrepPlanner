import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import db
from .ingredients import UNITS

SOURCE_KINDS = ("manual", "web", "tiktok", "youtube", "instagram", "text")
WARNINGS = ("no_recipe_data", "already_imported", "ai_failed", "ai_disabled", "paste_caption", "image_failed")
IMAGE_RE = re.compile(r"^[0-9a-f]{64}\.(jpg|png|webp)$")
NUTRITION_MAX = {"kcal": 5000, "protein_g": 500, "fat_g": 500, "carbs_g": 500}
MAX_TAG = 50


def sort_key(s):
    """Case-insensitive German-friendly order (Ä sorts with A)."""
    return s.casefold().translate(str.maketrans("äöüß", "aous"))


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v, lo, hi):
    """Trimmed string within [lo, hi] chars, else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if lo <= len(v) <= hi else None


def http_url(v):
    if not isinstance(v, str) or len(v) > 2048:
        return False
    parts = urlsplit(v)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def validate_draft(obj, tag_names, images_dir=None):
    """Recipe draft (§6) -> (clean draft, errors). errors maps a field path to a reason; unknown keys are dropped."""
    errors = {}
    if not isinstance(obj, dict):
        return None, {"": "not_an_object"}
    get = obj.get
    d = {"format_version": 1}
    if get("format_version") != 1 or not _int(get("format_version")):
        errors["format_version"] = "must_be_1"

    d["title"] = _text(get("title"), 1, 200)
    if d["title"] is None:
        errors["title"] = "invalid"

    for key in ("source_url", "image_url"):
        if key == "image_url" and key not in obj:
            continue
        d[key] = get(key)
        if d[key] is not None and not http_url(d[key]):
            errors[key] = "invalid"

    d["source_kind"] = get("source_kind", "manual")
    if d["source_kind"] not in SOURCE_KINDS:
        errors["source_kind"] = "invalid"

    d["image"] = get("image")
    if d["image"] is not None:
        if not isinstance(d["image"], str) or not IMAGE_RE.match(d["image"]) or (
            images_dir is not None and not (Path(images_dir) / d["image"]).is_file()
        ):
            errors["image"] = "invalid"

    d["servings"] = get("servings")
    if not _int(d["servings"]) or not 1 <= d["servings"] <= 50:
        errors["servings"] = "invalid"

    d["total_minutes"] = get("total_minutes")
    if d["total_minutes"] is not None and (not _int(d["total_minutes"]) or not 1 <= d["total_minutes"] <= 1440):
        errors["total_minutes"] = "invalid"

    for key in ("for_lunch", "for_dinner"):
        d[key] = get(key, True)
        if not isinstance(d[key], bool):
            errors[key] = "invalid"
    if d["for_lunch"] is False and d["for_dinner"] is False:
        errors["for_lunch"] = "at_least_one"

    d["tags"] = []
    tags = get("tags", [])
    known = {n.casefold(): n for n in tag_names}
    if not isinstance(tags, list) or len(tags) > 15:
        errors["tags"] = "invalid"
    else:
        for i, tag in enumerate(tags):
            name = known.get(tag.strip().casefold()) if isinstance(tag, str) else None
            if name is None:
                errors[f"tags.{i}"] = "unknown_tag"
            elif name not in d["tags"]:
                d["tags"].append(name)

    d["ingredients"] = []
    ingredients = get("ingredients", [])
    if not isinstance(ingredients, list) or len(ingredients) > 100:
        errors["ingredients"] = "invalid"
    else:
        for i, ing in enumerate(ingredients):
            if not isinstance(ing, dict):
                errors[f"ingredients.{i}"] = "invalid"
                continue
            amount, unit, note = ing.get("amount"), ing.get("unit"), ing.get("note")
            name = _text(ing.get("name"), 1, 100)
            if amount is not None and (not _num(amount) or not 0 < amount <= 100000):
                errors[f"ingredients.{i}.amount"] = "invalid"
            if unit is not None and unit not in UNITS:
                errors[f"ingredients.{i}.unit"] = "invalid"
            if name is None:
                errors[f"ingredients.{i}.name"] = "invalid"
            if note is not None:
                note = _text(note, 0, 200)
                if note is None:
                    errors[f"ingredients.{i}.note"] = "invalid"
            d["ingredients"].append({"amount": amount, "unit": unit, "name": name, "note": note or None})

    d["steps"] = []
    steps = get("steps", [])
    if not isinstance(steps, list) or len(steps) > 50:
        errors["steps"] = "invalid"
    else:
        for i, step in enumerate(steps):
            step = _text(step, 1, 2000)
            if step is None:
                errors[f"steps.{i}"] = "invalid"
            else:
                d["steps"].append(step)

    d["nutrition"] = None
    nutrition = get("nutrition")
    if nutrition is not None:
        if not isinstance(nutrition, dict):
            errors["nutrition"] = "invalid"
        else:
            n = {}
            for key, hi in NUTRITION_MAX.items():
                n[key] = nutrition.get(key)
                if n[key] is not None and (not _num(n[key]) or not 0 <= n[key] <= hi):
                    errors[f"nutrition.{key}"] = "invalid"
            n["source"] = nutrition.get("source")
            if any(n[k] is not None for k in NUTRITION_MAX):  # all-empty nutrition is stored as none
                if n["source"] not in ("page", "ai", "manual"):
                    errors["nutrition.source"] = "invalid"
                d["nutrition"] = n

    if "warnings" in obj:
        warnings = get("warnings")
        if not isinstance(warnings, list) or any(w not in WARNINGS for w in warnings):
            errors["warnings"] = "invalid"
        d["warnings"] = warnings
    return (None if errors else d), errors


# ---- tags ----

def list_tags(conn):
    rows = conn.execute("SELECT id, name FROM tags").fetchall()
    return sorted(({"id": r["id"], "name": r["name"]} for r in rows), key=lambda t: sort_key(t["name"]))


def _tag_name(name):
    name = _text(name, 1, MAX_TAG)
    if name is None:
        raise db.InvalidField("name")
    return name


def create_tag(conn, name):
    name = _tag_name(name)
    with conn:
        if conn.execute("SELECT 1 FROM tags WHERE name = ?", (name,)).fetchone():
            raise db.InvalidField("name")
        tag_id = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid
    return {"id": tag_id, "name": name}


def rename_tag(conn, tag_id, name):
    """Returns the tag, or None if it does not exist."""
    name = _tag_name(name)
    with conn:
        if conn.execute("SELECT 1 FROM tags WHERE name = ? AND id != ?", (name, tag_id)).fetchone():
            raise db.InvalidField("name")
        if conn.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id)).rowcount == 0:
            return None
    return {"id": tag_id, "name": name}


def delete_tag(conn, tag_id):
    with conn:
        return conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,)).rowcount > 0


# ---- recipes ----

def _now():
    return datetime.now().isoformat(timespec="seconds")


def _store_children(conn, recipe_id, draft):
    conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (recipe_id,))
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        "INSERT INTO ingredients (recipe_id, pos, amount, unit, name, note) VALUES (?, ?, ?, ?, ?, ?)",
        [(recipe_id, i, g["amount"], g["unit"], g["name"], g["note"]) for i, g in enumerate(draft["ingredients"])],
    )
    conn.executemany(
        "INSERT INTO recipe_tags (recipe_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
        [(recipe_id, name) for name in draft["tags"]],
    )


def _columns(draft):
    n = draft["nutrition"] or {}
    return (
        draft["title"], draft["source_url"], draft["source_kind"], draft["image"], draft["servings"],
        draft["total_minutes"], draft["for_lunch"], draft["for_dinner"], json.dumps(draft["steps"]),
        n.get("kcal"), n.get("protein_g"), n.get("fat_g"), n.get("carbs_g"), n.get("source"),
    )


def create_recipe(conn, draft, user_id):
    now = _now()
    with conn:
        recipe_id = conn.execute(
            "INSERT INTO recipes (title, source_url, source_kind, image, servings, total_minutes, for_lunch, "
            "for_dinner, steps, kcal, protein_g, fat_g, carbs_g, nutrition_source, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*_columns(draft), user_id, now, now),
        ).lastrowid
        _store_children(conn, recipe_id, draft)
    return recipe_id


def update_recipe(conn, recipe_id, draft):
    """Returns False if the recipe does not exist."""
    with conn:
        cur = conn.execute(
            "UPDATE recipes SET title = ?, source_url = ?, source_kind = ?, image = ?, servings = ?, "
            "total_minutes = ?, for_lunch = ?, for_dinner = ?, steps = ?, kcal = ?, protein_g = ?, fat_g = ?, "
            "carbs_g = ?, nutrition_source = ?, updated_at = ? WHERE id = ?",
            (*_columns(draft), _now(), recipe_id),
        )
        if cur.rowcount == 0:
            return False
        _store_children(conn, recipe_id, draft)
    return True


def get_recipe(conn, recipe_id):
    r = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if r is None:
        return None
    nutrition = None
    if r["nutrition_source"]:
        nutrition = {k: r[k] for k in NUTRITION_MAX} | {"source": r["nutrition_source"]}
    return {
        "format_version": 1,
        "id": r["id"],
        "archived": bool(r["archived"]),
        "title": r["title"],
        "source_url": r["source_url"],
        "source_kind": r["source_kind"],
        "image": r["image"],
        "servings": r["servings"],
        "total_minutes": r["total_minutes"],
        "for_lunch": bool(r["for_lunch"]),
        "for_dinner": bool(r["for_dinner"]),
        "tags": [t["name"] for t in conn.execute(
            "SELECT name FROM tags JOIN recipe_tags ON tag_id = id WHERE recipe_id = ? ORDER BY name COLLATE NOCASE",
            (recipe_id,))],
        "ingredients": [dict(i) for i in conn.execute(
            "SELECT amount, unit, name, note FROM ingredients WHERE recipe_id = ? ORDER BY pos", (recipe_id,))],
        "steps": json.loads(r["steps"]),
        "nutrition": nutrition,
    }


def list_recipes(conn, q="", tag="", archived=False):
    """Summaries sorted by title; q matches title or ingredient names (case-insensitive, also umlauts)."""
    q = q.strip().casefold()
    tags = {}
    for row in conn.execute("SELECT recipe_id, name FROM recipe_tags JOIN tags ON tag_id = id ORDER BY name COLLATE NOCASE"):
        tags.setdefault(row["recipe_id"], []).append(row["name"])
    names = {}
    if q:
        for row in conn.execute("SELECT recipe_id, name FROM ingredients"):
            names.setdefault(row["recipe_id"], []).append(row["name"].casefold())
    out = []
    for r in conn.execute(
        "SELECT id, title, image, total_minutes, for_lunch, for_dinner FROM recipes WHERE archived = ?", (int(archived),)
    ):
        recipe_tags = tags.get(r["id"], [])
        if tag and tag.casefold() not in (t.casefold() for t in recipe_tags):
            continue
        if q and q not in r["title"].casefold() and not any(q in n for n in names.get(r["id"], [])):
            continue
        out.append({"id": r["id"], "title": r["title"], "image": r["image"], "total_minutes": r["total_minutes"],
                    "for_lunch": bool(r["for_lunch"]), "for_dinner": bool(r["for_dinner"]), "tags": recipe_tags})
    return sorted(out, key=lambda r: sort_key(r["title"]))


def set_archived(conn, recipe_id, archived):
    with conn:
        return conn.execute(
            "UPDATE recipes SET archived = ?, updated_at = ? WHERE id = ?", (int(archived), _now(), recipe_id)
        ).rowcount > 0


def known_ingredient_names(conn):
    seen = {}
    for row in conn.execute("SELECT DISTINCT name FROM ingredients"):
        seen.setdefault(row["name"].casefold(), row["name"])
    return sorted(seen.values(), key=sort_key)


def top_ingredient_names(conn, limit):
    """Most used ingredient names first (case-insensitive), at most `limit`; for the AI prompt."""
    return [r["name"] for r in conn.execute(
        "SELECT name FROM ingredients GROUP BY name COLLATE NOCASE ORDER BY COUNT(*) DESC, name COLLATE NOCASE LIMIT ?",
        (limit,))]
