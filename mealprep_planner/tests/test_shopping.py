import tempfile
import unittest
from unittest import mock

from mealprep import db, ha, ingredients, shopping

WEEK = "2026-W41"
BRING = "todo.bring"


def line(name, amount=None, unit=None):
    return (name, amount, unit)


def amounts(lines):
    return {k: i["amounts"] for k, i in ingredients.aggregate(lines).items()}


class IngredientHelpersTest(unittest.TestCase):
    def test_unit_key_and_base(self):
        self.assertEqual([ingredients.unit_key(u) for u in ("g", "kg", "ml", "cl", "dl", "l", "Dose", "EL", None)],
                         ["g", "g", "ml", "ml", "ml", "ml", "Dose", "EL", ""])
        self.assertEqual([ingredients.to_base(1.5, u) for u in ("kg", "g", "l", "dl", "cl", "ml", "Dose", None)],
                         [1500, 1.5, 1500, 150, 15, 1.5, 1.5, 1.5])
        self.assertIsNone(ingredients.to_base(None, "kg"))

    def test_shopping_round(self):
        self.assertEqual(ingredients.shopping_round(333.333, "g"), 333.33)
        self.assertEqual(ingredients.shopping_round(1.26, "EL"), 1.3)
        self.assertEqual(ingredients.shopping_round(0.1 + 0.2, "Zehe"), 1)  # countable: up
        self.assertEqual(ingredients.shopping_round(2.0000001, ""), 2)
        self.assertEqual(ingredients.shopping_round(2.5, "Dose"), 3)

    def test_aggregate(self):
        self.assertEqual(amounts([line("Hafer", 500, "g"), line("hafer", 0.75, "kg")]), {"hafer": {"g": 1250}})
        self.assertEqual(amounts([line("Milch", 1, "l"), line("Milch", 250, "ml"), line("Milch", 5, "dl")]), {"milch": {"ml": 1750}})
        self.assertEqual(amounts([line("Tomaten", 1, "Dose"), line("Tomaten", 300, "g"), line("Tomaten", 2, "Dose")]),
                         {"tomaten": {"Dose": 3, "g": 300}})
        self.assertEqual(amounts([line("Salz"), line("Salz")]), {"salz": {}})
        self.assertEqual(amounts([line("Salz"), line("Salz", 1, "TL"), line("Salz")]), {"salz": {"TL": 1}})
        self.assertEqual(amounts([line("Eier", 4), line("Eier", 3)]), {"eier": {"": 7}})
        self.assertEqual(amounts([line("Knoblauch", 1.2, "Zehe"), line("Knoblauch", 1.2, "Zehe")]), {"knoblauch": {"Zehe": 3}})
        self.assertEqual(amounts([line("Zimt", 0.26, "TL"), line("Zimt", 0.26, "TL")]), {"zimt": {"TL": 0.5}})
        self.assertEqual(ingredients.aggregate([line("Zwiebeln", 1), line("zwiebeln", 1)])["zwiebeln"]["name"], "Zwiebeln")

    def test_format_note(self):
        note = ingredients.format_note
        self.assertEqual(note({"g": 1250, "Dose": 2}), "1,25 kg + 2 Dosen")
        self.assertEqual(note({"Dose": 2, "ml": 1000, "g": 800, "": 4}), "800 g + 1 l + 4 + 2 Dosen")
        self.assertEqual(note({"g": 800}), "800 g")
        self.assertEqual(note({"ml": 500}), "500 ml")
        self.assertEqual(note({"Zehe": 1, "TL": 0.5}), "0,5 TL + 1 Zehe")
        self.assertEqual(note({"g": 999.5}), "999,5 g")
        self.assertEqual(note({}), "")


class StubBring:
    """In-memory Bring! list standing in for HA: get_items, add_item, update_item (by uid)."""

    def __init__(self, items=(), fail=None):
        self.items = [{"status": "needs_action", "description": "", **i} for i in items]
        self.fail = fail or {}  # {item name or uid: HAError}
        self.calls = []

    def __call__(self, domain, service, data, return_response=False, timeout=10):
        self.calls.append((service, data))
        if service != "get_items" and data["item"] in self.fail:
            raise self.fail[data["item"]]
        if service == "get_items":
            return {"service_response": {data["entity_id"]: {"items": [dict(i) if isinstance(i, dict) else i for i in self.items]}}}
        if service == "add_item":
            self.items.append({"summary": data["item"], "uid": f"new{len(self.items)}", "status": "needs_action",
                               "description": data.get("description", "")})
        else:  # by uid; a name only when the item has no usable uid
            target = next(i for i in self.items if isinstance(i, dict) and data["item"] in (i["uid"], i["summary"]))
            target["description"] = data["description"]
        return {}

    def writes(self):
        return [(s, d["item"], d.get("description")) for s, d in self.calls if s != "get_items"]


class ShoppingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        self.now = "2026-01-01T00:00:00"
        with self.conn:
            self.conn.execute("INSERT INTO plans (week, status) VALUES (?, 'confirmed')", (WEEK,))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def recipe(self, servings, ingredient_rows):
        with self.conn:
            rid = self.conn.execute("INSERT INTO recipes (title, source_kind, servings, created_at, updated_at) "
                                    "VALUES ('r', 'manual', ?, ?, ?)", (servings, self.now, self.now)).lastrowid
            self.conn.executemany("INSERT INTO ingredients (recipe_id, pos, amount, unit, name) VALUES (?, ?, ?, ?, ?)",
                                  [(rid, i, *row) for i, row in enumerate(ingredient_rows)])
        return rid

    def slot(self, day, meal, recipe_id, portions=2, active=1, skipped=0, week=WEEK):
        with self.conn:
            self.conn.execute("DELETE FROM plan_slots WHERE week = ? AND day = ? AND meal = ?", (week, day, meal))
            self.conn.execute("INSERT INTO plan_slots (week, day, meal, active, recipe_id, portions, skipped) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?)", (week, day, meal, active, recipe_id, portions, skipped))

    def configure(self):
        with mock.patch.object(ha, "get_state", return_value={}):
            db.set_settings(self.conn, {"bring_entity": BRING})

    def push(self, stub):
        with mock.patch.object(ha, "call_service", stub):
            return shopping.push(self.conn, WEEK)

    def pushed(self):
        return {(r["name"], r["unit_key"]): r["amount"]
                for r in self.conn.execute("SELECT name, unit_key, amount FROM pushed_items WHERE week = ?", (WEEK,))}


class BuildListTest(ShoppingTestCase):
    def test_scaled_by_portions_over_servings_and_summed(self):
        r1 = self.recipe(4, [(400, "g", "Spaghetti"), (4, None, "Eier"), (None, None, "Salz"), (1, "Dose", "Tomaten")])
        r2 = self.recipe(2, [(1, "kg", "Spaghetti"), (1, "Dose", "Tomaten")])
        self.slot(0, "lunch", r1, portions=2)
        self.slot(0, "dinner", r2, portions=6)
        items, _ = shopping.build_list(self.conn, WEEK)
        self.assertEqual({k: i["amounts"] for k, i in items.items()},
                         {"spaghetti": {"g": 3200}, "eier": {"": 2}, "tomaten": {"Dose": 4}})  # 200 + 3000 g; 0.5 + 3 Dosen -> 4

    def test_pantry_excluded_case_insensitively_and_reported(self):
        rid = self.recipe(2, [(None, None, "salz"), (2, "EL", "Olivenöl"), (200, "g", "Nudeln"), (1, "EL", "zucker")])
        self.slot(0, "lunch", rid)
        items, skipped = shopping.build_list(self.conn, WEEK)
        self.assertEqual(list(items), ["nudeln"])
        self.assertEqual(skipped, ["Olivenöl", "salz", "zucker"])

    def test_only_active_unskipped_filled_slots(self):
        rid = self.recipe(2, [(1, None, "Ei")])
        self.slot(0, "lunch", rid)
        self.slot(0, "dinner", rid, active=0)
        self.slot(1, "lunch", rid, skipped=1)
        self.slot(1, "dinner", None)
        items, _ = shopping.build_list(self.conn, WEEK)
        self.assertEqual(items["ei"]["amounts"], {"": 1})
        self.assertEqual(shopping.build_list(self.conn, "2031-W01"), ({}, []))


class DiffTest(unittest.TestCase):
    @staticmethod
    def cur(**items):
        return {k.lower(): {"name": k, "amounts": v} for k, v in items.items()}

    @staticmethod
    def was(*rows):
        return {(n.lower(), k): {"name": n, "amount": a} for n, k, a in rows}

    def test_new_increased_decreased_removed(self):
        current = self.cur(Hafer={"g": 500}, Milch={"ml": 1500}, Eier={"": 2}, Reis={"g": 300})
        pushed = self.was(("Hafer", "g", 500), ("Milch", "ml", 1000), ("Eier", "", 4), ("Butter", "g", 250))
        to_push, gone = shopping.diff(current, pushed)
        self.assertEqual(to_push, {"milch": {"name": "Milch", "amounts": {"ml": 500}},
                                   "reis": {"name": "Reis", "amounts": {"g": 300}}})
        self.assertEqual(gone, {"eier": {"name": "Eier", "amounts": {"": 2}}, "butter": {"name": "Butter", "amounts": {"g": 250}}})

    def test_same_name_with_another_unit(self):
        current = self.cur(Tomaten={"Dose": 2, "g": 300})
        to_push, gone = shopping.diff(current, self.was(("Tomaten", "Dose", 2)))
        self.assertEqual((to_push["tomaten"]["amounts"], gone), ({"g": 300}, {}))
        to_push, gone = shopping.diff(self.cur(Tomaten={"Dose": 2}), self.was(("Tomaten", "Dose", 2), ("Tomaten", "g", 300)))
        self.assertEqual((to_push, gone), ({}, {"tomaten": {"name": "Tomaten", "amounts": {"g": 300}}}))

    def test_amount_less_item_is_pushed_once(self):
        current = self.cur(Pfefferminz={})
        self.assertEqual(shopping.diff(current, {})[0], {"pfefferminz": {"name": "Pfefferminz", "amounts": {}}})
        self.assertEqual(shopping.diff(current, self.was(("Pfefferminz", "", None))), ({}, {}))
        self.assertEqual(shopping.diff(current, self.was(("Pfefferminz", "g", 20))), ({}, {}))  # still needed, nothing to add
        self.assertEqual(shopping.diff({}, self.was(("Pfefferminz", "", None)))[1],
                         {"pfefferminz": {"name": "Pfefferminz", "amounts": {}}})

    def test_amount_after_amount_less_push(self):
        to_push, gone = shopping.diff(self.cur(Kaffee={"g": 250}), self.was(("Kaffee", "", None)))
        self.assertEqual((to_push["kaffee"]["amounts"], gone), ({"g": 250}, {}))

    def test_float_noise_is_no_change(self):
        self.assertEqual(shopping.diff(self.cur(Hafer={"g": 0.1 + 0.2}), self.was(("Hafer", "g", 0.3))), ({}, {}))


class PushTest(ShoppingTestCase):
    def test_missing_bring_entity_is_bring_failed(self):
        self.slot(0, "lunch", self.recipe(2, [(1, None, "Ei")]))
        stub = StubBring()
        with self.assertRaises(shopping.BringFailed):
            self.push(stub)
        self.assertEqual(stub.calls, [])

    def test_adds_new_items_with_notes_and_records_them(self):
        self.configure()
        rid = self.recipe(2, [(500, "g", "Hafer"), (1.5, "kg", "Kartoffeln"), (2, "Dose", "Tomaten"), (None, None, "Basilikum"),
                              (None, None, "Salz")])
        self.slot(0, "lunch", rid, portions=2)
        stub = StubBring()
        result = self.push(stub)
        self.assertEqual(sorted(stub.writes()), [("add_item", "Basilikum", None), ("add_item", "Hafer", "500 g"),
                                                 ("add_item", "Kartoffeln", "1,5 kg"), ("add_item", "Tomaten", "2 Dosen")])
        self.assertEqual([e["name"] for e in result["added"]], ["Basilikum", "Hafer", "Kartoffeln", "Tomaten"])
        self.assertEqual((result["updated"], result["failed"], result["no_longer_needed"], result["skipped_pantry"]), ([], [], [], ["Salz"]))
        self.assertEqual(self.pushed(), {("Basilikum", ""): None, ("Kartoffeln", "g"): 1500, ("Hafer", "g"): 500, ("Tomaten", "Dose"): 2})
        self.assertEqual(stub.calls[0][1]["status"], ["needs_action"])

    def test_open_item_gets_our_amount_appended_by_uid(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(500, "ml", "Milch"), (200, "g", "Käse")]))
        stub = StubBring([{"summary": "milch", "uid": "u-1", "description": "1 l"},
                          {"summary": "Milch", "uid": "u-2", "description": "ignored: second item with this name"},
                          {"summary": "Käse", "uid": "u-3", "description": ""}])
        result = self.push(stub)
        self.assertEqual(sorted(stub.writes()), [("update_item", "u-1", "1 l + 500 ml"), ("update_item", "u-3", "200 g")])
        self.assertEqual([e["name"] for e in result["updated"]], ["Käse", "Milch"])
        self.assertEqual(result["added"], [])
        self.assertEqual(len(stub.items), 3)  # no duplicate item

    def test_completed_bring_items_are_not_extended(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(1, "l", "Milch")]))
        stub = StubBring([{"summary": "Milch", "uid": "u-1", "description": "old", "status": "completed"}])
        self.assertEqual(len(self.push(stub)["added"]), 1)

    def test_open_amount_less_item_needs_no_call(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(None, None, "Basilikum")]))
        stub = StubBring([{"summary": "Basilikum", "uid": "u-1"}])
        result = self.push(stub)
        self.assertEqual((stub.writes(), len(result["updated"]), self.pushed()), ([], 1, {("Basilikum", ""): None}))

    def test_repush_sends_only_the_difference(self):
        self.configure()
        rid = self.recipe(2, [(500, "g", "Hafer")])
        other = self.recipe(2, [(250, "g", "Hafer"), (1, None, "Ei")])
        self.slot(0, "lunch", rid)
        stub = StubBring()
        self.push(stub)
        self.assertEqual(self.push(stub)["added"], [])  # nothing changed: no further call
        self.assertEqual(len(stub.writes()), 1)
        self.slot(0, "dinner", other)
        result = self.push(stub)
        self.assertEqual(sorted(stub.writes()), [("add_item", "Ei", "1"), ("add_item", "Hafer", "500 g"), ("update_item", "new0", "500 g + 250 g")])
        self.assertEqual(([e["name"] for e in result["added"]], [e["name"] for e in result["updated"]]), (["Ei"], ["Hafer"]))
        self.assertEqual(self.pushed(), {("Hafer", "g"): 750, ("Ei", ""): 1})

    def test_removed_and_decreased_items_are_listed_not_touched(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(500, "g", "Hafer"), (1, None, "Ei")]))
        self.slot(0, "dinner", self.recipe(2, [(200, "g", "Hafer")]))
        stub = StubBring()
        self.push(stub)
        before = len(stub.calls)
        self.slot(0, "dinner", None)
        result = self.push(stub)
        self.assertEqual(len(stub.calls), before)  # nothing to send: no HA call at all
        self.assertEqual(result["no_longer_needed"], [{"name": "Hafer", "note": "200 g"}])
        self.assertEqual(shopping.view(self.conn, WEEK)["no_longer_needed"], [{"name": "Hafer", "note": "200 g"}])
        self.slot(0, "lunch", None)
        self.assertEqual(self.push(stub)["no_longer_needed"], [{"name": "Ei", "note": "1"}, {"name": "Hafer", "note": "700 g"}])
        self.assertEqual(self.pushed(), {("Hafer", "g"): 700, ("Ei", ""): 1})  # what Bring! got stays recorded

    def test_partial_failure_is_recorded_and_resumable(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(1, "kg", "Apfel"), (2, None, "Birne"), (3, None, "Kiwi")]))
        stub = StubBring(fail={"Birne": ha.HAError(500, "ha_error")})
        result = self.push(stub)
        self.assertEqual(([e["name"] for e in result["added"]], result["failed"]),
                         (["Apfel", "Kiwi"], [{"name": "Birne", "note": "2"}]))
        self.assertEqual(self.pushed(), {("Apfel", "g"): 1000, ("Kiwi", ""): 3})
        stub.fail = {}
        result = self.push(stub)
        self.assertEqual(([e["name"] for e in result["added"]], result["failed"]), (["Birne"], []))
        self.assertEqual([i["summary"] for i in stub.items], ["Apfel", "Kiwi", "Birne"])  # no duplicates

    def test_connection_loss_fails_the_rest_without_more_calls(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(1, None, "Apfel"), (2, None, "Birne"), (3, None, "Kiwi")]))
        stub = StubBring(fail={"Birne": ha.HAError(None, "ha_unavailable")})
        result = self.push(stub)
        self.assertEqual(([e["name"] for e in result["added"]], [e["name"] for e in result["failed"]]), (["Apfel"], ["Birne", "Kiwi"]))
        self.assertEqual([w[1] for w in stub.writes()], ["Apfel", "Birne"])
        self.assertEqual(self.pushed(), {("Apfel", ""): 1})

    def test_unreadable_bring_list_is_bring_failed(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(1, None, "Apfel")]))
        for bad in (ha.HAError(None, "ha_unavailable"), ha.HAError(500, "ha_error")):
            with mock.patch.object(ha, "call_service", side_effect=bad), self.assertRaises(shopping.BringFailed):
                shopping.push(self.conn, WEEK)
        for resp in (None, {}, {"service_response": {}}, {"service_response": {BRING: {"items": "x"}}},
                     {"service_response": {BRING: {"items": None}}}):
            with mock.patch.object(ha, "call_service", return_value=resp), self.assertRaises(shopping.BringFailed):
                shopping.push(self.conn, WEEK)
        self.assertEqual(self.pushed(), {})

    def test_untrusted_items_are_skipped(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(1, "l", "Milch")]))
        stub = StubBring([{"summary": 5, "uid": "x"}, {"summary": "Milch", "uid": 7, "description": None}])
        stub.items.insert(0, "garbage")
        result = self.push(stub)
        self.assertEqual(stub.writes(), [("update_item", "Milch", "1 l")])  # no usable uid: addressed by name
        self.assertEqual(len(result["updated"]), 1)

    def test_view_shows_status_per_item(self):
        self.configure()
        self.slot(0, "lunch", self.recipe(2, [(500, "g", "Hafer"), (None, None, "Salz")]))
        self.assertEqual(shopping.view(self.conn, WEEK),
                         {"items": [{"name": "Hafer", "note": "500 g", "status": "pending"}], "pantry": ["Salz"], "no_longer_needed": []})
        self.push(StubBring())
        self.slot(0, "dinner", self.recipe(2, [(100, "g", "Hafer")]))
        self.assertEqual(shopping.view(self.conn, WEEK)["items"], [{"name": "Hafer", "note": "600 g", "status": "pending"}])


class PantryTest(ShoppingTestCase):
    def test_defaults_roundtrip_and_dedupe(self):
        self.assertEqual(shopping.get_pantry(self.conn), sorted(db.DEFAULT_PANTRY, key=str.casefold))
        self.assertEqual(len(db.DEFAULT_PANTRY), 11)
        self.assertEqual(shopping.set_pantry(self.conn, [" Salz ", "salz", "Öl", "Reis"]), ["Reis", "Salz", "Öl"])
        self.assertEqual(shopping.set_pantry(self.conn, []), [])

    def test_validation(self):
        for bad in ("Salz", None, [""], ["  "], [5], ["a" * 101], ["x"] * 301):
            with self.assertRaises(db.InvalidField, msg=bad) as cm:
                shopping.set_pantry(self.conn, bad)
            self.assertEqual(cm.exception.field, "names")
        shopping.set_pantry(self.conn, ["a" * 100] + [f"n{i}" for i in range(299)])
        self.assertEqual(len(shopping.get_pantry(self.conn)), 300)
        self.assertEqual(len(db.DEFAULT_PANTRY), len(set(n.casefold() for n in db.DEFAULT_PANTRY)))


if __name__ == "__main__":
    unittest.main()
