"""Week plans (M7): load/store plans and slots, run the pure planner on the stored data."""
import json
from datetime import datetime, timedelta

from . import db, planner, recipes

ACTIONS = ("reroll", "set", "clear", "lock", "unlock", "activate", "deactivate", "portions", "skip", "unskip")


class Refused(Exception):
    """The action is not possible in the current state (-> 400 bad_request)."""


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def load_plan(conn, week, settings):
    """The stored plan; a missing week is created as a draft from the slot pattern."""
    with conn:
        if conn.execute("SELECT 1 FROM plans WHERE week = ?", (week,)).fetchone() is None:
            conn.execute("INSERT INTO plans (week, status) VALUES (?, 'draft')", (week,))
            conn.executemany(
                "INSERT INTO plan_slots (week, day, meal, active, portions) VALUES (?, ?, ?, ?, ?)",
                [(week, i // 2, planner.MEALS[i % 2], int(on), settings["default_portions"])
                 for i, on in enumerate(settings["slot_pattern"])])
    plan = dict(conn.execute("SELECT week, status, confirmed_at FROM plans WHERE week = ?", (week,)).fetchone())
    plan["slots"] = [
        {"day": r["day"], "meal": r["meal"], "active": bool(r["active"]), "recipe_id": r["recipe_id"],
         "portions": r["portions"], "locked": bool(r["locked"]), "skipped": bool(r["skipped"]),
         "reason": json.loads(r["reason"]) if r["reason"] else None}
        for r in conn.execute("SELECT * FROM plan_slots WHERE week = ? ORDER BY day, meal = 'dinner'", (week,))]
    return plan


def _store(conn, week, slots):
    with conn:
        conn.executemany(
            "UPDATE plan_slots SET active = ?, recipe_id = ?, portions = ?, locked = ?, skipped = ?, reason = ? "
            "WHERE week = ? AND day = ? AND meal = ?",
            [(int(s["active"]), s["recipe_id"], s["portions"], int(s["locked"]), int(s["skipped"]),
              json.dumps(s["reason"]) if s["reason"] else None, week, s["day"], s["meal"]) for s in slots])


def _inputs(conn, week):
    """recipes, ratings, users, history for the planner."""
    return (recipes.planning_recipes(conn), recipes.all_ratings(conn),
            [r["id"] for r in conn.execute("SELECT id FROM users")], planner.history(conn, week))


def view(conn, week, settings, today):
    """The plan as the API returns it: slots with recipe info and reason, day totals."""
    plan = load_plan(conn, week, settings)
    ids = sorted({s["recipe_id"] for s in plan["slots"] if s["recipe_id"] is not None})
    info, nutrition = {}, {}
    for r in conn.execute(
        f"SELECT id, title, image, kcal, protein_g, fat_g, carbs_g, nutrition_source FROM recipes "
        f"WHERE id IN ({', '.join('?' * len(ids))})", ids):
        info[r["id"]] = {"id": r["id"], "title": r["title"], "image": r["image"]}
        nutrition[r["id"]] = ({k: r[k] for k in planner.NUTRIENTS} | {"source": r["nutrition_source"]}
                              if r["nutrition_source"] else None)
    dates = planner.week_dates(week)
    return {
        "week": week, "status": plan["status"], "confirmed_at": plan["confirmed_at"], "today": today.isoformat(),
        "dates": [d.isoformat() for d in dates],
        "slots": [{"day": s["day"], "meal": s["meal"], "date": dates[s["day"]].isoformat(), "active": s["active"],
                   "recipe": info.get(s["recipe_id"]), "portions": s["portions"], "locked": s["locked"],
                   "skipped": s["skipped"], "reason": s["reason"]} for s in plan["slots"]],
        "totals": planner.day_totals(plan["slots"], nutrition),
    }


def generate(conn, week, settings, rng):
    plan = load_plan(conn, week, settings)
    _store(conn, week, planner.generate(plan, *_inputs(conn, week), settings, rng))


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
    if action == "reroll":
        if not slot["active"]:
            raise Refused(action)
        slots = planner.reroll(plan, day, meal, *_inputs(conn, week), settings, rng)
    elif action == "set":
        recipe_id = body.get("recipe_id")
        row = conn.execute("SELECT archived FROM recipes WHERE id = ?", (recipe_id,)).fetchone() if _int(recipe_id) else None
        if row is None or row["archived"]:
            raise db.InvalidField("recipe_id")
        slot.update(recipe_id=recipe_id, reason={"kind": "manual"})
    elif action == "clear":
        slot.update(recipe_id=None, reason=None, locked=False)
    elif action in ("lock", "unlock"):
        slot["locked"] = action == "lock"
    elif action in ("activate", "deactivate"):
        slot["active"] = action == "activate"
    elif action == "portions":
        portions = body.get("portions")
        if not _int(portions) or not 1 <= portions <= 12:
            raise db.InvalidField("portions")
        slot["portions"] = portions
    else:  # skip / unskip
        if plan["status"] != "confirmed" or planner.week_dates(week)[day] > today:
            raise Refused(action)
        slot["skipped"] = action == "skip"
    _store(conn, week, slots)


# ---- today page (M9) ----

RATE_DAYS = 14  # "Wie war's?" looks back this many days before today
RATE_MAX = 10


def dated_slots(conn, days, confirmed_only=False):
    """Filled, active, non-skipped slots on the given dates (any plan status unless confirmed_only), oldest first:
    [{date, meal, recipe_id, title, image, portions}]. Never creates a plan."""
    days = set(days)
    weeks = sorted({planner.week_of(d) for d in days})
    rows = conn.execute(
        "SELECT s.week, s.day, s.meal, s.recipe_id, s.portions, r.title, r.image FROM plan_slots s "
        "JOIN plans p ON p.week = s.week JOIN recipes r ON r.id = s.recipe_id "
        f"WHERE s.week IN ({', '.join('?' * len(weeks))}) AND s.active = 1 AND s.skipped = 0"
        + (" AND p.status = 'confirmed'" if confirmed_only else ""), weeks)
    slots = [{"date": planner.week_dates(r["week"])[r["day"]], "meal": r["meal"], "recipe_id": r["recipe_id"],
              "title": r["title"], "image": r["image"], "portions": r["portions"]} for r in rows]
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
    """GET api/today: today's and tomorrow's meals (null = nothing planned) and the "Wie war's?" list."""
    slots = dated_slots(conn, [today, today + timedelta(days=1)])

    def day(d):
        return {"date": d.isoformat(), **{meal: next(
            ({k: s[k] for k in ("recipe_id", "title", "image", "portions")} for s in slots
             if (s["date"], s["meal"]) == (d, meal)), None) for meal in planner.MEALS}}

    return {"today": day(today), "tomorrow": day(today + timedelta(days=1)), "rate": rate_list(conn, user_id, today)}
