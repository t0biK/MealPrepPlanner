"""Shopping list (M8): build it from a week plan, diff it against what was pushed before, push the rest to Bring!.

item: {name, amounts: {unit_key: amount}}; an empty `amounts` means "no amount" (e.g. Salz).
current / to_push / no_longer_needed: {name.casefold(): item}   pushed: {(name.casefold(), unit_key): {name, amount}}
"""
import threading
from datetime import datetime

from . import db, ha, ingredients

MAX_PANTRY = 300
MAX_NAME = 100
SLOTS_SQL = (
    "SELECT i.name, i.amount, i.unit, s.portions, r.servings FROM plan_slots s "
    "JOIN recipes r ON r.id = s.recipe_id JOIN ingredients i ON i.recipe_id = r.id "
    "WHERE s.week = ? AND s.active = 1 AND s.skipped = 0")
_push_lock = threading.Lock()  # one push at a time: two parallel pushes would add duplicates


class BringFailed(Exception):
    """Bring! could not be reached at all (no list chosen, HA down, unexpected response) -> 502 bring_failed."""


def get_pantry(conn):
    return sorted((r["name"] for r in conn.execute("SELECT name FROM pantry")), key=str.casefold)


def set_pantry(conn, names):
    """Replace the pantry; each name 1-100 chars after trimming, ≤ 300 names, duplicates (case-insensitive) dropped."""
    if not isinstance(names, list) or len(names) > MAX_PANTRY:
        raise db.InvalidField("names")
    clean = {}
    for name in names:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME:
            raise db.InvalidField("names")
        clean.setdefault(name.strip().casefold(), name.strip())
    with conn:
        conn.execute("DELETE FROM pantry")
        conn.executemany("INSERT INTO pantry (name) VALUES (?)", [(n,) for n in clean.values()])
    return get_pantry(conn)


def build_list(conn, week):
    """(items, skipped_pantry): what the week needs (active, non-skipped, filled slots; amounts scaled by
    portions / servings), without pantry names (case-insensitive), plus the display names left out."""
    items = ingredients.aggregate(
        (r["name"], ingredients.scale(r["amount"], r["portions"] / r["servings"]), r["unit"])
        for r in conn.execute(SLOTS_SQL + " ORDER BY s.day, s.meal = 'dinner', i.pos", (week,)))
    pantry = {n.casefold() for n in get_pantry(conn)}
    skipped = sorted((i["name"] for k, i in items.items() if k in pantry), key=str.casefold)
    return {k: i for k, i in items.items() if k not in pantry}, skipped


def pushed_items(conn, week):
    return {(r["name"].casefold(), r["unit_key"]): {"name": r["name"], "amount": r["amount"]}
            for r in conn.execute("SELECT name, unit_key, amount FROM pushed_items WHERE week = ?", (week,))}


def diff(current, pushed):
    """(to_push, no_longer_needed). A positive delta per name + unit_key is pushed; an amount-less item once.
    Smaller or missing current amounts are no longer needed (the surplus, or the whole item)."""
    names_pushed = {nk for nk, _ in pushed}
    to_push, gone = {}, {}
    for nk, item in current.items():
        if not item["amounts"]:
            if nk not in names_pushed:
                to_push[nk] = {"name": item["name"], "amounts": {}}
            continue
        delta = {}
        for key, amount in item["amounts"].items():
            have = pushed.get((nk, key), {}).get("amount") or 0
            if round(amount - have, 4) > 0:
                delta[key] = round(amount - have, 4)
        if delta:
            to_push[nk] = {"name": item["name"], "amounts": delta}
    for (nk, key), row in pushed.items():
        cur = current.get(nk)
        if cur is not None and (not cur["amounts"] or row["amount"] is None):
            continue  # still needed, only its amount is missing
        surplus = None if row["amount"] is None else round(row["amount"] - (cur["amounts"].get(key, 0) if cur else 0), 4)
        if surplus is None or surplus > 0:
            entry = gone.setdefault(nk, {"name": row["name"], "amounts": {}})
            if surplus is not None:
                entry["amounts"][key] = surplus
    return to_push, gone


def _entry(item):
    return {"name": item["name"], "note": ingredients.format_note(item["amounts"])}


def view(conn, week):
    """The shopping list as the API returns it: status per item, pantry names left out, no longer needed."""
    current, skipped = build_list(conn, week)
    to_push, gone = diff(current, pushed_items(conn, week))
    return {"items": [{**_entry(i), "status": "pending" if k in to_push else "pushed"} for k, i in sorted(current.items())],
            "pantry": skipped, "no_longer_needed": [_entry(i) for _, i in sorted(gone.items())]}


def _open_items(entity):
    """{name.casefold(): {ref, description}} of the open Bring! items; the first one wins when names repeat (V3)."""
    try:
        items = ha.call_service("todo", "get_items", {"entity_id": entity, "status": ["needs_action"]}, True)[
            "service_response"][entity]["items"]
    except (ha.HAError, KeyError, TypeError, IndexError):  # HA down or an untrusted response shape
        raise BringFailed from None
    if not isinstance(items, list):
        raise BringFailed
    out = {}
    for i in items:
        if isinstance(i, dict) and isinstance(i.get("summary"), str) and i.get("status", "needs_action") == "needs_action":
            uid = i.get("uid")
            out.setdefault(i["summary"].strip().casefold(), {
                "ref": uid if isinstance(uid, str) and uid else i["summary"],  # update_item addresses by uid (V4)
                "description": i["description"].strip() if isinstance(i.get("description"), str) else ""})
    return out


def _record(conn, week, item, current_item, pushed):
    """Remember what is now in Bring! for this item (cumulative per unit_key)."""
    nk = item["name"].casefold()
    with conn:
        for key in item["amounts"] or {"": None}:
            conn.execute(
                "INSERT INTO pushed_items (week, name, unit_key, amount, pushed_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(week, name, unit_key) DO UPDATE SET amount = excluded.amount, pushed_at = excluded.pushed_at",
                (week, pushed.get((nk, key), item)["name"], key, current_item["amounts"].get(key),
                 datetime.now().isoformat(timespec="seconds")))


def _lower(conn, week, current, pushed):
    """Items no longer needed are assumed removed from Bring! by hand: lower their recorded amount to the current
    need (row deleted when nothing is needed), so a later increase pushes the difference again. Same rows as `diff`."""
    with conn:
        for (nk, key), row in pushed.items():
            cur = current.get(nk)
            if cur is not None and (not cur["amounts"] or row["amount"] is None):
                continue  # still needed, only its amount is missing
            need = cur["amounts"].get(key, 0) if cur else 0
            if row["amount"] is not None and round(row["amount"] - need, 4) <= 0:
                continue  # nothing surplus
            if need:
                conn.execute("UPDATE pushed_items SET amount = ? WHERE week = ? AND name = ? AND unit_key = ?",
                             (need, week, row["name"], key))
            else:
                conn.execute("DELETE FROM pushed_items WHERE week = ? AND name = ? AND unit_key = ?", (week, row["name"], key))


def push(conn, week):
    """Push what is new or increased since the last push to the Bring! list. Every successful call is recorded
    at once, so a partial failure can be resumed. Returns {added, updated, skipped_pantry, no_longer_needed, failed};
    raises BringFailed when Bring! is not reachable at all."""
    entity = db.get_settings(conn)["bring_entity"]
    if not entity:
        raise BringFailed
    with _push_lock:
        current, skipped = build_list(conn, week)
        pushed = pushed_items(conn, week)
        to_push, gone = diff(current, pushed)
        result = {"added": [], "updated": [], "skipped_pantry": skipped,
                  "no_longer_needed": [_entry(i) for _, i in sorted(gone.items())], "failed": []}
        open_items = _open_items(entity) if to_push else {}
        _lower(conn, week, current, pushed)  # only once Bring! answered, so a BringFailed keeps the list for the retry
        if not to_push:
            return result
        down = False
        for nk, item in sorted(to_push.items()):
            entry = _entry(item)
            existing = open_items.get(nk)
            if down:
                result["failed"].append(entry)
                continue
            try:
                if existing is None:
                    ha.call_service("todo", "add_item", {"entity_id": entity, "item": item["name"],
                                                         **({"description": entry["note"]} if entry["note"] else {})})
                elif entry["note"]:  # Bring! truncates long descriptions; accepted (agreed 2026-10-06)
                    ha.call_service("todo", "update_item", {
                        "entity_id": entity, "item": existing["ref"],
                        "description": f"{existing['description']} + {entry['note']}" if existing["description"] else entry["note"]})
            except ha.HAError as e:
                down = e.code == "ha_unavailable"  # no point in waiting for every remaining item
                result["failed"].append(entry)
                continue
            _record(conn, week, item, current[nk], pushed)
            result["added" if existing is None else "updated"].append(entry)
    return result
