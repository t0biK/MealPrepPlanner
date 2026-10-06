import json
import unittest
from unittest import mock

from mealprep import ai, ha

TAGS = ["Nudeln", "Italienisch", "Schnell", "Suppe", "Vegetarisch", "Fleisch", "Fisch", "Reis"]
ENTITY = "ai_task.test"

SAMPLE = {
    "title": "Spaghetti Pomodoro",
    "servings": 2,
    "total_minutes": 25,
    "for_lunch": True,
    "for_dinner": True,
    "tags": ["nudeln", "Italienisch"],
    "ingredients": [
        {"amount": 200, "unit": "g", "name": "Spaghetti", "note": None},
        {"amount": 1, "unit": "Dose", "name": "Tomaten", "note": "gehackt"},
        {"amount": None, "unit": None, "name": "Salz", "note": None},
    ],
    "steps": ["Nudeln kochen.", "Sauce mischen."],
    "nutrition": {"kcal": 600, "protein_g": 20, "fat_g": 10, "carbs_g": 100},
}


def reply(data):
    return {"service_response": {"data": data}}


def fenced(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


def draft(**kw):
    return {
        "format_version": 1, "title": "Alt", "source_url": "https://example.com/r", "source_kind": "web",
        "image_url": "https://example.com/i.jpg", "image": "a" * 64 + ".jpg", "servings": 4, "total_minutes": None,
        "for_lunch": True, "for_dinner": True, "tags": ["Suppe"], "steps": ["Kochen."], "nutrition": None,
        "ingredients": [{"amount": None, "unit": None, "name": "Salz und Pfeffer", "note": None}],
        "warnings": ["already_imported"], **kw,
    }


def base(**kw):
    return {"format_version": 1, "source_kind": "tiktok", "source_url": "https://www.tiktok.com/@a/video/1",
            "servings": 2, "warnings": [], **kw}


class ValidateTest(unittest.TestCase):
    def v(self, **kw):
        return ai.validate_ai_output({**SAMPLE, **kw}, TAGS)

    def test_valid_sample(self):
        patch, dropped = self.v()
        self.assertEqual(dropped, [])
        self.assertEqual(patch["title"], "Spaghetti Pomodoro")
        self.assertEqual((patch["servings"], patch["total_minutes"]), (2, 25))
        self.assertEqual(patch["tags"], ["Nudeln", "Italienisch"])  # canonical spelling
        self.assertEqual(patch["ingredients"][1], {"amount": 1, "unit": "Dose", "name": "Tomaten", "note": "gehackt"})
        self.assertEqual(patch["nutrition"], {"kcal": 600, "protein_g": 20, "fat_g": 10, "carbs_g": 100, "source": "ai"})

    def test_not_an_object_or_bad_title(self):
        for bad in (None, [], "text", 5):
            with self.assertRaises(ai.AIInvalid):
                ai.validate_ai_output(bad)
        for title in (None, "", "   ", 5, "x" * 201):
            with self.assertRaises(ai.AIInvalid):
                self.v(title=title)
        with self.assertRaises(ai.AIInvalid):
            ai.validate_ai_output({"ingredients": []})

    def test_scalar_limits(self):
        for key, bad in (("servings", 0), ("servings", 51), ("servings", "4"), ("servings", True),
                         ("total_minutes", 0), ("total_minutes", 1441), ("total_minutes", 1.5)):
            patch, dropped = self.v(**{key: bad})
            self.assertNotIn(key, patch)
            self.assertEqual(dropped, [key])
        patch, dropped = self.v(for_lunch=False, for_dinner=False)
        self.assertNotIn("for_lunch", patch)
        self.assertEqual(dropped, ["for_lunch"])
        self.assertEqual(self.v(for_lunch=False, for_dinner=True)[0]["for_lunch"], False)

    def test_tag_limits(self):
        patch, dropped = self.v(tags=["Gibtsnicht", 5, "Nudeln", "NUDELN", None])
        self.assertEqual((patch["tags"], dropped), (["Nudeln"], ["tags.0", "tags.1", "tags.4"]))
        many = ["Nudeln", "Italienisch", "Schnell", "Suppe", "Vegetarisch", "Fleisch", "Fisch", "Reis"]
        self.assertEqual(len(self.v(tags=many)[0]["tags"]), ai.MAX_TAGS)
        self.assertEqual(self.v(tags="Nudeln")[0]["tags"], [])

    def test_ingredient_limits(self):
        good = {"amount": 1, "unit": None, "name": "x", "note": None}
        patch, _ = self.v(ingredients=[good] * 101)
        self.assertEqual(len(patch["ingredients"]), 100)
        patch, dropped = self.v(ingredients=[{"name": "y" * 101}, {"name": ""}, {"amount": 1}, "Salz", good])
        self.assertEqual((len(patch["ingredients"]), dropped), (1, ["ingredients.0", "ingredients.1", "ingredients.2", "ingredients.3"]))
        for amount in (0, -1, 100001, "400", True):
            self.assertIsNone(self.v(ingredients=[{**good, "amount": amount}])[0]["ingredients"][0]["amount"], amount)
        self.assertEqual(self.v(ingredients=[{**good, "amount": 100000}])[0]["ingredients"][0]["amount"], 100000)
        self.assertEqual(len(self.v(ingredients=[{**good, "note": "n" * 300}])[0]["ingredients"][0]["note"]), 200)

    def test_step_limits(self):
        patch, dropped = self.v(steps=["a"] * 51)
        self.assertEqual((len(patch["steps"]), len(dropped)), (50, 1))
        patch, dropped = self.v(steps=["ok", "", "  ", "s" * 2001, 5])
        self.assertEqual((patch["steps"], dropped), (["ok"], ["steps.1", "steps.2", "steps.3", "steps.4"]))

    def test_nutrition_limits(self):
        n = lambda **kw: self.v(nutrition=kw)[0]["nutrition"]
        self.assertEqual(n(kcal=5001, protein_g=20), {"kcal": None, "protein_g": 20, "fat_g": None, "carbs_g": None, "source": "ai"})
        self.assertEqual(n(carbs_g=501, fat_g=-1, kcal="x"), None)
        self.assertEqual(n(kcal=5000, protein_g=500)["kcal"], 5000)
        self.assertIsNone(self.v(nutrition="viel")[0]["nutrition"])
        self.assertIsNone(self.v(nutrition=None)[0]["nutrition"])

    def test_units(self):
        def one(unit, note=None):
            ing = {"amount": 2, "unit": unit, "name": "Sahne", "note": note}
            return self.v(ingredients=[ing])[0]["ingredients"][0]
        self.assertEqual(one("EL")["unit"], "EL")
        self.assertEqual(one("Esslöffel")["unit"], "EL")  # alias of the unit table
        self.assertEqual(one("Pck.")["unit"], "Packung")
        self.assertEqual((one("Handvoll")["unit"], one("Messbecher")["unit"]), ("Handvoll", None))
        self.assertEqual(one("Messbecher")["note"], "Messbecher")  # unknown unit word goes to the note
        self.assertEqual(one("Messbecher", "gehäuft")["note"], "Messbecher gehäuft")
        self.assertEqual((one("")["unit"], one(5)["unit"]), (None, None))

    def test_unknown_keys_are_dropped(self):
        patch, _ = ai.validate_ai_output({**SAMPLE, "source_url": "https://evil.example/", "image": "x", "evil": 1,
                                          "ingredients": [{**SAMPLE["ingredients"][0], "price": 5}]}, TAGS)
        self.assertEqual(set(patch), {"title", "servings", "total_minutes", "for_lunch", "for_dinner", "tags",
                                      "ingredients", "steps", "nutrition"})
        self.assertEqual(set(patch["ingredients"][0]), {"amount", "unit", "name", "note"})

    def test_partial_output(self):
        patch, _ = ai.validate_ai_output({"title": "Nur Titel"}, TAGS)
        self.assertEqual((patch["title"], patch["ingredients"], patch["steps"], patch["tags"], patch["nutrition"]),
                         ("Nur Titel", [], [], [], None))


class AskTest(unittest.TestCase):
    def ask(self, data):
        with mock.patch.object(ha, "call_service", return_value=data):
            return ai._ask("prompt", ENTITY)

    def test_fenced_plain_and_dict_data(self):
        self.assertEqual(self.ask(reply(fenced(SAMPLE))), SAMPLE)
        self.assertEqual(self.ask(reply(json.dumps(SAMPLE))), SAMPLE)
        self.assertEqual(self.ask(reply("```\n" + json.dumps(SAMPLE) + "\n```")), SAMPLE)
        self.assertEqual(self.ask(reply(SAMPLE)), SAMPLE)

    def test_unusable_answers(self):
        for data in (reply("Hier ist Ihr Rezept: lecker"), reply('{"title": "abgeschnit'), reply("[1, 2]"),
                     reply(None), reply(5), {"service_response": None}, {}, None, []):
            with self.assertRaises(ai.AIInvalid, msg=data):
                self.ask(data)

    def test_call_arguments(self):
        with mock.patch.object(ha, "call_service", return_value=reply(fenced(SAMPLE))) as call:
            ai._ask("prompt", ENTITY)
        args, kwargs = call.call_args
        self.assertEqual(args[:2], ("ai_task", "generate_data"))
        self.assertEqual(args[2], {"task_name": "mealprep_import", "entity_id": ENTITY, "instructions": "prompt"})
        self.assertNotIn("structure", args[2])  # variant B (V6)
        self.assertEqual((kwargs["return_response"], kwargs["timeout"]), (True, 60))


class EnrichTest(unittest.TestCase):
    def enrich(self, d, data=None, entity=ENTITY, **kw):
        with mock.patch.object(ha, "call_service", return_value=reply(fenced({**SAMPLE, **kw}) if data is None else data)) as call:
            result = ai.enrich(d, ["Zwiebeln"], TAGS, entity)
        return result, call

    def test_merge(self):
        (d, warnings), _ = self.enrich(draft())
        self.assertEqual(warnings, [])
        self.assertEqual([i["name"] for i in d["ingredients"]], ["Spaghetti", "Tomaten", "Salz"])
        self.assertEqual(d["tags"], ["Suppe", "Nudeln", "Italienisch"])  # existing tags stay, AI tags are added
        self.assertEqual(d["nutrition"]["source"], "ai")
        # title, servings, steps and warnings come from the rule-based draft
        self.assertEqual((d["title"], d["servings"], d["steps"], d["warnings"]), ("Alt", 4, ["Kochen."], ["already_imported"]))

    def test_source_and_image_are_never_changed(self):
        (d, _), _ = self.enrich(draft(), source_url="https://evil.example/", image="b" * 64 + ".png", source_kind="text")
        self.assertEqual((d["source_url"], d["image"], d["image_url"], d["source_kind"]),
                         ("https://example.com/r", "a" * 64 + ".jpg", "https://example.com/i.jpg", "web"))

    def test_page_nutrition_is_kept(self):
        page = {"kcal": 500.0, "protein_g": None, "fat_g": None, "carbs_g": None, "source": "page"}
        (d, _), _ = self.enrich(draft(nutrition=page))
        self.assertEqual(d["nutrition"], page)

    def test_lunch_dinner_from_ai(self):
        (d, _), _ = self.enrich(draft(), for_lunch=False, for_dinner=True)
        self.assertEqual((d["for_lunch"], d["for_dinner"]), (False, True))

    def test_empty_ai_ingredients_keep_the_draft_ingredients(self):
        (d, _), _ = self.enrich(draft(), ingredients=[])
        self.assertEqual(d["ingredients"][0]["name"], "Salz und Pfeffer")

    def test_disabled_makes_no_call(self):
        original = draft()
        for entity in (None, ""):
            (d, warnings), call = self.enrich(original, entity=entity)
            self.assertEqual((d, warnings), (original, ["ai_disabled"]))
            call.assert_not_called()

    def test_failures_keep_the_rule_based_draft(self):
        original = draft()
        for data in (reply("kein JSON"), reply(fenced({"ingredients": []})), {"service_response": {}}):
            (d, warnings), _ = self.enrich(original, data=data)
            self.assertEqual((d, warnings), (original, ["ai_failed"]), data)
        for error in (ha.HAError(None, "ha_unavailable"), ha.HAError(500, "ha_error")):
            with mock.patch.object(ha, "call_service", side_effect=error):
                self.assertEqual(ai.enrich(original, [], TAGS, ENTITY), (original, ["ai_failed"]))

    def test_prompt_has_at_most_500_known_names_and_all_tags(self):
        names = [f"Zutat{i:04d}" for i in range(600)]
        with mock.patch.object(ha, "call_service", return_value=reply(fenced(SAMPLE))) as call:
            ai.enrich(draft(), names, TAGS, ENTITY)
        prompt = call.call_args.args[2]["instructions"]
        self.assertIn("Zutat0499", prompt)
        self.assertNotIn("Zutat0500", prompt)
        for tag in TAGS:
            self.assertIn(tag, prompt)
        # the draft content is part of the prompt
        for part in ("Alt", "Portionen: 4", "Salz und Pfeffer", "1. Kochen.", "keine"):
            self.assertIn(part, prompt)


class FromTextTest(unittest.TestCase):
    def from_text(self, text="Caption", data=None, entity=ENTITY, **kw):
        with mock.patch.object(ha, "call_service", return_value=reply(fenced({**SAMPLE, **kw}) if data is None else data)) as call:
            result = ai.from_text(text, ["Zwiebeln"], TAGS, entity, base(image="a" * 64 + ".jpg", image_url="https://example.com/t.jpg"))
        return result, call

    def test_draft_from_text(self):
        (d, warnings), call = self.from_text("200 g Spaghetti mit Sauce")
        self.assertEqual(warnings, [])
        self.assertEqual((d["title"], d["servings"], d["source_kind"], d["source_url"]),
                         ("Spaghetti Pomodoro", 2, "tiktok", "https://www.tiktok.com/@a/video/1"))
        self.assertEqual((d["image"], d["image_url"]), ("a" * 64 + ".jpg", "https://example.com/t.jpg"))
        self.assertEqual(d["nutrition"]["source"], "ai")
        self.assertIn("200 g Spaghetti mit Sauce", call.call_args.args[2]["instructions"])

    def test_servings_default_when_ai_has_none(self):
        (d, _), _ = self.from_text(servings=None)
        self.assertEqual(d["servings"], 2)

    def test_source_fields_are_not_taken_from_the_ai(self):
        (d, _), _ = self.from_text(source_url="https://evil.example/", image="b" * 64 + ".png", source_kind="web")
        self.assertEqual((d["source_url"], d["image"], d["source_kind"]),
                         ("https://www.tiktok.com/@a/video/1", "a" * 64 + ".jpg", "tiktok"))

    def test_disabled_and_failing(self):
        (d, warnings), call = self.from_text(entity=None)
        self.assertEqual((d, warnings), (None, ["ai_disabled"]))
        call.assert_not_called()
        for data in (reply("nope"), reply(fenced({"title": ""}))):
            self.assertEqual(self.from_text(data=data)[0], (None, ["ai_failed"]))
        with mock.patch.object(ha, "call_service", side_effect=ha.HAError(None, "ha_unavailable")):
            self.assertEqual(ai.from_text("x", [], TAGS, ENTITY, base()), (None, ["ai_failed"]))


class EstimateNutritionTest(unittest.TestCase):
    def estimate(self, data, entity=ENTITY):
        with mock.patch.object(ha, "call_service", return_value=data) as call:
            return ai.estimate_nutrition(draft(title="Pasta", servings=3), entity), call

    def test_valid_answer(self):
        answer = {"kcal": 640, "protein_g": 22.5, "fat_g": 18, "carbs_g": 90}
        for data in (reply(fenced(answer)), reply(json.dumps(answer)), reply(answer)):
            nutrition, call = self.estimate(data)
            self.assertEqual(nutrition, {**answer, "source": "ai"})
        prompt = call.call_args.args[2]["instructions"]
        for part in ("Pasta", "Portionen: 3", "Salz und Pfeffer", "pro Portion"):
            self.assertIn(part, prompt)
        self.assertNotIn("structure", call.call_args.args[2])

    def test_out_of_range_values_are_dropped(self):
        nutrition, _ = self.estimate(reply(fenced({"kcal": 5001, "protein_g": -1, "fat_g": "viel", "carbs_g": 500, "evil": 1})))
        self.assertEqual(nutrition, {"kcal": None, "protein_g": None, "fat_g": None, "carbs_g": 500, "source": "ai"})
        self.assertIsNone(self.estimate(reply(fenced({"kcal": 9999, "protein_g": 501})))[0])  # nothing usable left

    def test_unusable_answers_and_ai_off(self):
        for data in (reply("Das sind etwa 600 kcal."), reply('{"kcal": 6'), reply("[1]"), reply(None), {}, {"service_response": {}}):
            self.assertIsNone(self.estimate(data)[0], data)
        nutrition, call = self.estimate(reply(fenced({"kcal": 600})), entity=None)
        self.assertIsNone(nutrition)
        call.assert_not_called()
        for error in (ha.HAError(None, "ha_unavailable"), ha.HAError(500, "ha_error")):
            with mock.patch.object(ha, "call_service", side_effect=error):
                self.assertIsNone(ai.estimate_nutrition(draft(), ENTITY))


if __name__ == "__main__":
    unittest.main()
