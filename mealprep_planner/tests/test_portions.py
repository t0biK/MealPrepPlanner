"""M12: personal portion factors, canteen, per-person totals, calorie fit (planner.py pure; plans.py/shopping.py on a real DB)."""
import random
import tempfile
import unittest
from datetime import date

from mealprep import db, plans, planner, shopping

WEEK = "2026-W41"  # Monday 2026-10-05
MONDAY = date(2026, 10, 5)
NUTRITION = {1: {"kcal": 450, "protein_g": 20}, 2: {"kcal": 800, "protein_g": 40}, 3: {"kcal": None, "protein_g": 10}, 4: None}


def person(kcal_target=None, protein_target_g=None, canteen_kcal=700):
    return {"kcal_target": kcal_target, "protein_target_g": protein_target_g, "canteen_kcal": canteen_kcal}


def day_slots(*meals, day=0, guests=0):
    """Active slots of one day: meals = (meal, recipe_id, [eaters])."""
    return [{"day": day, "meal": m, "active": True, "recipe_id": r, "eaters": list(e), "guests": guests, "locked": False,
             "skipped": False, "reason": None} for m, r, e in meals]


class FactorTest(unittest.TestCase):
    def factor(self, target, kcals, canteen_kcal=None, user="a"):
        """Factor of one person with `target` for one day of meals with the given kcal (ids 100+)."""
        nutrition = {100 + i: {"kcal": k} for i, k in enumerate(kcals)}
        slots = day_slots(*[("lunch" if i == 0 else "dinner", 100 + i, [user]) for i in range(len(kcals))])
        canteen = {} if canteen_kcal is None else {0: [user]}
        return planner.personal_factors(slots, canteen, {user: person(target, canteen_kcal=canteen_kcal or 700)}, nutrition).get((user, 0), 1.0)

    def test_worked_example(self):
        slots = day_slots(("lunch", 1, ["a", "b"]), ("dinner", 2, ["a", "b"]))
        people = {"a": person(1700), "b": person(1300)}
        factors = planner.personal_factors(slots, {}, people, NUTRITION)
        self.assertEqual((factors["a", 0], factors["b", 0]), (1.25, 1.0))  # 1700 / 1250 = 1.36, 1300 / 1250 = 1.04
        self.assertEqual(planner.cooked_portions(slots[0], factors), 2.25)
        self.assertEqual(planner.cooked_portions({**slots[0], "guests": 2}, factors), 4.25)  # guests count 1 each
        totals = planner.person_day_totals(slots, {}, people, NUTRITION)[0]
        self.assertEqual(totals["a"], {"kcal": 1562.5, "kcal_target": 1700, "protein_g": 75.0, "protein_target": None,
                                       "canteen": 0, "incomplete": False})  # 1.25 * (450 + 800), 1.25 * (20 + 40)
        self.assertEqual(totals["b"]["kcal"], 1250.0)

    def test_canteen_day(self):
        slots = day_slots(("dinner", 2, ["a"]))
        people = {"a": person(1700)}
        factors = planner.personal_factors(slots, {0: ["a"]}, people, NUTRITION)
        self.assertEqual(factors["a", 0], 1.25)  # (1700 - 700) / 800
        total = planner.person_day_totals(slots, {0: ["a"]}, people, NUTRITION)[0]["a"]
        self.assertEqual((total["kcal"], total["canteen"]), (1700.0, 700))

    def test_canteen_kcal_is_the_persons_own(self):
        people = {"a": person(1700, canteen_kcal=500)}
        factors = planner.personal_factors(day_slots(("dinner", 2, ["a"])), {0: ["a"]}, people, NUTRITION)
        self.assertEqual(factors["a", 0], 1.5)  # (1700 - 500) / 800

    def test_canteen_only_day_is_in_the_totals(self):
        total = planner.person_day_totals(day_slots(("dinner", 2, [])), {0: ["a"]}, {"a": person(1700)}, NUTRITION)[0]["a"]
        self.assertEqual((total["kcal"], total["protein_g"], total["incomplete"]), (700.0, 0.0, False))

    def test_budget_not_positive_gives_the_minimum(self):
        self.assertEqual(self.factor(600, [800], canteen_kcal=700), planner.MIN_FACTOR)
        self.assertEqual(self.factor(700, [800], canteen_kcal=700), planner.MIN_FACTOR)

    def test_clamped_to_0_5_and_2(self):
        self.assertEqual(self.factor(300, [1000]), 0.5)  # 0.3
        self.assertEqual(self.factor(5000, [600]), 2.0)  # 8.33
        self.assertEqual((self.factor(500, [1000]), self.factor(2000, [1000])), (0.5, 2.0))  # the limits themselves

    def test_rounding_to_a_quarter_halves_up(self):
        self.assertEqual(self.factor(1360, [1000]), 1.25)  # 1.36
        self.assertEqual(self.factor(1380, [1000]), 1.5)  # 1.38
        self.assertEqual(self.factor(1100, [800]), 1.5)  # 1.375: a half goes up
        self.assertEqual(self.factor(900, [800]), 1.25)  # 1.125: banker's rounding would give 1.0
        self.assertEqual(self.factor(1000, [800]), 1.25)  # 1.25 exactly
        self.assertEqual(planner.round_step(0.875), 1.0)

    def test_no_target_gives_one(self):
        slots = day_slots(("lunch", 1, ["a"]))
        self.assertEqual(planner.personal_factors(slots, {}, {"a": person()}, NUTRITION), {})
        self.assertEqual(planner.cooked_portions(slots[0], {}), 1.0)

    def test_missing_kcal_gives_one_and_incomplete(self):
        slots = day_slots(("lunch", 1, ["a"]), ("dinner", 3, ["a"]))  # recipe 3 has no kcal
        people = {"a": person(1700, 100)}
        self.assertEqual(planner.personal_factors(slots, {}, people, NUTRITION), {})
        total = planner.person_day_totals(slots, {}, people, NUTRITION)[0]["a"]
        self.assertEqual((total["kcal"], total["protein_g"], total["incomplete"]), (450.0, 30.0, True))  # factor 1
        for nutrition in ({1: {"kcal": 450}, 3: None}, {1: {"kcal": 450}, 3: {"kcal": 0}}):  # no nutrition at all, kcal 0
            self.assertEqual(planner.personal_factors(slots, {}, people, nutrition), {})

    def test_only_filled_active_non_skipped_meals_of_that_person_count(self):
        slots = day_slots(("lunch", 1, ["a"]), ("dinner", 2, ["b"]))
        people = {"a": person(900), "b": person(900)}
        self.assertEqual(planner.personal_factors(slots, {}, people, NUTRITION)["a", 0], 2.0)  # lunch only: 900 / 450
        slots[0]["skipped"] = True
        self.assertNotIn(("a", 0), planner.personal_factors(slots, {}, people, NUTRITION))
        slots[0].update(skipped=False, active=False)
        self.assertNotIn(("a", 0), planner.personal_factors(slots, {}, people, NUTRITION))

    def test_protein_totals_and_targets(self):
        slots = day_slots(("lunch", 1, ["a"]), ("dinner", 2, ["a"]))
        total = planner.person_day_totals(slots, {}, {"a": person(None, 100)}, NUTRITION)[0]["a"]
        self.assertEqual((total["protein_g"], total["protein_target"], total["kcal_target"], total["kcal"]), (60.0, 100, None, 1250.0))
        self.assertEqual(planner.person_day_totals(slots, {}, {"a": person()}, NUTRITION)[0], {})  # no target at all: no totals
        self.assertEqual(planner.person_day_totals(slots, {}, {"a": person(1700)}, NUTRITION)[1], {})  # nothing eaten on day 1


class FitTest(unittest.TestCase):
    PEOPLE = {"u": person(1700), "v": person()}

    def one_slot_plan(self, eaters=("u",)):
        slots = [{"day": d, "meal": m, "active": (d, m) == (0, "lunch"), "recipe_id": None, "eaters": list(eaters) if (d, m) == (0, "lunch") else [],
                  "guests": 0, "locked": False, "skipped": False, "reason": None} for d in range(7) for m in planner.MEALS]
        return {"week": WEEK, "status": "draft", "slots": slots, "canteen": {}}

    def gen(self, kcals, seed=1, people=PEOPLE, eaters=("u",)):
        recipes = [{"id": i, "for_lunch": True, "for_dinner": True, "archived": False, "tags": [], "kcal": k}
                   for i, k in kcals.items()]
        out = planner.generate(self.one_slot_plan(eaters), recipes, {}, ["u"], [], {"repeat_window_days": 14, "new_per_week": 0},
                               random.Random(seed), MONDAY, people)
        return out[0]

    def test_fit_labels(self):
        plan = self.one_slot_plan()
        slots, slot = plan["slots"], plan["slots"][0]
        fit = lambda kcal, people=self.PEOPLE: planner.fit(slots, slot, kcal, people, {})
        self.assertEqual(fit(1700), ("ok", 0))  # budget / kcal = 1
        self.assertEqual((fit(1134)[0], fit(1133)[0]), ("ok", "poor"))  # 1.499 / 1.5006: more than 1.5 is too light a meal
        self.assertEqual((fit(2266)[0], fit(2267)[0]), ("ok", "poor"))  # 0.7503 / 0.7499
        edges = {"u": person(1500)}
        self.assertEqual((fit(1000, edges)[0], fit(2000, edges)[0]), ("ok", "ok"))  # exactly 1.5 and 0.75 fit
        self.assertEqual(fit(None), ("unknown", 0))
        self.assertEqual(fit(0), ("unknown", 0))
        self.assertEqual(fit(500), ("poor", 1.0))
        self.assertEqual(fit(500, {"u": person()}), (None, 0))  # nobody with a target: no label

    def test_fit_counts_the_meals_of_the_day_and_the_canteen(self):
        plan = self.one_slot_plan()
        plan["slots"][1].update(active=True, eaters=["u"])  # + Monday dinner: the budget per meal halves
        slot = plan["slots"][0]
        self.assertEqual(planner.fit(plan["slots"], slot, 850, self.PEOPLE, {})[0], "ok")  # 850 / 850
        self.assertEqual(planner.fit(plan["slots"], slot, 1700, self.PEOPLE, {})[0], "poor")  # 0.5
        self.assertEqual(planner.fit(plan["slots"], slot, 850, self.PEOPLE, {0: ["u"]})[0], "poor")  # (1700 - 700) / 2 = 500 -> 0.59
        self.assertEqual(planner.fit(plan["slots"], slot, 500, self.PEOPLE, {0: ["u"]})[0], "ok")

    def test_share_is_misfits_per_eater_with_a_target(self):
        people = {"u": person(1700), "v": person(500), "w": person()}
        plan = self.one_slot_plan(("u", "v", "w"))
        self.assertEqual(planner.fit(plan["slots"], plan["slots"][0], 1700, people, {}), ("poor", 0.5))  # v: 500 / 1700 = 0.29

    def test_a_misfit_recipe_is_picked_less_often_and_says_so(self):
        picks = [self.gen({1: 1700, 2: 500}, seed)["recipe_id"] for seed in range(100)]
        self.assertGreater(picks.count(1), 75)  # weights 1 : exp(-2) ~ 88 %
        self.assertLess(picks.count(2), 25)
        self.assertEqual(self.gen({2: 500})["reason"]["fit"], "poor")
        self.assertEqual(self.gen({1: 1700})["reason"]["fit"], "ok")

    def test_unknown_kcal_is_not_penalised(self):
        picks = [self.gen({1: 1700, 2: None}, seed)["recipe_id"] for seed in range(100)]
        self.assertEqual(self.gen({2: None})["reason"]["fit"], "unknown")
        baseline = [self.gen({1: 1700, 2: None}, seed, people={})["recipe_id"] for seed in range(100)]
        self.assertEqual(picks, baseline)  # same seeds, same picks as without any target

    def test_without_targets_nothing_changes(self):
        self.assertNotIn("fit", self.gen({1: 1700}, people={})["reason"])
        self.assertNotIn("fit", self.gen({1: 1700}, people={"u": person()})["reason"])

    def test_reroll_uses_the_fit(self):
        plan = self.one_slot_plan()
        recipes = [{"id": i, "for_lunch": True, "for_dinner": True, "archived": False, "tags": [], "kcal": k}
                   for i, k in {1: 1700, 2: 500}.items()]
        plan["slots"][0]["recipe_id"] = 1
        out = planner.reroll(plan, 0, "lunch", recipes, {}, ["u"], [], {"repeat_window_days": 14}, random.Random(1), self.PEOPLE)
        self.assertEqual((out[0]["recipe_id"], out[0]["reason"]["fit"]), (2, "poor"))


class PortionsDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        for uid, name in (("a", "Anna"), ("b", "Ben")):
            db.upsert_user(self.conn, {"id": uid, "name": uid, "display_name": name})
        self.settings = {**db.get_settings(self.conn), "slot_pattern": [i != 1 for i in range(14)]}  # Monday dinner off

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def slots(self, week=WEEK):
        return {(s["day"], s["meal"]): s for s in plans.load_plan(self.conn, week, self.settings)["slots"]}

    def act(self, day, meal, **body):
        plans.slot_action(self.conn, WEEK, day, meal, body, self.settings, random.Random(1), MONDAY)
        return self.slots()[(day, meal)]

    def recipe(self, kcal, servings=2, protein=None, ingredient=("Reis", 200)):
        with self.conn:
            rid = self.conn.execute(
                "INSERT INTO recipes (title, source_kind, servings, kcal, protein_g, nutrition_source, created_at, updated_at) "
                "VALUES ('r', 'manual', ?, ?, ?, ?, 'x', 'x')", (servings, kcal, protein, "manual" if kcal else None)).lastrowid
            self.conn.execute("INSERT INTO ingredients (recipe_id, pos, amount, unit, name) VALUES (?, 0, ?, 'g', ?)",
                              (rid, ingredient[1], ingredient[0]))
        return rid

    def test_canteen_defaults_apply_to_new_weeks_only(self):
        db.set_household(self.conn, "a", {"canteen_days": [0, 2]})
        db.set_household(self.conn, "b", {"canteen_days": [0], "eats": False})  # not a participant: no defaults
        plan = plans.load_plan(self.conn, WEEK, self.settings)
        self.assertEqual(plan["canteen"], {0: ["a"], 2: ["a"]})
        slots = self.slots()
        self.assertEqual((slots[0, "lunch"]["eaters"], slots[1, "lunch"]["eaters"], slots[2, "lunch"]["eaters"]), ([], ["a"], []))
        self.assertEqual(slots[2, "dinner"]["eaters"], ["a"])  # only the lunch is replaced
        db.set_household(self.conn, "a", {"canteen_days": [4]})
        self.assertEqual(plans.load_plan(self.conn, WEEK, self.settings)["canteen"], {0: ["a"], 2: ["a"]})  # stored, not recreated
        self.assertEqual(plans.load_plan(self.conn, "2026-W42", self.settings)["canteen"], {4: ["a"]})

    def test_canteen_default_on_a_day_with_the_lunch_switched_off(self):
        db.set_household(self.conn, "a", {"canteen_days": [0]})
        self.settings["slot_pattern"] = [i != 0 for i in range(14)]  # Monday lunch off
        self.assertEqual(plans.load_plan(self.conn, WEEK, self.settings)["canteen"], {0: ["a"]})  # the row exists anyway

    def test_canteen_toggle_removes_and_re_adds_only_that_lunch_eater(self):
        plans.set_canteen(self.conn, WEEK, 1, "a", True, self.settings)
        slots = self.slots()
        self.assertEqual((slots[1, "lunch"]["eaters"], slots[1, "dinner"]["eaters"]), (["b"], ["a", "b"]))
        self.assertEqual(plans.load_plan(self.conn, WEEK, self.settings)["canteen"], {1: ["a"]})
        plans.set_canteen(self.conn, WEEK, 1, "a", True, self.settings)  # idempotent
        self.assertEqual(self.slots()[1, "lunch"]["eaters"], ["b"])
        plans.set_canteen(self.conn, WEEK, 1, "a", False, self.settings)
        self.assertEqual(self.slots()[1, "lunch"]["eaters"], ["a", "b"])
        self.assertEqual(plans.load_plan(self.conn, WEEK, self.settings)["canteen"], {})
        self.act(2, "lunch", action="deactivate")  # off while the lunch is switched off: not put back
        plans.set_canteen(self.conn, WEEK, 2, "a", True, self.settings)
        plans.set_canteen(self.conn, WEEK, 2, "a", False, self.settings)
        self.assertEqual(self.slots()[2, "lunch"]["eaters"], [])

    def test_canteen_creates_the_week_and_validates(self):
        plans.set_canteen(self.conn, "2026-W45", 0, "a", True, self.settings)
        self.assertEqual(plans.read_plan(self.conn, "2026-W45")["canteen"], {0: ["a"]})
        for args, field in [((7, "a", True), "day"), ((-1, "a", True), "day"), (("0", "a", True), "day"), ((True, "a", True), "day"),
                            ((0, "nobody", True), "user_id"), ((0, 1, True), "user_id"), ((0, "a", "yes"), "on"), ((0, "a", None), "on")]:
            with self.assertRaises(db.InvalidField, msg=args) as cm:
                plans.set_canteen(self.conn, WEEK, *args, self.settings)
            self.assertEqual(cm.exception.field, field)

    def test_activate_keeps_the_canteen_person_off_the_lunch(self):
        plans.set_canteen(self.conn, WEEK, 3, "a", True, self.settings)
        self.act(3, "lunch", action="deactivate")
        self.assertEqual(self.act(3, "lunch", action="activate")["eaters"], ["b"])
        self.assertEqual(self.act(3, "dinner", action="activate")["eaters"], ["a", "b"])  # canteen is for lunch only

    def test_view_portions_totals_and_canteen(self):
        db.set_household(self.conn, "a", {"kcal_target": 1700, "protein_target_g": 100})
        db.set_household(self.conn, "b", {"kcal_target": 1300})
        self.settings["slot_pattern"] = [True] * 14
        lunch, dinner = self.recipe(450, protein=20), self.recipe(800, protein=40)
        self.act(0, "lunch", action="set", recipe_id=lunch)
        self.act(0, "dinner", action="set", recipe_id=dinner)
        view = plans.view(self.conn, WEEK, self.settings, MONDAY)
        s = view["slots"][0]
        self.assertEqual((s["portions_by_user"], s["cooked_portions"]), ({"a": 1.25, "b": 1.0}, 2.25))
        self.assertEqual(view["slots"][2]["portions_by_user"], {"a": 1.0, "b": 1.0})  # nothing planned on Tuesday
        by_user = view["totals"][0]["totals_by_user"]
        self.assertEqual(by_user["a"], {"kcal": 1562.5, "kcal_target": 1700, "protein_g": 75.0, "protein_target": 100,
                                        "canteen": 0, "incomplete": False})
        self.assertEqual((by_user["b"]["kcal"], by_user["b"]["protein_target"]), (1250.0, None))
        self.assertEqual((view["totals"][0]["canteen"], view["totals"][0]["kcal"]), ([], 1250.0))  # per-portion totals stay
        self.assertEqual(view["totals"][1]["totals_by_user"], {})
        plans.set_canteen(self.conn, WEEK, 0, "a", True, self.settings)
        view = plans.view(self.conn, WEEK, self.settings, MONDAY)
        self.assertEqual(view["totals"][0]["canteen"], ["a"])
        self.assertEqual((view["slots"][0]["portions_by_user"], view["slots"][0]["cooked_portions"]), ({"b": 1.0}, 1.0))
        self.assertEqual(view["totals"][0]["totals_by_user"]["a"]["kcal"], 1700.0)  # 700 + 1.25 * 800 (dinner only)
        self.assertEqual(view["slots"][1]["portions_by_user"]["a"], 1.25)

    def test_shopping_scales_with_the_personal_factors_and_the_canteen(self):
        db.set_household(self.conn, "a", {"kcal_target": 1700})  # b has no target: factor 1
        self.settings["slot_pattern"] = [True] * 14
        self.act(0, "lunch", action="set", recipe_id=self.recipe(450, ingredient=("Reis", 200)))
        self.act(0, "dinner", action="set", recipe_id=self.recipe(800, ingredient=("Linsen", 100)))
        amounts = lambda: {k: i["amounts"] for k, i in shopping.build_list(self.conn, WEEK).items()}
        self.assertEqual(amounts(), {"reis": {"g": 225.0}, "linsen": {"g": 112.5}})  # lunch 1.25 + 1 = 2.25 of 2 servings; dinner the same
        plans.set_canteen(self.conn, WEEK, 0, "a", True, self.settings)  # a: canteen lunch, dinner (1700 - 700) / 800 = 1.25
        self.assertEqual(amounts(), {"reis": {"g": 100.0}, "linsen": {"g": 112.5}})
        self.assertEqual(shopping.build_list(self.conn, "2026-W50"), {})  # a week without a plan

    def test_manual_set_reason_carries_the_fit(self):
        db.set_household(self.conn, "a", {"kcal_target": 1700})
        self.settings["slot_pattern"] = [True] * 14
        self.act(1, "lunch", action="eater", user_id="b", on=False)
        for kcal, fit in ((3000, "poor"), (850, "ok"), (None, "unknown")):  # a has two meals on Tuesday: 850 kcal each
            self.assertEqual(self.act(1, "lunch", action="set", recipe_id=self.recipe(kcal))["reason"], {"kind": "manual", "fit": fit})
        self.act(1, "lunch", action="eater", user_id="a", on=False)  # nobody with a target eats it: no fit
        self.assertEqual(self.act(1, "lunch", action="set", recipe_id=self.recipe(3000))["reason"], {"kind": "manual"})

    def test_generate_gives_each_slot_a_fit_for_eaters_with_targets(self):
        db.set_household(self.conn, "a", {"kcal_target": 1700})
        for kcal in (800, 900, 1000, 850, 780, 820, 760, 810, 790, 880, 830, 840, 770, 860):
            self.recipe(kcal)
        self.settings["slot_pattern"] = [True] * 14
        plans.generate(self.conn, WEEK, self.settings, random.Random(1), MONDAY)
        self.assertEqual({s["reason"]["fit"] for s in self.slots().values() if s["reason"]["kind"] != "none"}, {"ok"})  # 850 per meal


if __name__ == "__main__":
    unittest.main()
