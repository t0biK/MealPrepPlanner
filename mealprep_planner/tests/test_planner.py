import random
import tempfile
import unittest
from datetime import date, timedelta

from mealprep import db, plans, planner, recipes, shopping

WEEK = "2026-W41"  # Monday 2026-10-05
MONDAY = date(2026, 10, 5)
SETTINGS = {"repeat_window_days": 14, "new_per_week": 2}
USERS = ["u"]


def recipe(rid, archived=False, tags=(), slots=None):
    return {"id": rid, "archived": archived, "tags": list(tags), **({"slots": slots} if slots else {})}


def make_plan(active=None, overrides=None):
    """14 empty slots; `active` = set of (day, meal) that are switched on (default: all)."""
    slots = [{"day": d, "meal": m, "active": active is None or (d, m) in active, "recipe_id": None, "eaters": [], "guests": 0,
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

    def test_a_recipe_without_a_category_fits_lunch_and_dinner(self):
        recipes = [{**recipe(1), "for_lunch": False}, {**recipe(2), "for_dinner": False}, recipe(3)]  # the old flags restrict nothing
        self.assertEqual(self.only(recipes, meal="lunch"), {1, 2, 3})
        self.assertEqual(self.only(recipes, meal="dinner"), {1, 2, 3})

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
                        "INSERT INTO plan_slots (week, day, meal, active, recipe_id, skipped) VALUES (?, ?, ?, ?, ?, ?)",
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


class EatersTest(unittest.TestCase):
    """M11: who eats a slot, guests, cooked portions (plans.py on a real DB)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        for uid, name in (("a", "Anna"), ("b", "Ben"), ("c", "Cleo")):
            db.upsert_user(self.conn, {"id": uid, "name": uid, "display_name": name})
        db.set_household(self.conn, "c", {"eats": False})
        self.settings = {**db.get_settings(self.conn), "slot_pattern": [i != 1 for i in range(14)]}  # Monday dinner off

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def slots(self, week=WEEK):
        return {(s["day"], s["meal"]): s for s in plans.load_plan(self.conn, week, self.settings)["slots"]}

    def act(self, day, meal, **body):
        plans.slot_action(self.conn, WEEK, day, meal, body, self.settings, random.Random(1), MONDAY)
        return self.slots()[(day, meal)]

    def test_new_week_puts_exactly_the_participants_on_every_active_slot(self):
        slots = self.slots()
        self.assertEqual({tuple(s["eaters"]) for k, s in slots.items() if s["active"]}, {("a", "b")})
        self.assertEqual(slots[(0, "dinner")]["eaters"], [])
        self.assertEqual({(s["guests"], planner.cooked_portions(s, {})) for s in slots.values() if s["active"]}, {(0, 2)})

    def test_eater_and_guests_actions(self):
        s = self.act(0, "lunch", action="eater", user_id="b", on=False)
        self.assertEqual((s["eaters"], planner.cooked_portions(s, {})), (["a"], 1))
        s = self.act(0, "lunch", action="eater", user_id="c", on=True)  # a non-participant may be added by hand
        self.assertEqual(s["eaters"], ["a", "c"])
        s = self.act(0, "lunch", action="eater", user_id="c", on=True)  # idempotent
        self.assertEqual(s["eaters"], ["a", "c"])
        s = self.act(0, "lunch", action="guests", n=3)
        self.assertEqual((s["guests"], planner.cooked_portions(s, {})), (3, 5))
        for n in (0, 12):
            self.assertEqual(self.act(0, "lunch", action="guests", n=n)["guests"], n)
        for n in (-1, 13, 1.5, "1", True, None):
            with self.assertRaises(db.InvalidField) as cm:
                self.act(0, "lunch", action="guests", n=n)
            self.assertEqual(cm.exception.field, "n")
        for body, field in [({"user_id": "x", "on": True}, "user_id"), ({"user_id": 1, "on": True}, "user_id"),
                            ({"on": True}, "user_id"), ({"user_id": "a", "on": "yes"}, "on"), ({"user_id": "a"}, "on")]:
            with self.assertRaises(db.InvalidField) as cm:
                self.act(0, "lunch", action="eater", **body)
            self.assertEqual(cm.exception.field, field)
        with self.assertRaises(plans.Refused):  # a switched-off slot has no eaters
            self.act(0, "dinner", action="eater", user_id="a", on=True)

    def test_portions_action_is_gone(self):
        with self.assertRaises(db.InvalidField) as cm:
            self.act(0, "lunch", action="portions", portions=3)
        self.assertEqual(cm.exception.field, "action")

    def test_activate_re_adds_the_participants_and_deactivate_clears_them(self):
        self.act(0, "lunch", action="eater", user_id="a", on=False)
        self.act(0, "lunch", action="guests", n=2)
        s = self.act(0, "lunch", action="deactivate")
        self.assertEqual((s["active"], s["eaters"], s["guests"]), (False, [], 0))
        db.set_household(self.conn, "c", {"eats": True})
        s = self.act(0, "lunch", action="activate")
        self.assertEqual((s["active"], s["eaters"]), (True, ["a", "b", "c"]))
        s = self.act(0, "dinner", action="activate")  # the slot the pattern had switched off
        self.assertEqual(s["eaters"], ["a", "b", "c"])

    def test_eaters_survive_generate_and_a_changed_household_only_affects_new_weeks(self):
        self.act(0, "lunch", action="eater", user_id="b", on=False)
        db.set_household(self.conn, "a", {"eats": False})
        plans.generate(self.conn, WEEK, self.settings, random.Random(1), MONDAY)
        self.assertEqual(self.slots()[(0, "lunch")]["eaters"], ["a"])
        self.assertEqual(self.slots()[(1, "lunch")]["eaters"], ["a", "b"])
        self.assertEqual(self.slots("2026-W42")[(0, "lunch")]["eaters"], ["b"])


class CategorySlotsTest(unittest.TestCase):
    """M15: the slots a recipe's categories allow (`slots`, 14 bools) limit where generate and reroll put it."""
    DINNERS = [i % 2 == 1 for i in range(14)]
    SUNDAY = [i >= 12 for i in range(14)]

    def test_a_dinner_only_recipe_never_lands_in_a_lunch(self):
        recipes = [recipe(i, slots=self.DINNERS) for i in range(1, 6)] + [recipe(i) for i in range(6, 16)]
        for seed in range(20):
            out = gen(make_plan(), recipes, seed=seed)
            self.assertFalse({s["recipe_id"] for s in out if s["meal"] == "lunch"} & set(range(1, 6)), seed)
            self.assertTrue(any(s["recipe_id"] in range(1, 6) for s in out if s["meal"] == "dinner"), seed)  # it is still used

    def test_a_sunday_only_recipe_only_lands_on_sunday(self):
        recipes = [recipe(i, slots=self.SUNDAY) for i in range(1, 4)] + [recipe(i) for i in range(4, 20)]
        for seed in range(20):
            out = gen(make_plan(), recipes, seed=seed)
            self.assertEqual({s["day"] for s in out if s["recipe_id"] in (1, 2, 3)} - {6}, set(), seed)

    def test_the_allowed_slots_are_the_mask_of_the_recipe(self):
        mask = [i in (1, 3, 5) for i in range(14)]  # dinner of Monday to Wednesday (two categories intersected)
        for seed in range(20):
            out = gen(make_plan(), [recipe(1, slots=mask)], seed=seed)
            self.assertIn([(s["day"], s["meal"]) for s in out if s["recipe_id"] == 1][0], [(0, "dinner"), (1, "dinner"), (2, "dinner")])

    def test_no_candidate_leaves_the_slot_empty(self):
        out = gen(make_plan({(0, "lunch")}), [recipe(1, slots=self.DINNERS)])
        self.assertEqual((slot(out, 0, "lunch")["recipe_id"], slot(out, 0, "lunch")["reason"]), (None, {"kind": "none"}))
        self.assertIsNone(slot(gen(make_plan({(0, "lunch")}), [recipe(1, slots=[False] * 14)]), 0, "lunch")["recipe_id"])

    def test_a_recipe_without_slots_may_go_anywhere(self):
        self.assertEqual(slot(gen(make_plan({(6, "lunch")}), [recipe(1)]), 6, "lunch")["recipe_id"], 1)

    def test_reroll_respects_the_mask(self):
        recipes = [recipe(9), recipe(1, slots=self.DINNERS), recipe(2)]
        plan = make_plan({(0, "lunch")}, {(0, "lunch"): {"recipe_id": 9}})
        for seed in range(20):
            out = planner.reroll(plan, 0, "lunch", recipes, {}, USERS, [], SETTINGS, random.Random(seed))
            self.assertEqual(slot(out, 0, "lunch")["recipe_id"], 2)
        out = planner.reroll(plan, 0, "lunch", recipes[:2], {}, USERS, [], SETTINGS, random.Random(1))  # no allowed alternative
        self.assertEqual(slot(out, 0, "lunch")["recipe_id"], 9)


class PlanCategorySlotsTest(unittest.TestCase):
    """M15: category slots in plans.py on a real DB; the old slot rules are gone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        db.upsert_user(self.conn, {"id": "a", "name": "a", "display_name": "Anna"})
        self.settings = db.get_settings(self.conn)
        tag = self.conn.execute("SELECT id FROM tags WHERE name = 'Gäste'").fetchone()[0]
        recipes.update_tag(self.conn, tag, "Gäste", [i % 2 == 1 for i in range(14)])  # dinners only
        with self.conn:
            for rid in range(1, 6):  # five dinner-only recipes, ten without category
                self.conn.execute("INSERT INTO recipes (id, title, source_kind, servings, created_at, updated_at) VALUES (?, 'r', 'manual', 2, 'x', 'x')", (rid,))
                self.conn.execute("INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)", (rid, tag))
            for rid in range(6, 16):
                self.conn.execute("INSERT INTO recipes (id, title, source_kind, servings, created_at, updated_at) VALUES (?, 'r', 'manual', 2, 'x', 'x')", (rid,))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def slots(self):
        return {(s["day"], s["meal"]): s for s in plans.load_plan(self.conn, WEEK, self.settings)["slots"]}

    def test_generate_keeps_the_category_recipes_out_of_the_lunches(self):
        for seed in range(10):
            plans.generate(self.conn, WEEK, self.settings, random.Random(seed), MONDAY)
            lunches = {s["recipe_id"] for (day, meal), s in self.slots().items() if meal == "lunch"}
            self.assertFalse(lunches & set(range(1, 6)), seed)

    def test_set_by_hand_ignores_the_slots(self):
        plans.slot_action(self.conn, WEEK, 0, "lunch", {"action": "set", "recipe_id": 1}, self.settings, random.Random(1), MONDAY)
        self.assertEqual(self.slots()[(0, "lunch")]["recipe_id"], 1)

    def test_the_rule_action_and_field_are_gone(self):
        with self.assertRaises(db.InvalidField) as cm:
            plans.slot_action(self.conn, WEEK, 0, "lunch", {"action": "rule", "tag_id": None}, self.settings, random.Random(1), MONDAY)
        self.assertEqual(cm.exception.field, "action")
        self.assertTrue(all("rule" not in s for s in plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"]))
        self.assertNotIn("slot_rules", self.settings)
        self.assertNotIn("rule_tag_id", [r["name"] for r in self.conn.execute("PRAGMA table_info(plan_slots)")])


class LeftoverTest(unittest.TestCase):
    """M14: leftover slots in the pure planner."""
    PEOPLE = {u: {"kcal_target": t, "protein_target_g": None, "canteen_kcal": 700} for u, t in (("a", 1300), ("b", None))}

    def plan(self, locked=True):
        """Monday dinner = the source (recipe 1), Tuesday lunch = Reste von it, Tuesday dinner empty."""
        return make_plan({(0, "dinner"), (1, "lunch"), (1, "dinner")}, {
            (0, "dinner"): {"recipe_id": 1, "locked": locked, "eaters": ["a", "b"]},
            (1, "lunch"): {"leftover": (0, "dinner"), "eaters": ["a"], "guests": 1},
            (1, "dinner"): {"eaters": ["a"]}})

    def test_a_leftover_slot_gets_the_recipe_of_its_source(self):
        plan = self.plan()
        out = planner.resolve_leftovers(plan["slots"])
        self.assertEqual((slot(out, 1, "lunch")["recipe_id"], slot(out, 0, "dinner")["recipe_id"]), (1, 1))
        self.assertIsNone(slot(plan["slots"], 1, "lunch")["recipe_id"])  # the input is not changed

    def test_generate_skips_leftover_slots_and_counts_their_recipe_as_used(self):
        out = gen(self.plan(), [recipe(1), recipe(2)], settings={"repeat_window_days": 14, "new_per_week": 0})
        self.assertEqual(slot(out, 1, "dinner")["recipe_id"], 2)  # 1 is taken by the source and its leftover
        lunch = slot(out, 1, "lunch")
        self.assertEqual((lunch["leftover"], lunch["recipe_id"], lunch["reason"], lunch["eaters"], lunch["guests"]),
                         ((0, "dinner"), 1, None, ["a"], 1))

    def test_generate_with_an_unlocked_source_moves_the_leftover_along(self):
        for seed in range(5):
            out = gen(self.plan(locked=False), [recipe(1), recipe(2), recipe(3)], seed=seed)
            self.assertEqual(slot(out, 1, "lunch")["recipe_id"], slot(out, 0, "dinner")["recipe_id"])
            self.assertNotEqual(slot(out, 1, "dinner")["recipe_id"], slot(out, 0, "dinner")["recipe_id"])

    def test_reroll_of_the_source_moves_the_leftover_along_and_a_leftover_is_not_rerolled(self):
        plan = self.plan(locked=False)
        out = planner.reroll(plan, 0, "dinner", [recipe(1), recipe(2)], {}, USERS, [], SETTINGS, random.Random(1))
        self.assertEqual((slot(out, 0, "dinner")["recipe_id"], slot(out, 1, "lunch")["recipe_id"]), (2, 2))
        out = planner.reroll(plan, 1, "lunch", [recipe(1), recipe(2)], {}, USERS, [], SETTINGS, random.Random(1))
        self.assertEqual((slot(out, 1, "lunch")["recipe_id"], slot(out, 1, "lunch")["reason"]), (1, None))

    def test_cooked_portions_of_the_source_include_its_leftovers(self):
        slots = self.plan()["slots"]
        factors = {("a", 1): 1.5}
        self.assertEqual(planner.cooked_portions(slot(slots, 0, "dinner"), factors), 2)  # without the slot list: as in M12
        self.assertEqual(planner.cooked_portions(slot(slots, 0, "dinner"), factors, slots), 4.5)  # 2 + a's 1.5 + 1 guest
        self.assertEqual(planner.cooked_portions(slot(slots, 1, "lunch"), factors, slots), 0)  # a leftover cooks nothing
        slot(slots, 1, "lunch")["skipped"] = True  # not eaten
        self.assertEqual(planner.cooked_portions(slot(slots, 0, "dinner"), factors, slots), 2)

    def test_personal_factors_count_a_leftover_meal_with_the_kcal_of_its_source(self):
        slots = planner.resolve_leftovers(self.plan()["slots"])
        factors = planner.personal_factors(slots, {}, self.PEOPLE, {1: {"kcal": 800}})
        self.assertEqual(factors, {("a", 0): 1.75, ("a", 1): 1.75})  # 1300 / 800 = 1.625 on both days
        total = planner.person_day_totals(slots, {}, self.PEOPLE, {1: {"kcal": 800}})[1]["a"]
        self.assertEqual(total["kcal"], 1400.0)


class LeftoverPlanTest(unittest.TestCase):
    """M14: the `leftover` slot action, portions, shopping, history, today page (plans.py on a real DB)."""
    DINNER = (0, "dinner")  # the source used below; Tuesday lunch is the leftover

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        for uid, name in (("a", "Anna"), ("b", "Ben")):
            db.upsert_user(self.conn, {"id": uid, "name": uid, "display_name": name})
        self.settings = db.get_settings(self.conn)
        self.stew = self.recipe("Stew", 800, "Linsen", 200)
        self.rice = self.recipe("Rice", 450, "Reis", 100)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def recipe(self, title, kcal, ingredient, grams):
        with self.conn:
            rid = self.conn.execute(
                "INSERT INTO recipes (title, source_kind, servings, kcal, nutrition_source, created_at, updated_at) "
                "VALUES (?, 'manual', 2, ?, 'manual', 'x', 'x')", (title, kcal)).lastrowid
            self.conn.execute("INSERT INTO ingredients (recipe_id, pos, amount, unit, name) VALUES (?, 0, ?, 'g', ?)",
                              (rid, grams, ingredient))
        return rid

    def slots(self, week=WEEK):
        return {(s["day"], s["meal"]): s for s in plans.load_plan(self.conn, week, self.settings)["slots"]}

    def act(self, day, meal, today=MONDAY, **body):
        plans.slot_action(self.conn, WEEK, day, meal, body, self.settings, random.Random(1), today)
        return self.slots()[(day, meal)]

    def leftover(self, day=1, meal="lunch"):
        """Monday dinner gets the stew; the given slot (default Tuesday lunch) becomes Reste von it."""
        self.act(*self.DINNER, action="set", recipe_id=self.stew)
        return self.act(day, meal, action="leftover", from_day=0, from_meal="dinner")

    def invalid(self, field, day, meal, **body):
        before = self.slots()
        with self.assertRaises(db.InvalidField, msg=body) as cm:
            self.act(day, meal, action="leftover", **body)
        self.assertEqual(cm.exception.field, field)
        self.assertEqual(self.slots(), before)

    def test_a_leftover_shows_the_current_recipe_of_its_source_and_stores_none_of_its_own(self):
        s = self.leftover()
        self.assertEqual((s["leftover"], s["recipe_id"], s["reason"]), (self.DINNER, self.stew, None))
        self.assertEqual(tuple(self.conn.execute("SELECT recipe_id, leftover_day, leftover_meal FROM plan_slots "
                                                 "WHERE day = 1 AND meal = 'lunch'").fetchone()), (None, 0, "dinner"))
        view = plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"]
        self.assertEqual((view[2]["leftover"], view[2]["recipe"]["title"], view[2]["cooked_portions"]),
                         ({"day": 0, "meal": "dinner"}, "Stew", 0))
        self.assertIsNone(view[1]["leftover"])

    def test_the_leftover_follows_rerolls_sets_and_clears_of_its_source(self):
        self.leftover()
        self.assertEqual(self.act(*self.DINNER, action="reroll")["recipe_id"], self.rice)  # the only other recipe
        self.assertEqual(self.slots()[1, "lunch"]["recipe_id"], self.rice)
        self.act(*self.DINNER, action="set", recipe_id=self.stew)
        self.assertEqual(self.slots()[1, "lunch"]["recipe_id"], self.stew)
        self.act(*self.DINNER, action="clear")
        self.assertEqual((self.slots()[1, "lunch"]["recipe_id"], self.slots()[1, "lunch"]["leftover"]), (None, self.DINNER))  # link stays
        self.assertIsNone(plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"][2]["recipe"])

    def test_the_source_must_be_strictly_earlier_and_can_be_shared(self):
        self.act(0, "lunch", action="set", recipe_id=self.rice)
        self.assertEqual(self.act(0, "dinner", action="leftover", from_day=0, from_meal="lunch")["leftover"], (0, "lunch"))
        self.assertEqual(self.act(6, "dinner", action="leftover", from_day=0, from_meal="lunch")["leftover"], (0, "lunch"))

    def test_invalid_sources(self):
        self.invalid("from_day", 1, "lunch", from_day=0, from_meal="dinner")  # empty source
        self.act(0, "dinner", action="set", recipe_id=self.stew)
        self.invalid("from_day", 0, "dinner", from_day=0, from_meal="dinner")  # itself
        self.invalid("from_day", 0, "lunch", from_day=0, from_meal="dinner")  # later the same day
        self.act(1, "lunch", action="set", recipe_id=self.rice)
        self.invalid("from_day", 0, "dinner", from_day=1, from_meal="lunch")  # a later day
        self.act(0, "lunch", action="set", recipe_id=self.rice)
        self.act(0, "lunch", action="deactivate")  # inactive (keeps its recipe)
        self.invalid("from_day", 1, "dinner", from_day=0, from_meal="lunch")
        self.act(1, "lunch", action="leftover", from_day=0, from_meal="dinner")
        self.act(2, "lunch", action="set", recipe_id=self.rice)
        self.invalid("from_day", 2, "dinner", from_day=1, from_meal="lunch")  # a leftover of a leftover
        for bad in (7, -1, "0", True, 1.0, None):
            self.invalid("from_day", 2, "dinner", from_day=bad, from_meal="dinner")
        for bad in ("brunch", None, 1, ["dinner"]):
            self.invalid("from_meal", 2, "dinner", from_day=0, from_meal=bad)

    def test_a_source_cannot_be_a_leftover_and_an_inactive_slot_cannot_be_one(self):
        self.leftover()
        self.act(0, "lunch", action="set", recipe_id=self.rice)
        with self.assertRaises(plans.Refused):  # Monday dinner is the source of Tuesday lunch
            self.act(0, "dinner", action="leftover", from_day=0, from_meal="lunch")
        self.act(3, "lunch", action="deactivate")
        with self.assertRaises(plans.Refused):
            self.act(3, "lunch", action="leftover", from_day=0, from_meal="dinner")

    def test_null_removes_the_link_and_empties_the_slot(self):
        for body in ({"from_day": None, "from_meal": None}, {}):
            self.leftover()
            s = self.act(1, "lunch", action="leftover", **body)
            self.assertEqual((s["leftover"], s["recipe_id"], s["reason"], s["locked"]), (None, None, None, False))
            self.assertEqual(self.slots()[0, "dinner"]["recipe_id"], self.stew)  # the source is untouched

    def test_a_leftover_can_get_another_source(self):
        self.leftover()
        self.act(0, "lunch", action="set", recipe_id=self.rice)
        s = self.act(1, "lunch", action="leftover", from_day=0, from_meal="lunch")
        self.assertEqual((s["leftover"], s["recipe_id"]), ((0, "lunch"), self.rice))

    def test_reroll_set_and_clear_are_refused_on_a_leftover(self):
        self.leftover()
        for body in ({"action": "reroll"}, {"action": "set", "recipe_id": self.rice}, {"action": "clear"}):
            with self.assertRaises(plans.Refused, msg=body):
                self.act(1, "lunch", **body)
        self.assertTrue(self.act(1, "lunch", action="lock")["locked"])  # harmless; linking again resets it
        self.assertFalse(self.act(1, "lunch", action="leftover", from_day=0, from_meal="dinner")["locked"])

    def test_past_slots_of_a_confirmed_plan_are_protected(self):
        self.leftover()
        plans.confirm(self.conn, WEEK, self.settings)
        wednesday = MONDAY + timedelta(days=2)
        with self.assertRaises(plans.Refused):  # Tuesday is past: cooked, no longer changeable
            self.act(1, "lunch", today=wednesday, action="leftover", from_day=None, from_meal=None)
        with self.assertRaises(plans.Refused):
            self.act(1, "lunch", today=wednesday, action="leftover", from_day=0, from_meal="dinner")
        s = self.act(3, "lunch", today=wednesday, action="leftover", from_day=0, from_meal="dinner")
        self.assertEqual(s["leftover"], self.DINNER)

    def test_deactivating_the_source_removes_its_links(self):
        self.leftover()
        self.leftover(3, "dinner")
        self.act(*self.DINNER, action="deactivate")
        for key in ((1, "lunch"), (3, "dinner")):
            s = self.slots()[key]
            self.assertEqual((s["leftover"], s["recipe_id"], s["active"]), (None, None, True))

    def test_a_deactivated_leftover_keeps_its_link_but_cooks_nothing(self):
        self.leftover()
        self.act(1, "lunch", action="deactivate")
        s = self.slots()
        self.assertEqual((s[1, "lunch"]["leftover"], s[1, "lunch"]["eaters"]), (self.DINNER, []))
        self.assertEqual(plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"][1]["cooked_portions"], 2)  # the source's own eaters
        plans.confirm(self.conn, WEEK, self.settings)
        self.assertIsNone(plans.today_view(self.conn, "a", MONDAY + timedelta(days=1))["today"]["lunch"])

    def test_generate_leaves_leftover_slots_alone_and_counts_the_recipe(self):
        self.leftover()
        self.act(*self.DINNER, action="lock")
        plans.generate(self.conn, WEEK, self.settings, random.Random(1), MONDAY)
        slots = self.slots()
        lunch = slots[1, "lunch"]
        self.assertEqual((lunch["leftover"], lunch["recipe_id"], lunch["reason"]), (self.DINNER, self.stew, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM plan_slots WHERE recipe_id = ?", (self.stew,)).fetchone()[0], 1)
        self.assertEqual([k for k, s in slots.items() if s["recipe_id"] == self.rice and not s["leftover"]], [(0, "lunch")])
        self.act(*self.DINNER, action="unlock")
        plans.generate(self.conn, WEEK, self.settings, random.Random(2), MONDAY)  # the source may change: the leftover follows
        slots = self.slots()
        self.assertEqual(slots[1, "lunch"]["recipe_id"], slots[0, "dinner"]["recipe_id"])
        self.assertEqual(slots[1, "lunch"]["leftover"], self.DINNER)

    def test_cooked_portions_personal_factors_and_shopping(self):
        db.set_household(self.conn, "a", {"kcal_target": 1300})  # b has no target
        self.act(0, "lunch", action="set", recipe_id=self.rice)
        self.leftover()
        s = plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"]  # 0 Mon lunch, 1 Mon dinner, 2 Tue lunch
        self.assertEqual(s[2]["portions_by_user"], {"a": 1.75, "b": 1.0})  # Tuesday: 1300 / 800 kcal of the stew
        self.assertEqual((s[1]["portions_by_user"]["a"], s[1]["cooked_portions"]), (1.0, 4.75))  # 1300 / 1250; 1 + 1 + (1.75 + 1)
        self.assertEqual(s[0]["cooked_portions"], 2.0)
        amounts = {k: i["amounts"] for k, i in shopping.build_list(self.conn, WEEK).items()}
        self.assertEqual(amounts, {"linsen": {"g": 475.0}, "reis": {"g": 100.0}})  # the leftover adds nothing of its own
        self.assertEqual(plans.view(self.conn, WEEK, self.settings, MONDAY)["totals"][1]["kcal"], 800.0)  # but it is a meal of the day
        plans.confirm(self.conn, WEEK, self.settings)
        self.act(1, "lunch", today=MONDAY + timedelta(days=1), action="skip")  # not eaten: the source needs no extra portions
        self.assertEqual(plans.view(self.conn, WEEK, self.settings, MONDAY)["slots"][1]["cooked_portions"], 2.0)

    def test_a_leftover_adds_no_history_and_no_rate_entry(self):
        self.leftover()
        plans.confirm(self.conn, WEEK, self.settings)
        self.assertEqual(planner.history(self.conn, "2026-W42"), [(self.stew, MONDAY)])
        wednesday = MONDAY + timedelta(days=2)
        self.assertEqual([(x["title"], x["date"], x["meal"]) for x in plans.rate_list(self.conn, "a", wednesday)],
                         [("Stew", "2026-10-05", "dinner")])  # not Tuesday's lunch

    def test_the_today_page_and_the_sensor_show_the_recipe_of_a_leftover(self):
        self.leftover()
        plans.confirm(self.conn, WEEK, self.settings)
        tuesday = MONDAY + timedelta(days=1)
        day = plans.today_view(self.conn, "a", tuesday)["today"]
        self.assertEqual({k: day["lunch"][k] for k in ("title", "cooked_portions", "leftover", "my_portion")}, {
            "title": "Stew", "cooked_portions": 0, "leftover": True, "my_portion": 1.0})
        self.assertIsNone(day["dinner"])
        monday = plans.today_view(self.conn, "a", MONDAY)["today"]["dinner"]
        self.assertEqual((monday["cooked_portions"], "leftover" in monday), (4, False))  # 2 eaters + the leftover's 2
        self.assertEqual([(s["date"], s["meal"], s["title"]) for s in plans.sensor_slots(self.conn, tuesday)],
                         [(MONDAY, "dinner", "Stew"), (tuesday, "lunch", "Stew")])


if __name__ == "__main__":
    unittest.main()
