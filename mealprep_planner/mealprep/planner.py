"""Taste prediction (M6) and week planning (M7): pure functions on plain data (history() is the only DB read).

ratings: {user_id: {recipe_id: stars 0-5}}   tags: {recipe_id: [tag names]}   users: [user_id]
recipes: [{id, archived, tags, kcal, slots}]   (slots: 14 bools, index = day * 2 + 0 lunch / 1 dinner, absent = all)   history: [(recipe_id, date)]
slot: {day 0-6, meal, active, recipe_id, eaters [user_id], guests, locked, skipped, reason, leftover ((day, meal) of the source | None)}   plan: {week, slots: [slot x 14], canteen}
M14: a leftover slot has no recipe of its own; its recipe_id is its source's (resolve_leftovers), so every function below sees what is eaten.
M12: people: {user_id: {kcal_target, protein_target_g, canteen_kcal}}   canteen: {day: [user_id]}   nutrition: {recipe_id: {kcal, ...} | None}
dated slot (sensor_payload): {date, meal, title}
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
ALL_SLOTS = [True] * 14
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


def _reason(model, recipe_id, cooked, fit_label=None):
    """Suggestion reason (section 6): rated, predicted (cooked before) or new; `fit` only when an eater has a target."""
    kind = "rated" if _is_rated(model, recipe_id) else "predicted" if recipe_id in cooked else "new"
    return {"kind": kind, "score": round(household_score(model, recipe_id)[0], 1), "tags": top_tags(model, recipe_id),
            **({"fit": fit_label} if fit_label else {})}


def _candidates(slot, dates, recipes, model, history, used, window):
    """Recipe ids that may fill the slot: not archived, not vetoed, not used this week, not cooked recently, and allowed in
    this slot by all of its categories (M15; a recipe without a category fits every slot)."""
    day = dates[slot["day"]]
    index = slot["day"] * 2 + MEALS.index(slot["meal"])
    recent = {rid for rid, d in history if abs((d - day).days) < window}
    return sorted(r["id"] for r in recipes if not r["archived"] and r.get("slots", ALL_SLOTS)[index]
                  and r["id"] not in used and r["id"] not in recent and not is_vetoed(model, r["id"]))


def _pick(pool, model, rng, misfit):
    """Weighted random pick; the score of a recipe is lowered by its calorie misfit share (M12)."""
    scores = [household_score(model, rid)[0] - misfit[rid] for rid in pool]
    best = max(scores)
    return rng.choices(pool, [math.exp((s - best) / TEMPERATURE) for s in scores])[0]


def resolve_leftovers(slots):
    """Copies of the slots in which every leftover slot has the recipe of its source (the source is never a leftover)."""
    by_key = {(s["day"], s["meal"]): s for s in slots}
    return [{**s, "recipe_id": by_key[s["leftover"]]["recipe_id"]} if s.get("leftover") else dict(s) for s in slots]


def _used(slots, skip=()):
    """Recipes of the active filled slots; a leftover slot adds nothing of its own (its source is counted)."""
    return {s["recipe_id"] for s in slots if s["active"] and s["recipe_id"] is not None and not s.get("leftover")
            and (s["day"], s["meal"]) not in skip}


def protected(plan, today):
    """{(day, meal)} of a confirmed plan's past slots (date < today): cooked, so they are never changed."""
    dates = week_dates(plan["week"])
    return {(s["day"], s["meal"]) for s in plan["slots"] if dates[s["day"]] < today} if plan["status"] == "confirmed" else set()


def _fill(slot, pool, slots, plan, kcal, model, cooked, rng, people):
    """Pick a recipe of the pool for the slot, weighted by score minus the calorie misfit; sets recipe_id and reason."""
    fits = {rid: fit(slots, slot, kcal.get(rid), people, plan.get("canteen", {})) for rid in pool}
    slot["recipe_id"] = _pick(pool, model, rng, {rid: f[1] for rid, f in fits.items()})
    slot["reason"] = _reason(model, slot["recipe_id"], cooked, fits[slot["recipe_id"]][0])


def generate(plan, recipes, ratings, users, history, settings, rng, today, people=None):
    """Fill all active, unlocked, non-skipped, non-leftover slots (Monday to Sunday, lunch first); returns the new slot list.
    Past slots of a confirmed plan stay as they are but still count as used; leftover slots follow their source."""
    slots = resolve_leftovers(plan["slots"])
    dates = week_dates(plan["week"])
    model = build_model(users, ratings, {r["id"]: r["tags"] for r in recipes})
    kcal = {r["id"]: r.get("kcal") for r in recipes}
    cooked = {rid for rid, _ in history}
    past = protected(plan, today)
    todo = sorted((s for s in slots if s["active"] and not s["locked"] and not s["skipped"] and not s.get("leftover")
                   and (s["day"], s["meal"]) not in past), key=slot_order)
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
        _fill(slot, pool, slots, plan, kcal, model, cooked, rng, people or {})
        used.add(slot["recipe_id"])
    return resolve_leftovers(slots)


def reroll(plan, day, meal, recipes, ratings, users, history, settings, rng, people=None):
    """Same rules for one slot, excluding its current recipe; without an alternative (or on a leftover slot) the slot is
    unchanged. Leftover slots follow the new recipe."""
    slots = resolve_leftovers(plan["slots"])
    slot = next(s for s in slots if s["day"] == day and s["meal"] == meal)
    if slot.get("leftover"):
        return slots
    model = build_model(users, ratings, {r["id"]: r["tags"] for r in recipes})
    pool = _candidates(slot, week_dates(plan["week"]), recipes, model, history,
                       _used(slots, {(day, meal)}) | {slot["recipe_id"]}, settings["repeat_window_days"])
    if pool:
        _fill(slot, pool, slots, plan, {r["id"]: r.get("kcal") for r in recipes}, model, {rid for rid, _ in history}, rng,
              people or {})
    return resolve_leftovers(slots)


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


# ---- personal portions (M12) ----

STEP, MIN_FACTOR, MAX_FACTOR = 0.25, 0.5, 2.0
FIT_RANGE = (0.75, 1.5)  # a recipe fits when (per-meal calorie budget) / (its kcal) lies in this range


def _kcal(nutrition, recipe_id):
    """kcal per portion, None when the recipe has none (0 counts as none)."""
    return (nutrition.get(recipe_id) or {}).get("kcal") or None


def _canteen_kcal(canteen, people, user, day):
    return people[user]["canteen_kcal"] if user in canteen.get(day, ()) else 0


def _meals(slots, day, user):
    """Recipe ids of the active, non-skipped, filled slots of the day the person eats."""
    return [s["recipe_id"] for s in slots if s["day"] == day and s["active"] and not s["skipped"]
            and s["recipe_id"] is not None and user in s["eaters"]]


def round_step(x):
    """Nearest multiple of STEP, halves up (not the banker's rounding of round())."""
    return math.floor(x / STEP + 0.5) * STEP


def personal_factors(slots, canteen, people, nutrition):
    """{(user_id, day): portion factor} for every eater with a kcal target whose meals of the day all have kcal:
    (target - canteen kcal) / sum of the meals' kcal, rounded to STEP and limited to MIN_FACTOR..MAX_FACTOR.
    Anyone else (no target, no meals, a meal without kcal) is missing = factor 1."""
    out = {}
    for user in {u for s in slots for u in s["eaters"]}:
        target = people.get(user, {}).get("kcal_target")
        for day in range(7):
            kcals = [_kcal(nutrition, r) for r in _meals(slots, day, user)]
            if target and kcals and all(kcals):
                budget = target - _canteen_kcal(canteen, people, user, day)
                out[user, day] = min(MAX_FACTOR, max(MIN_FACTOR, round_step(budget / sum(kcals))))
    return out


def cooked_portions(slot, factors, slots=()):
    """Portions to cook for a slot: its eaters' personal factors plus the guests, plus the same for every slot of `slots`
    marked "Reste von" it (not skipped). A leftover slot itself cooks nothing."""
    if slot.get("leftover"):
        return 0
    eating = [slot, *(t for t in slots if t.get("leftover") == (slot["day"], slot["meal"]) and not t["skipped"])]
    return sum(sum(factors.get((u, t["day"]), 1.0) for u in t["eaters"]) + t["guests"] for t in eating)


def person_day_totals(slots, canteen, people, nutrition):
    """Per day (0-6) {user_id: {kcal, kcal_target, protein_g, protein_target, canteen, incomplete}} for every person with a
    target who eats or is at the canteen that day. kcal = canteen kcal + factor * kcal of their meals; incomplete = a
    meal has no kcal (then the factor is 1). Canteen protein is unknown and not counted."""
    factors = personal_factors(slots, canteen, people, nutrition)
    days = [{} for _ in range(7)]
    for user, p in people.items():
        if not (p["kcal_target"] or p["protein_target_g"]):
            continue
        for day in range(7):
            meals, at_canteen = _meals(slots, day, user), user in canteen.get(day, ())
            if not meals and not at_canteen:
                continue
            f = factors.get((user, day), 1.0)
            days[day][user] = {
                "kcal": round(_canteen_kcal(canteen, people, user, day) + f * sum(_kcal(nutrition, r) or 0 for r in meals), 1),
                "kcal_target": p["kcal_target"],
                "protein_g": round(f * sum((nutrition.get(r) or {}).get("protein_g") or 0 for r in meals), 1),
                "protein_target": p["protein_target_g"],
                "canteen": _canteen_kcal(canteen, people, user, day),
                "incomplete": not all(_kcal(nutrition, r) for r in meals)}
    return days


def fit(slots, slot, kcal, people, canteen):
    """(label, misfit share) of a recipe with `kcal` per portion in the slot. Each eater with a kcal target has a budget of
    (target - canteen kcal) / meals that day; it is a misfit when budget / kcal is outside FIT_RANGE. Label: None when no
    eater has a target, `unknown` without kcal, else `ok`, or `poor` when some eater misfits.
    Share = misfits / eaters with a target."""
    eaters = [u for u in slot["eaters"] if people.get(u, {}).get("kcal_target")]
    if not eaters:
        return None, 0
    if not kcal:
        return "unknown", 0
    misfits = 0
    for user in eaters:
        meals = max(1, sum(1 for s in slots if s["day"] == slot["day"] and s["active"] and not s["skipped"] and user in s["eaters"]))
        budget = (people[user]["kcal_target"] - _canteen_kcal(canteen, people, user, slot["day"])) / meals
        misfits += not FIT_RANGE[0] <= budget / kcal <= FIT_RANGE[1]
    return ("poor" if misfits else "ok"), misfits / len(eaters)


# ---- HA sensor (M9) ----

LUNCH_UNTIL, DINNER_UNTIL = 14, 21  # hours: the sensor shows today's lunch until 14:00, then the dinner until 21:00


def sensor_payload(slots, now):
    """(state, attributes) of sensor.essensplan (section 6). slots: dated slots (filled, active, non-skipped) that
    cover at least tomorrow and the current week. State = title of the next meal, "–" if there is none."""
    today = now.date()
    tomorrow = today + timedelta(days=1)
    titles = {(s["date"], s["meal"]): s["title"] for s in slots}
    state = next((titles[(today, meal)] for meal, until in zip(MEALS, (LUNCH_UNTIL, DINNER_UNTIL))
                  if now.hour < until and (today, meal) in titles), None)
    state = state or titles.get((tomorrow, "lunch")) or titles.get((tomorrow, "dinner")) or "–"
    week = week_of(today)
    return state[:255], {
        "friendly_name": "Essensplan",
        "icon": "mdi:silverware-fork-knife",
        "today_lunch": titles.get((today, "lunch")),
        "today_dinner": titles.get((today, "dinner")),
        "tomorrow_lunch": titles.get((tomorrow, "lunch")),
        "tomorrow_dinner": titles.get((tomorrow, "dinner")),
        "week": [{"date": s["date"].isoformat(), "meal": s["meal"], "title": s["title"]}
                 for s in sorted(slots, key=lambda s: (s["date"], MEALS.index(s["meal"]))) if week_of(s["date"]) == week],
    }
