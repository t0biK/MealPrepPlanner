"""Taste prediction (M6) and week planning (M7): pure functions on plain data (history() is the only DB read).

ratings: {user_id: {recipe_id: stars 0-5}}   tags: {recipe_id: [tag names]}   users: [user_id]
recipes: [{id, for_lunch, for_dinner, archived, tags}]   history: [(recipe_id, date)]
slot: {day 0-6, meal, active, recipe_id, portions, locked, skipped, reason}   plan: {week, slots: [slot x 14]}
"""
import math
import re
from datetime import date, timedelta

PRIOR = 3.0  # household mean while nobody has rated anything
K = 2  # pseudo-ratings pulling a tag affinity towards the user's own mean


def global_mean(ratings):
    """Mean of all stars of all users (zeros count); PRIOR if there are none."""
    stars = [s for rated in ratings.values() for s in rated.values()]
    return sum(stars) / len(stars) if stars else PRIOR


def user_means(users, ratings, g):
    """{user: mean of the user's stars, or g if the user has none}"""
    out = {}
    for u in users:
        stars = list(ratings.get(u, {}).values())
        out[u] = sum(stars) / len(stars) if stars else g
    return out


def tag_affinities(user_ratings, tags, mu):
    """{tag: a_u(t)} for the tags the user has rated at least once; any other tag has affinity mu."""
    sums, counts = {}, {}
    for recipe_id, stars in user_ratings.items():
        for tag in tags.get(recipe_id, ()):
            sums[tag] = sums.get(tag, 0) + stars
            counts[tag] = counts.get(tag, 0) + 1
    return {t: (sums[t] + K * mu) / (counts[t] + K) for t in sums}


def build_model(users, ratings, tags):
    """Everything the per-recipe functions need, computed once per request."""
    g = global_mean(ratings)
    mu = user_means(users, ratings, g)
    return {
        "g": g, "mu": mu, "ratings": ratings, "tags": tags, "users": list(users),
        "affinity": {u: tag_affinities(ratings.get(u, {}), tags, mu[u]) for u in users},
    }


def predict(model, user, recipe_id):
    """pred_u(r): mean affinity of the recipe's tags, or the user's mean if it has no tags."""
    recipe_tags = model["tags"].get(recipe_id, ())
    mu = model["mu"][user]
    if not recipe_tags:
        return mu
    affinity = model["affinity"][user]
    return sum(affinity.get(t, mu) for t in recipe_tags) / len(recipe_tags)


def household_score(model, recipe_id):
    """score(r) and details {user: {"stars": int | None, "value": s_u(r)}} (G if there are no users)."""
    details = {}
    for u in model["users"]:
        stars = model["ratings"].get(u, {}).get(recipe_id)
        details[u] = {"stars": stars, "value": predict(model, u, recipe_id) if stars is None else stars}
    if not details:
        return model["g"], details
    return sum(d["value"] for d in details.values()) / len(details), details


def is_vetoed(model, recipe_id):
    """Some user rated the recipe 0 ("Nie wieder")."""
    return any(rated.get(recipe_id) == 0 for rated in model["ratings"].values())


# ---- week planning (M7) ----

WEEK_RE = re.compile(r"\d{4}-W\d{2}", re.ASCII)
MEALS = ("lunch", "dinner")
TEMPERATURE = 0.5  # weight = exp((score - best) / TEMPERATURE)


def week_dates(week):
    """The 7 dates (Monday-Sunday) of an ISO week 'YYYY-Www'; ValueError if malformed or the week does not exist."""
    if not isinstance(week, str) or not WEEK_RE.fullmatch(week):
        raise ValueError(week)
    monday = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    return [monday + timedelta(days=i) for i in range(7)]


def week_of(day):
    year, number, _ = day.isocalendar()
    return f"{year:04d}-W{number:02d}"


def history(conn, exclude_week):
    """[(recipe_id, date)] of the active, non-skipped, filled slots of all other confirmed plans."""
    return [(r["recipe_id"], week_dates(r["week"])[r["day"]]) for r in conn.execute(
        "SELECT plan_slots.week, day, recipe_id FROM plan_slots JOIN plans ON plans.week = plan_slots.week "
        "WHERE plans.status = 'confirmed' AND plan_slots.week != ? AND active = 1 AND skipped = 0 "
        "AND recipe_id IS NOT NULL", (exclude_week,))]


def slot_order(slot):
    return slot["day"], MEALS.index(slot["meal"])


def _is_rated(model, recipe_id):
    return any(recipe_id in rated for rated in model["ratings"].values())


def top_tags(model, recipe_id, n=3):
    """Up to n tags of the recipe with the highest affinity for the household (ties by name)."""
    users = model["users"]

    def affinity(tag):
        if not users:
            return model["g"]
        return sum(model["affinity"][u].get(tag, model["mu"][u]) for u in users) / len(users)

    return sorted(model["tags"].get(recipe_id, ()), key=lambda t: (-affinity(t), t))[:n]


def _reason(model, recipe_id, cooked):
    """Suggestion reason (section 6): rated, predicted (cooked before) or new."""
    kind = "rated" if _is_rated(model, recipe_id) else "predicted" if recipe_id in cooked else "new"
    return {"kind": kind, "score": round(household_score(model, recipe_id)[0], 1), "tags": top_tags(model, recipe_id)}


def _candidates(slot, dates, recipes, model, history, used, window):
    """Recipe ids that may fill the slot: not archived, matching meal, not vetoed, not used this week, not cooked recently."""
    suits = "for_lunch" if slot["meal"] == "lunch" else "for_dinner"
    day = dates[slot["day"]]
    recent = {rid for rid, d in history if abs((d - day).days) < window}
    return sorted(r["id"] for r in recipes if not r["archived"] and r[suits] and r["id"] not in used
                  and r["id"] not in recent and not is_vetoed(model, r["id"]))


def _pick(pool, model, rng):
    scores = [household_score(model, rid)[0] for rid in pool]
    best = max(scores)
    return rng.choices(pool, [math.exp((s - best) / TEMPERATURE) for s in scores])[0]


def _used(slots, skip=()):
    return {s["recipe_id"] for s in slots if s["active"] and s["recipe_id"] is not None and (s["day"], s["meal"]) not in skip}


def generate(plan, recipes, ratings, users, history, settings, rng):
    """Fill all active, unlocked, non-skipped slots (Monday to Sunday, lunch first); returns the new slot list."""
    slots = [dict(s) for s in plan["slots"]]
    dates = week_dates(plan["week"])
    model = build_model(users, ratings, {r["id"]: r["tags"] for r in recipes})
    cooked = {rid for rid, _ in history}
    todo = sorted((s for s in slots if s["active"] and not s["locked"] and not s["skipped"]), key=slot_order)
    used = _used(slots, {(s["day"], s["meal"]) for s in todo})
    wants_new = set(rng.sample(range(len(todo)), min(settings["new_per_week"], len(todo))))
    for i, slot in enumerate(todo):
        pool = _candidates(slot, dates, recipes, model, history, used, settings["repeat_window_days"])
        fresh = [rid for rid in pool if not _is_rated(model, rid) and rid not in cooked]
        known = [rid for rid in pool if rid not in fresh]
        pool = (fresh or known) if i in wants_new else (known or fresh)
        if not pool:
            slot.update(recipe_id=None, reason={"kind": "none"})
            continue
        slot["recipe_id"] = _pick(pool, model, rng)
        slot["reason"] = _reason(model, slot["recipe_id"], cooked)
        used.add(slot["recipe_id"])
    return slots


def reroll(plan, day, meal, recipes, ratings, users, history, settings, rng):
    """Same rules for one slot, excluding its current recipe; without an alternative the slot is unchanged."""
    slots = [dict(s) for s in plan["slots"]]
    slot = next(s for s in slots if s["day"] == day and s["meal"] == meal)
    model = build_model(users, ratings, {r["id"]: r["tags"] for r in recipes})
    pool = _candidates(slot, week_dates(plan["week"]), recipes, model, history,
                       _used(slots, {(day, meal)}) | {slot["recipe_id"]}, settings["repeat_window_days"])
    if pool:
        slot["recipe_id"] = _pick(pool, model, rng)
        slot["reason"] = _reason(model, slot["recipe_id"], {rid for rid, _ in history})
    return slots


NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g")


def day_totals(slots, nutrition):
    """Per day (0-6): per-portion nutrients summed over active, non-skipped, filled slots.
    nutrition: {recipe_id: {kcal, protein_g, fat_g, carbs_g, source} or None}.
    estimated = some value is an AI estimate, incomplete = some recipe has no nutrition."""
    totals = [{**dict.fromkeys(NUTRIENTS, 0), "estimated": False, "incomplete": False} for _ in range(7)]
    for s in slots:
        if not s["active"] or s["skipped"] or s["recipe_id"] is None:
            continue
        total, n = totals[s["day"]], nutrition.get(s["recipe_id"])
        if not n:
            total["incomplete"] = True
            continue
        for key in NUTRIENTS:
            total[key] += n.get(key) or 0
        total["estimated"] |= n.get("source") == "ai"
    for total in totals:
        for key in NUTRIENTS:
            total[key] = round(total[key], 1)
    return totals
