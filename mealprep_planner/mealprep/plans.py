"""Week plans (M7): load/store plans and slots, run the pure planner on the stored data."""
import json
from datetime import datetime, timedelta

from . import db, planner, recipes

ACTIONS = ("reroll", "set", "clear", "lock", "unlock", "activate", "deactivate", "eater", "guests", "rule", "skip", "unskip")


class Refused(Exception):
    """The action is not possible in the current state (-> 400 bad_request)."""


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


MAX_GUESTS = 12


def participants(conn):
    """User ids with "Ich esse mit" on."""
    return [r["id"] for r in conn.execute("SELECT id FROM users WHERE eats = 1")]


def targets(conn):
    """{user_id: {kcal_target, protein_target_g, canteen_kcal}} of every user (planner input)."""
    return {r["id"]: {k: r[k] for k in ("kcal_target", "protein_target_g", "canteen_kcal")}
            for r in conn.execute("SELECT id, kcal_target, protein_target_g, canteen_kcal FROM users")}


def recipe_nutrition(conn, ids):
    """{recipe_id: {kcal, protein_g, fat_g, carbs_g, source} or None (no nutrition)} for the given recipe ids."""
    ids = sorted(ids)
    return {r["id"]: ({k: r[k] for k in planner.NUTRIENTS} | {"source": r["nutrition_source"]} if r["nutrition_source"] else None)
            for r in conn.execute(
                f"SELECT id, kcal, protein_g, fat_g, carbs_g, nutrition_source FROM recipes WHERE id IN ({', '.join('?' * len(ids))})", ids)}


def load_plan(conn, week, settings):
    """The stored plan; a missing week is created as a draft from the slot pattern and slot rules. Every participant gets
    the canteen on their default weekdays (and is then not on that day's lunch)."""
    with conn:
        if conn.execute("SELECT 1 FROM plans WHERE week = ?", (week,)).fetchone() is None:
            conn.execute("INSERT INTO plans (week, status) VALUES (?, 'draft')", (week,))
            conn.executemany(
                "INSERT INTO plan_slots (week, day, meal, active, rule_tag_id) VALUES (?, ?, ?, ?, ?)",
                [(week, i // 2, planner.MEALS[i % 2], int(on), settings["slot_rules"][i]) for i, on in enumerate(settings["slot_pattern"])])
            canteen = {r["id"]: json.loads(r["canteen_days"]) for r in conn.execute("SELECT id, canteen_days FROM users WHERE eats = 1")}
            conn.executemany("INSERT INTO plan_canteen (week, day, user_id) VALUES (?, ?, ?)",
                             [(week, d, u) for u, days in canteen.items() for d in days])
            conn.executemany(
                "INSERT INTO slot_eaters (week, day, meal, user_id) VALUES (?, ?, ?, ?)",
                [(week, i // 2, planner.MEALS[i % 2], u) for i, on in enumerate(settings["slot_pattern"]) if on
                 for u, days in canteen.items() if not (i % 2 == 0 and i // 2 in days)])
    return read_plan(conn, week)


def read_plan(conn, week):
    """The stored plan (slots, and `canteen` = {day: [user_id]}), None if the week has none. Never creates one."""
    row = conn.execute("SELECT week, status, confirmed_at FROM plans WHERE week = ?", (week,)).fetchone()
    if row is None:
        return None
    plan = dict(row)
    plan["canteen"] = {}
    for r in conn.execute("SELECT day, user_id FROM plan_canteen WHERE week = ? ORDER BY user_id", (week,)):
        plan["canteen"].setdefault(r["day"], []).append(r["user_id"])
    slot_eaters = {}
    for r in conn.execute("SELECT day, meal, user_id FROM slot_eaters WHERE week = ? ORDER BY user_id", (week,)):
        slot_eaters.setdefault((r["day"], r["meal"]), []).append(r["user_id"])
    plan["slots"] = [
        {"day": r["day"], "meal": r["meal"], "active": bool(r["active"]), "recipe_id": r["recipe_id"],
         "eaters": slot_eaters.get((r["day"], r["meal"]), []), "guests": r["guests"],
         "locked": bool(r["locked"]), "skipped": bool(r["skipped"]), "rule": r["rule"],
         "reason": json.loads(r["reason"]) if r["reason"] else None}
        for r in conn.execute("SELECT plan_slots.*, tags.name AS rule FROM plan_slots LEFT JOIN tags ON tags.id = rule_tag_id "
                              "WHERE week = ? ORDER BY day, meal = 'dinner'", (week,))]
    return plan


def _store(conn, week, slots):
    with conn:
        conn.executemany(
            "UPDATE plan_slots SET active = ?, recipe_id = ?, guests = ?, locked = ?, skipped = ?, reason = ?, "
            "rule_tag_id = (SELECT id FROM tags WHERE name = ?) WHERE week = ? AND day = ? AND meal = ?",
            [(int(s["active"]), s["recipe_id"], s["guests"], int(s["locked"]), int(s["skipped"]),
              json.dumps(s["reason"]) if s["reason"] else None, s["rule"], week, s["day"], s["meal"]) for s in slots])
        conn.execute("DELETE FROM slot_eaters WHERE week = ?", (week,))
        conn.executemany("INSERT INTO slot_eaters (week, day, meal, user_id) VALUES (?, ?, ?, ?)",
                         [(week, s["day"], s["meal"], u) for s in slots for u in s["eaters"]])


def week_portions(conn, plan):
    """The personal portion factors {(user_id, day): factor} of a stored plan (planner.personal_factors)."""
    nutrition = recipe_nutrition(conn, {s["recipe_id"] for s in plan["slots"] if s["recipe_id"] is not None})
    return planner.personal_factors(plan["slots"], plan["canteen"], targets(conn), nutrition)


def _inputs(conn, week):
    """recipes, ratings, users, history for the planner."""
    return (recipes.planning_recipes(conn), recipes.all_ratings(conn),
            [r["id"] for r in conn.execute("SELECT id FROM users")], planner.history(conn, week))


def view(conn, week, settings, today):
    """The plan as the API returns it: slots with recipe info, reason, eaters, personal portions and cooked portions,
    day totals (per portion, plus per person against the targets) and who is at the canteen."""
    plan = load_plan(conn, week, settings)
    names = {r["id"]: r["display_name"] for r in conn.execute("SELECT id, display_name FROM users")}
    ids = sorted({s["recipe_id"] for s in plan["slots"] if s["recipe_id"] is not None})
    info = {r["id"]: {"id": r["id"], "title": r["title"], "image": r["image"], "kcal": r["kcal"]} for r in conn.execute(
        f"SELECT id, title, image, kcal FROM recipes WHERE id IN ({', '.join('?' * len(ids))})", ids)}
    nutrition, people = recipe_nutrition(conn, ids), targets(conn)
    factors = planner.personal_factors(plan["slots"], plan["canteen"], people, nutrition)
    totals = planner.day_totals(plan["slots"], nutrition)
    for total, by_user, day in zip(totals, planner.person_day_totals(plan["slots"], plan["canteen"], people, nutrition), range(7)):
        total.update(canteen=plan["canteen"].get(day, []), totals_by_user=by_user)
    dates = planner.week_dates(week)
    return {
        "week": week, "status": plan["status"], "confirmed_at": plan["confirmed_at"], "today": today.isoformat(),
        "dates": [d.isoformat() for d in dates],
        "slots": [{"day": s["day"], "meal": s["meal"], "date": dates[s["day"]].isoformat(), "active": s["active"],
                   "recipe": info.get(s["recipe_id"]), "locked": s["locked"], "skipped": s["skipped"], "reason": s["reason"],
                   "rule": s["rule"], "eaters": sorted(({"user_id": u, "display_name": names[u]} for u in s["eaters"]),
                                    key=lambda p: (p["display_name"].casefold(), p["user_id"])),
                   "portions_by_user": {u: factors.get((u, s["day"]), 1.0) for u in s["eaters"]},
                   "guests": s["guests"], "cooked_portions": planner.cooked_portions(s, factors)} for s in plan["slots"]],
        "totals": totals,
    }


def generate(conn, week, settings, rng, today):
    plan = load_plan(conn, week, settings)
    _store(conn, week, planner.generate(plan, *_inputs(conn, week), settings, rng, today, targets(conn)))


def confirm(conn, week, settings):
    load_plan(conn, week, settings)
    with conn:
        conn.execute("UPDATE plans SET status = 'confirmed', confirmed_at = ? WHERE week = ?",
                     (datetime.now().isoformat(timespec="seconds"), week))


def slot_action(conn, week, day, meal, body, settings, rng, today):
    """Apply one slot action of the API (section 7). InvalidField for bad input, Refused for a wrong state."""
    action = body.get("action")
    if action not in ACTIONS:
        raise db.InvalidField("action")
    plan = load_plan(conn, week, settings)
    slots = plan["slots"]
    slot = next(s for s in slots if s["day"] == day and s["meal"] == meal)
    if action in ("reroll", "set", "clear") and (day, meal) in planner.protected(plan, today):
        raise Refused(action)
    if action == "reroll":
        if not slot["active"] or slot["locked"]:
            raise Refused(action)
        slots = planner.reroll(plan, day, meal, *_inputs(conn, week), settings, rng, targets(conn))
    elif action == "set":
        recipe_id = body.get("recipe_id")
        row = conn.execute("SELECT archived, kcal FROM recipes WHERE id = ?", (recipe_id,)).fetchone() if _int(recipe_id) else None
        if row is None or row["archived"]:
            raise db.InvalidField("recipe_id")
        label = planner.fit(slots, slot, row["kcal"], targets(conn), plan["canteen"])[0]
        slot.update(recipe_id=recipe_id, reason={"kind": "manual", **({"fit": label} if label else {})})
    elif action == "clear":
        slot.update(recipe_id=None, reason=None, locked=False)
    elif action in ("lock", "unlock"):
        slot["locked"] = action == "lock"
    elif action == "activate":
        slot.update(active=True, eaters=[u for u in participants(conn) if not (meal == "lunch" and u in plan["canteen"].get(day, ()))])
    elif action == "deactivate":
        slot.update(active=False, eaters=[], guests=0)
    elif action == "eater":
        user_id, on = body.get("user_id"), body.get("on")
        if not isinstance(user_id, str) or conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise db.InvalidField("user_id")
        if not isinstance(on, bool):
            raise db.InvalidField("on")
        if not slot["active"]:
            raise Refused(action)
        slot["eaters"] = sorted({*slot["eaters"], user_id} if on else set(slot["eaters"]) - {user_id})
    elif action == "guests":
        n = body.get("n")
        if not _int(n) or not 0 <= n <= MAX_GUESTS:
            raise db.InvalidField("n")
        slot["guests"] = n
    elif action == "rule":
        tag_id = body.get("tag_id")
        row = conn.execute("SELECT name FROM tags WHERE id = ? AND category = 1", (tag_id,)).fetchone() if _int(tag_id) else None
        if tag_id is not None and row is None:
            raise db.InvalidField("tag_id")
        slot["rule"] = row["name"] if row else None
    else:  # skip / unskip
        if plan["status"] != "confirmed" or planner.week_dates(week)[day] > today:
            raise Refused(action)
        slot["skipped"] = action == "skip"
    _store(conn, week, slots)


def set_canteen(conn, week, day, user_id, on, settings):
    """A person eats at the canteen on that day (on) or not (off). On: the person leaves that day's lunch; off: they
    are back on it if it is active. InvalidField for bad input."""
    if not _int(day) or not 0 <= day <= 6:
        raise db.InvalidField("day")
    if not isinstance(user_id, str) or conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise db.InvalidField("user_id")
    if not isinstance(on, bool):
        raise db.InvalidField("on")
    load_plan(conn, week, settings)
    with conn:
        if on:
            conn.execute("INSERT OR IGNORE INTO plan_canteen (week, day, user_id) VALUES (?, ?, ?)", (week, day, user_id))
            conn.execute("DELETE FROM slot_eaters WHERE week = ? AND day = ? AND meal = 'lunch' AND user_id = ?", (week, day, user_id))
        else:
            conn.execute("DELETE FROM plan_canteen WHERE week = ? AND day = ? AND user_id = ?", (week, day, user_id))
            conn.execute("INSERT OR IGNORE INTO slot_eaters (week, day, meal, user_id) "
                         "SELECT week, day, meal, ? FROM plan_slots WHERE week = ? AND day = ? AND meal = 'lunch' AND active = 1",
                         (user_id, week, day))


# ---- today page (M9) ----

RATE_DAYS = 14  # "Wie war's?" looks back this many days before today
RATE_MAX = 10


def dated_slots(conn, days, confirmed_only=False):
    """Filled, active, non-skipped slots on the given dates (any plan status unless confirmed_only), oldest first:
    [{date, meal, recipe_id, title, image, kcal, cooked_portions, portions_by_user}]. Never creates a plan."""
    days = set(days)
    weeks = sorted({planner.week_of(d) for d in days})
    rows = conn.execute(
        "SELECT s.week, s.day, s.meal, s.recipe_id, r.title, r.image, r.kcal FROM plan_slots s "
        "JOIN plans p ON p.week = s.week JOIN recipes r ON r.id = s.recipe_id "
        f"WHERE s.week IN ({', '.join('?' * len(weeks))}) AND s.active = 1 AND s.skipped = 0"
        + (" AND p.status = 'confirmed'" if confirmed_only else ""), weeks).fetchall()
    stored = {w: read_plan(conn, w) for w in {r["week"] for r in rows}}
    factors = {w: week_portions(conn, p) for w, p in stored.items()}
    slots = []
    for r in rows:
        s = next(s for s in stored[r["week"]]["slots"] if (s["day"], s["meal"]) == (r["day"], r["meal"]))
        slots.append({"date": planner.week_dates(r["week"])[r["day"]], "meal": r["meal"], "recipe_id": r["recipe_id"],
                      "title": r["title"], "image": r["image"], "kcal": r["kcal"],
                      "cooked_portions": planner.cooked_portions(s, factors[r["week"]]),
                      "portions_by_user": {u: factors[r["week"]].get((u, r["day"]), 1.0) for u in s["eaters"]}})
    return sorted((s for s in slots if s["date"] in days), key=lambda s: (s["date"], planner.MEALS.index(s["meal"])))


def sensor_slots(conn, today):
    """The slots sensor_payload needs: the current week and tomorrow."""
    return dated_slots(conn, [*planner.week_dates(planner.week_of(today)), today + timedelta(days=1)])


def rate_list(conn, user_id, today):
    """[{recipe_id, title, date, meal}]: meals of the RATE_DAYS days before today (confirmed plans) whose recipe the
    user has not rated; newest first, one entry per recipe, at most RATE_MAX."""
    rated = {r["recipe_id"] for r in conn.execute("SELECT recipe_id FROM ratings WHERE user_id = ?", (user_id,))}
    out, seen = [], set()
    for s in reversed(dated_slots(conn, [today - timedelta(days=n) for n in range(1, RATE_DAYS + 1)], True)):
        if s["recipe_id"] not in rated | seen:
            seen.add(s["recipe_id"])
            out.append({"recipe_id": s["recipe_id"], "title": s["title"], "date": s["date"].isoformat(), "meal": s["meal"]})
    return out[:RATE_MAX]


def today_view(conn, user_id, today):
    """GET api/today: today's and tomorrow's meals (null = nothing planned), each with the user's own portion and kcal
    (null when they do not eat it), `my_canteen` per day, and the "Wie war's?" list."""
    slots = dated_slots(conn, [today, today + timedelta(days=1)])
    canteen = {(r["week"], r["day"]) for r in conn.execute("SELECT week, day FROM plan_canteen WHERE user_id = ?", (user_id,))}

    def meal_of(s):
        portion = s["portions_by_user"].get(user_id)
        return {**{k: s[k] for k in ("recipe_id", "title", "image", "cooked_portions")}, "my_portion": portion,
                "my_kcal": int(portion * s["kcal"] + 0.5) if portion is not None and s["kcal"] else None}

    def day(d):
        return {"date": d.isoformat(), "my_canteen": (planner.week_of(d), d.weekday()) in canteen,
                **{meal: next((meal_of(s) for s in slots if (s["date"], s["meal"]) == (d, meal)), None) for meal in planner.MEALS}}

    return {"today": day(today), "tomorrow": day(today + timedelta(days=1)), "rate": rate_list(conn, user_id, today)}
