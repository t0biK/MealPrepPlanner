import random
import tempfile
import unittest
from datetime import date, timedelta

from mealprep import db, planner

WEEK = "2026-W41"  # Monday 2026-10-05
MONDAY = date(2026, 10, 5)
SETTINGS = {"repeat_window_days": 14, "new_per_week": 2}
USERS = ["u"]


def recipe(rid, lunch=True, dinner=True, archived=False, tags=()):
    return {"id": rid, "for_lunch": lunch, "for_dinner": dinner, "archived": archived, "tags": list(tags)}


def make_plan(active=None, overrides=None):
    """14 empty slots; `active` = set of (day, meal) that are switched on (default: all)."""
    slots = [{"day": d, "meal": m, "active": active is None or (d, m) in active, "recipe_id": None, "portions": 2,
              "locked": False, "skipped": False, "reason": None} for d in range(7) for m in planner.MEALS]
    for (d, m), values in (overrides or {}).items():
        next(s for s in slots if (s["day"], s["meal"]) == (d, m)).update(values)
    return {"week": WEEK, "status": "draft", "slots": slots}


def slot(slots, day, meal):
    return next(s for s in slots if (s["day"], s["meal"]) == (day, meal))


def gen(plan, recipes, ratings=None, history=(), settings=SETTINGS, seed=1, today=MONDAY):
    return planner.generate(plan, recipes, ratings or {}, USERS, list(history), settings, random.Random(seed), today)


def rated(*ids, stars=3):
    return {"u": {rid: stars for rid in ids}}


class WeekTest(unittest.TestCase):
    def test_week_dates(self):
        d = planner.week_dates("2026-W01")
        self.assertEqual((d[0], d[6]), (date(2025, 12, 29), date(2026, 1, 4)))
        self.assertEqual(len(d), 7)
        d = planner.week_dates("2026-W53")  # 2026 has 53 ISO weeks
        self.assertEqual((d[0], d[6]), (date(2026, 12, 28), date(2027, 1, 3)))
        self.assertEqual(planner.week_dates("2027-W01")[0], date(2027, 1, 4))

    def test_invalid_weeks_rejected(self):
        for bad in ("2027-W53", "2026-W54", "2026-W00", "2026-W1", "26-W01", "2026-01", " 2026-W01", "2026-W01\n",
                    "2026-w01", "", None, 202601):
            with self.assertRaises(ValueError, msg=bad):
                planner.week_dates(bad)

    def test_week_of(self):
        self.assertEqual(planner.week_of(date(2026, 1, 1)), "2026-W01")
        self.assertEqual(planner.week_of(date(2027, 1, 3)), "2026-W53")
        self.assertEqual(planner.week_of(date(2026, 10, 11)), "2026-W41")
        self.assertEqual(planner.week_of(date(2026, 10, 12)), "2026-W42")


class CandidateRulesTest(unittest.TestCase):
    def only(self, recipes, ratings=None, history=(), settings=SETTINGS, meal="lunch"):
        """Ids picked for Monday's slot when exactly that slot is active (a pool of several = several runs)."""
        picked = set()
        for seed in range(30):
            out = gen(make_plan({(0, meal)}), recipes, ratings, history, settings, seed)
            picked.add(slot(out, 0, meal)["recipe_id"])
        return picked

    def test_archived_excluded(self):
        self.assertEqual(self.only([recipe(1, archived=True), recipe(2)]), {2})

    def test_meal_suitability(self):
        recipes = [recipe(1, lunch=False), recipe(2, dinner=False), recipe(3)]
        self.assertEqual(self.only(recipes, meal="lunch"), {2, 3})
        self.assertEqual(self.only(recipes, meal="dinner"), {1, 3})

    def test_vetoed_excluded_even_by_one_user(self):
        ratings = {"u": {1: 5, 2: 0}, "other": {2: 5}}
        self.assertEqual(self.only([recipe(1), recipe(2)], ratings), {1})

    def test_no_recipe_twice_in_a_week(self):
        out = gen(make_plan(), [recipe(i) for i in range(1, 21)])
        ids = [s["recipe_id"] for s in out]
        self.assertNotIn(None, ids)
        self.assertEqual(len(set(ids)), 14)

    def test_repeat_window(self):
        recipes = [recipe(1), recipe(2)]
        cooked = lambda days: [(1, MONDAY + timedelta(days=days))]  # relative to the slot date (Monday)
        self.assertEqual(self.only(recipes, rated(1, 2), history=cooked(-13)), {2})  # < 14 days before
        self.assertEqual(self.only(recipes, rated(1, 2), history=cooked(13)), {2})  # < 14 days after
        self.assertEqual(self.only(recipes, rated(1, 2), history=cooked(-14)), {1, 2})  # exactly 14: allowed
        self.assertEqual(self.only(recipes, rated(1, 2), history=cooked(14)), {1, 2})
        self.assertEqual(self.only(recipes, rated(1, 2), history=cooked(-1), settings={**SETTINGS, "repeat_window_days": 0}), {1, 2})

    def test_locked_inactive_skipped_untouched(self):
        keep = {"recipe_id": 99, "reason": {"kind": "manual"}}
        plan = make_plan({(d, m) for d in range(7) for m in planner.MEALS} - {(1, "lunch")},
                         {(0, "lunch"): {**keep, "locked": True}, (0, "dinner"): {**keep, "skipped": True}})
        before = [dict(s) for s in plan["slots"]]
        out = gen(plan, [recipe(i) for i in range(1, 30)])
        self.assertEqual(plan["slots"], before)  # input untouched
        self.assertEqual(slot(out, 0, "lunch"), slot(before, 0, "lunch"))
        self.assertEqual(slot(out, 0, "dinner"), slot(before, 0, "dinner"))
        self.assertEqual(slot(out, 1, "lunch"), slot(before, 1, "lunch"))  # inactive
        self.assertIsNone(slot(out, 1, "lunch")["recipe_id"])
        self.assertEqual(sum(s["recipe_id"] is not None for s in out), 11 + 2)
        # a locked recipe counts as used in the week
        self.assertEqual(sum(s["recipe_id"] == 99 for s in out), 2)

    def test_past_slots_of_a_confirmed_plan_are_protected(self):
        past = {(0, "lunch"): {"recipe_id": 99, "reason": {"kind": "manual"}}}  # Monday; the empty Monday dinner is past too
        recipes = [recipe(i) for i in range(1, 30)]
        today = MONDAY + timedelta(days=1)
        plan = {**make_plan({(0, "lunch"), (0, "dinner"), (1, "lunch")}, past), "status": "confirmed"}
        out = gen(plan, recipes, today=today)
        self.assertEqual(out[:2], plan["slots"][:2])  # untouched, the empty one stays empty
        self.assertEqual(planner.protected(plan, today), {(0, "lunch"), (0, "dinner")})
        self.assertIsNotNone(slot(out, 1, "lunch")["recipe_id"])  # today is not protected
        self.assertNotEqual(slot(out, 1, "lunch")["recipe_id"], 99)
        draft = {**plan, "status": "draft"}
        self.assertEqual(planner.protected(draft, today), set())
        self.assertIsNotNone(slot(gen(draft, recipes, today=today), 0, "dinner")["recipe_id"])
        # a past recipe still counts as used in the week
        only = make_plan({(0, "lunch"), (1, "lunch")}, {(0, "lunch"): {"recipe_id": 1}})
        only["status"] = "confirmed"
        self.assertIsNone(slot(gen(only, [recipe(1)], today=today), 1, "lunch")["recipe_id"])

    def test_locked_recipe_not_picked_again(self):
        plan = make_plan({(0, "lunch"), (0, "dinner")}, {(0, "lunch"): {"recipe_id": 1, "locked": True}})
        for seed in range(20):
            self.assertEqual(slot(gen(plan, [recipe(1), recipe(2)], seed=seed), 0, "dinner")["recipe_id"], 2)

    def test_deterministic_with_seed(self):
        recipes = [recipe(i) for i in range(1, 31)]
        ratings = {"u": {i: i % 6 for i in range(1, 31)}}
        a = gen(make_plan(), recipes, ratings, seed=7)
        self.assertEqual(a, gen(make_plan(), recipes, ratings, seed=7))
        self.assertNotEqual([s["recipe_id"] for s in a], [s["recipe_id"] for s in gen(make_plan(), recipes, ratings, seed=8)])

    def test_better_scores_are_picked_more_often(self):
        wins = {1: 0, 2: 0}
        for seed in range(200):
            out = gen(make_plan({(0, "lunch")}), [recipe(1), recipe(2)], {"u": {1: 5, 2: 3}}, seed=seed,
                      settings={"repeat_window_days": 0, "new_per_week": 0})
            wins[slot(out, 0, "lunch")["recipe_id"]] += 1
        self.assertGreater(wins[1], wins[2] * 5)  # weight ratio exp(4) ~ 55


class NewQuotaTest(unittest.TestCase):
    def kinds(self, recipes, ratings, history=(), new_per_week=2, seed=3):
        out = gen(make_plan(), recipes, ratings, history, {**SETTINGS, "new_per_week": new_per_week}, seed)
        return [s["reason"]["kind"] for s in out]

    def test_quota_met_exactly_when_enough_new_recipes(self):
        recipes = [recipe(i) for i in range(1, 41)]
        ratings = rated(*range(1, 21))  # 1-20 known, 21-40 new
        for seed in range(10):
            self.assertEqual(self.kinds(recipes, ratings, seed=seed).count("new"), 2)
        self.assertEqual(self.kinds(recipes, ratings, new_per_week=5).count("new"), 5)
        self.assertEqual(self.kinds(recipes, ratings, new_per_week=0).count("new"), 0)

    def test_fewer_new_recipes_than_quota(self):
        recipes = [recipe(i) for i in range(1, 21)]
        self.assertEqual(self.kinds(recipes, rated(*range(1, 20))).count("new"), 1)  # only recipe 20 is new

    def test_no_known_recipes_falls_back_to_new(self):
        recipes = [recipe(i) for i in range(1, 15)]
        self.assertEqual(self.kinds(recipes, {}, new_per_week=0), ["new"] * 14)

    def test_cooked_recipe_is_not_new(self):
        hist = [(1, date(2020, 1, 1))]  # long ago: allowed again, but cooked before
        out = gen(make_plan({(0, "lunch")}), [recipe(1)], {}, hist, {**SETTINGS, "new_per_week": 0})
        self.assertEqual(slot(out, 0, "lunch")["reason"]["kind"], "predicted")

    def test_quota_never_exceeds_slots_to_fill(self):
        out = gen(make_plan({(0, "lunch")}), [recipe(1)], {}, (), {**SETTINGS, "new_per_week": 14})
        self.assertEqual(slot(out, 0, "lunch")["reason"]["kind"], "new")


class ReasonTest(unittest.TestCase):
    def test_reason_kinds_scores_and_tags(self):
        recipes = [recipe(1, tags=["A", "B", "C", "D"]), recipe(2), recipe(3)]
        ratings = {"u": {1: 4}}
        out = gen(make_plan({(0, "lunch")}), [recipes[0]], ratings)
        reason = slot(out, 0, "lunch")["reason"]
        self.assertEqual((reason["kind"], reason["score"]), ("rated", 4.0))
        self.assertEqual(len(reason["tags"]), 3)
        self.assertTrue(set(reason["tags"]) <= {"A", "B", "C", "D"})
        out = gen(make_plan({(0, "lunch")}), [recipes[1]], ratings, [(2, date(2020, 1, 1))])
        self.assertEqual(slot(out, 0, "lunch")["reason"]["kind"], "predicted")
        out = gen(make_plan({(0, "lunch")}), [recipes[2]], ratings)
        self.assertEqual(slot(out, 0, "lunch")["reason"]["kind"], "new")

    def test_empty_pool_gives_reason_none(self):
        out = gen(make_plan({(0, "lunch"), (0, "dinner")}), [recipe(1, archived=True)])
        for meal in planner.MEALS:
            self.assertEqual(slot(out, 0, meal)["recipe_id"], None)
            self.assertEqual(slot(out, 0, meal)["reason"], {"kind": "none"})

    def test_more_slots_than_recipes_leaves_some_empty(self):
        out = gen(make_plan(), [recipe(i) for i in range(1, 6)])
        self.assertEqual(sum(s["recipe_id"] is not None for s in out), 5)
        self.assertEqual([s["reason"]["kind"] for s in out].count("none"), 9)

    def test_top_tags_prefer_liked_tags(self):
        model = planner.build_model(["u"], {"u": {1: 5, 2: 5, 3: 0}}, {1: ["Liked"], 2: ["Liked"], 3: ["Hated"], 4: ["Hated", "Liked", "Other"]})
        self.assertEqual(planner.top_tags(model, 4, 2), ["Liked", "Other"])
        self.assertEqual(planner.top_tags(planner.build_model([], {}, {}), 4), [])


class RerollTest(unittest.TestCase):
    def test_reroll_returns_a_different_recipe(self):
        plan = make_plan({(0, "lunch")}, {(0, "lunch"): {"recipe_id": 1}})
        recipes = [recipe(1), recipe(2), recipe(3)]
        for seed in range(20):
            out = planner.reroll(plan, 0, "lunch", recipes, {}, USERS, [], SETTINGS, random.Random(seed))
            self.assertIn(slot(out, 0, "lunch")["recipe_id"], (2, 3))
            self.assertIn(slot(out, 0, "lunch")["reason"]["kind"], ("new", "predicted", "rated"))
        self.assertEqual(plan["slots"][0]["recipe_id"], 1)  # input untouched

    def test_reroll_avoids_recipes_of_other_slots(self):
        plan = make_plan({(0, "lunch"), (0, "dinner")}, {(0, "lunch"): {"recipe_id": 1}, (0, "dinner"): {"recipe_id": 2}})
        for seed in range(20):
            out = planner.reroll(plan, 0, "lunch", [recipe(i) for i in (1, 2, 3)], {}, USERS, [], SETTINGS, random.Random(seed))
            self.assertEqual(slot(out, 0, "lunch")["recipe_id"], 3)

    def test_reroll_without_alternative_keeps_the_slot(self):
        plan = make_plan({(0, "lunch")}, {(0, "lunch"): {"recipe_id": 1, "reason": {"kind": "manual"}}})
        out = planner.reroll(plan, 0, "lunch", [recipe(1)], {}, USERS, [], SETTINGS, random.Random(1))
        self.assertEqual(slot(out, 0, "lunch")["recipe_id"], 1)
        self.assertEqual(slot(out, 0, "lunch")["reason"], {"kind": "manual"})


class DayTotalsTest(unittest.TestCase):
    NUTRITION = {
        1: {"kcal": 500, "protein_g": 20, "fat_g": 10, "carbs_g": 60, "source": "page"},
        2: {"kcal": 300.5, "protein_g": None, "fat_g": 5, "carbs_g": 40, "source": "ai"},
        3: None,
    }

    def totals(self, overrides):
        slots = make_plan(None, overrides)["slots"]
        return planner.day_totals(slots, self.NUTRITION)

    def test_sums_per_day(self):
        t = self.totals({(0, "lunch"): {"recipe_id": 1}, (0, "dinner"): {"recipe_id": 1}})
        self.assertEqual(t[0], {"kcal": 1000, "protein_g": 40, "fat_g": 20, "carbs_g": 120, "estimated": False, "incomplete": False})
        self.assertEqual(t[1], {"kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0, "estimated": False, "incomplete": False})
        self.assertEqual(len(t), 7)

    def test_estimated_and_incomplete_flags(self):
        t = self.totals({(0, "lunch"): {"recipe_id": 1}, (0, "dinner"): {"recipe_id": 2}, (1, "lunch"): {"recipe_id": 3},
                           (1, "dinner"): {"recipe_id": 1}})
        self.assertEqual((t[0]["kcal"], t[0]["protein_g"], t[0]["estimated"], t[0]["incomplete"]), (800.5, 20, True, False))
        self.assertEqual((t[1]["kcal"], t[1]["estimated"], t[1]["incomplete"]), (500, False, True))

    def test_inactive_and_skipped_slots_do_not_count(self):
        t = self.totals({(0, "lunch"): {"recipe_id": 1, "active": False}, (0, "dinner"): {"recipe_id": 1, "skipped": True},
                           (1, "lunch"): {"recipe_id": 3, "active": False}})
        self.assertEqual((t[0]["kcal"], t[1]["incomplete"]), (0, False))


class HistoryTest(unittest.TestCase):
    def test_history_of_other_confirmed_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(tmp)
            try:
                db.migrate(conn)
                now = "2026-01-01T00:00:00"
                with conn:
                    conn.execute("INSERT INTO users (id, name, display_name, first_seen, last_seen) VALUES ('u', 'u', 'u', ?, ?)", (now, now))
                    for rid in (1, 2, 3, 4):
                        conn.execute("INSERT INTO recipes (id, title, source_kind, servings, created_at, updated_at) "
                                     "VALUES (?, 'r', 'manual', 2, ?, ?)", (rid, now, now))
                    conn.executemany("INSERT INTO plans (week, status) VALUES (?, ?)",
                                     [("2026-W40", "confirmed"), ("2026-W41", "confirmed"), ("2026-W42", "draft")])
                    conn.executemany(
                        "INSERT INTO plan_slots (week, day, meal, active, recipe_id, portions, skipped) VALUES (?, ?, ?, ?, ?, 2, ?)",
                        [("2026-W40", 2, "lunch", 1, 1, 0),   # counts: Wednesday 2026-09-30
                         ("2026-W40", 3, "dinner", 1, 2, 1),  # skipped
                         ("2026-W40", 4, "dinner", 0, 3, 0),  # switched off
                         ("2026-W40", 5, "lunch", 1, None, 0),  # empty
                         ("2026-W41", 0, "lunch", 1, 4, 0),   # the week being planned
                         ("2026-W42", 0, "lunch", 1, 4, 0)])  # draft
                self.assertEqual(planner.history(conn, "2026-W41"), [(1, date(2026, 9, 30))])
                self.assertEqual(sorted(planner.history(conn, "2026-W40")), [(4, date(2026, 10, 5))])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
