import copy
import json
import tempfile
import unittest
from pathlib import Path

from mealprep import db, recipes

TAGS = ["Nudeln", "Italienisch", "Schnell"]
ALL = [True] * 14
IMAGE = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.jpg"

SAMPLE = {
    "format_version": 1,
    "title": "Spaghetti Carbonara",
    "source_url": "https://www.example.com/rezepte/spaghetti-carbonara",
    "source_kind": "web",
    "image_url": "https://www.example.com/img/carbonara.jpg",
    "image": IMAGE,
    "servings": 4,
    "total_minutes": 30,
    "tags": ["Nudeln", "Italienisch", "Schnell"],
    "ingredients": [
        {"amount": 400, "unit": "g", "name": "Spaghetti", "note": None},
        {"amount": 4, "unit": None, "name": "Eier", "note": "Größe M"},
        {"amount": None, "unit": None, "name": "Salz", "note": None},
    ],
    "steps": ["Spaghetti in Salzwasser bissfest garen.", "Eier mit Parmesan verquirlen."],
    "nutrition": {"kcal": 650, "protein_g": 28, "fat_g": 25, "carbs_g": 75, "source": "page"},
    "warnings": [],
}


def with_(**changes):
    d = copy.deepcopy(SAMPLE)
    d.update(changes)
    return d


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.images = Path(self.tmp.name)
        (self.images / IMAGE).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, obj):
        return recipes.validate_draft(obj, TAGS, self.images)

    def assertRejected(self, obj, field):
        draft, errors = self.validate(obj)
        self.assertIsNone(draft)
        self.assertIn(field, errors)

    def test_sample_is_valid(self):
        draft, errors = self.validate(SAMPLE)
        self.assertEqual(errors, {})
        self.assertEqual(draft["title"], "Spaghetti Carbonara")
        self.assertEqual(draft["image_url"], SAMPLE["image_url"])
        self.assertEqual(draft["warnings"], [])

    def test_minimal_draft_gets_defaults(self):
        draft, errors = self.validate({"format_version": 1, "title": " Brot ", "servings": 2})
        self.assertEqual(errors, {})
        self.assertEqual((draft["title"], draft["source_kind"], draft["tags"]), ("Brot", "manual", []))
        self.assertNotIn("image_url", draft)

    def test_unknown_keys_dropped_and_non_object_rejected(self):
        draft, _ = self.validate(with_(evil=1))
        self.assertNotIn("evil", draft)
        self.assertRejected([], "")

    def test_format_version(self):
        for v in (2, "1", True, None):
            self.assertRejected(with_(format_version=v), "format_version")

    def test_title(self):
        for v in ("", "   ", "x" * 201, 5, None):
            self.assertRejected(with_(title=v), "title")

    def test_urls(self):
        for key in ("source_url", "image_url"):
            for v in ("ftp://x.de/a", "javascript:alert(1)", "https://", "https://x.de/" + "a" * 2048, 5):
                self.assertRejected(with_(**{key: v}), key)
            self.assertEqual(self.validate(with_(**{key: None}))[1], {})

    def test_source_kind(self):
        self.assertRejected(with_(source_kind="facebook"), "source_kind")

    def test_image(self):
        for v in ("../x.jpg", "abc.jpg", IMAGE.upper(), IMAGE.replace(".jpg", ".gif"), "a" * 64 + ".png", 5):
            self.assertRejected(with_(image=v), "image")  # last-but-one: well-formed but file missing
        self.assertEqual(self.validate(with_(image=None))[1], {})

    def test_servings(self):
        for v in (0, 51, 2.5, "4", True, None):
            self.assertRejected(with_(servings=v), "servings")

    def test_total_minutes(self):
        for v in (0, 1441, 1.5, "30"):
            self.assertRejected(with_(total_minutes=v), "total_minutes")
        self.assertEqual(self.validate(with_(total_minutes=None))[1], {})

    def test_old_meal_flags_are_dropped(self):
        for flags in ({"for_lunch": False, "for_dinner": False}, {"for_lunch": 1, "for_dinner": "yes"}, {"for_lunch": True}):
            draft, errors = self.validate(with_(**flags))
            self.assertEqual(errors, {})
            self.assertFalse({"for_lunch", "for_dinner"} & set(draft))

    def test_tags(self):
        draft, errors = self.validate(with_(tags=["nudeln", "NUDELN", "Schnell"]))
        self.assertEqual((errors, draft["tags"]), ({}, ["Nudeln", "Schnell"]))
        self.assertRejected(with_(tags=["Gibtsnicht"]), "tags.0")
        self.assertRejected(with_(tags=[5]), "tags.0")
        self.assertRejected(with_(tags=["Nudeln"] * 16), "tags")
        self.assertRejected(with_(tags="Nudeln"), "tags")

    def test_ingredients(self):
        def ing(**kw):
            return with_(ingredients=[{"amount": 1, "unit": "g", "name": "X", "note": None, **kw}])
        self.assertRejected(with_(ingredients=[{"name": "X"}] * 101), "ingredients")
        self.assertRejected(with_(ingredients=["Mehl"]), "ingredients.0")
        for v in (0, -1, 100001, "1"):
            self.assertRejected(ing(amount=v), "ingredients.0.amount")
        for v in ("Gramm", "g.", "dose"):
            self.assertRejected(ing(unit=v), "ingredients.0.unit")
        for v in ("", "  ", "x" * 101, None):
            self.assertRejected(ing(name=v), "ingredients.0.name")
        self.assertRejected(ing(note="x" * 201), "ingredients.0.note")
        self.assertRejected(ing(note=5), "ingredients.0.note")
        draft, errors = self.validate(ing(amount=None, unit=None, note="  "))
        self.assertEqual((errors, draft["ingredients"][0]["note"]), ({}, None))

    def test_steps(self):
        self.assertRejected(with_(steps=["x"] * 51), "steps")
        for v in ("", "  ", "x" * 2001, 5):
            self.assertRejected(with_(steps=["ok", v]), "steps.1")

    def test_nutrition(self):
        def nut(**kw):
            return with_(nutrition={"kcal": 1, "protein_g": 1, "fat_g": 1, "carbs_g": 1, "source": "manual", **kw})
        for key, v in [("kcal", 5001), ("kcal", -1), ("protein_g", 501), ("fat_g", "1"), ("carbs_g", 501)]:
            self.assertRejected(nut(**{key: v}), "nutrition." + key)
        self.assertRejected(nut(source="guess"), "nutrition.source")
        self.assertRejected(with_(nutrition=5), "nutrition")
        self.assertEqual(self.validate(nut(kcal=None, protein_g=None, fat_g=None))[1], {})
        draft, errors = self.validate(with_(nutrition={"kcal": None, "protein_g": None, "fat_g": None, "carbs_g": None, "source": None}))
        self.assertEqual((errors, draft["nutrition"]), ({}, None))

    def test_warnings(self):
        self.assertRejected(with_(warnings=["boom"]), "warnings")
        self.assertRejected(with_(warnings="no_recipe_data"), "warnings")

    def test_errors_are_reported_per_field(self):
        _, errors = self.validate(with_(title="", servings=0))
        self.assertEqual(set(errors), {"title", "servings"})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        self.conn.execute("INSERT INTO users (id, name, display_name, lang, first_seen, last_seen) VALUES ('u', 'n', 'N', NULL, 'now', 'now')")
        self.conn.commit()
        self.tags = [t["name"] for t in recipes.list_tags(self.conn)]
        (Path(self.tmp.name) / "images").mkdir()
        (Path(self.tmp.name) / "images" / IMAGE).write_bytes(b"x")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def make(self, **changes):
        draft, errors = recipes.validate_draft(with_(**changes), self.tags, Path(self.tmp.name) / "images")
        self.assertEqual(errors, {})
        return recipes.create_recipe(self.conn, draft, "u")

    def test_default_tags(self):
        self.assertEqual(len(self.tags), 31)  # the 25 defaults + the 6 categories added by migration 9
        self.assertEqual(self.tags, sorted(self.tags, key=recipes.sort_key))

    def test_create_get_roundtrip(self):
        rid = self.make()
        got = recipes.get_recipe(self.conn, rid)
        self.assertEqual(got["id"], rid)
        self.assertFalse(got["archived"])
        for key in ("title", "source_url", "source_kind", "image", "servings", "total_minutes", "steps", "nutrition"):
            self.assertEqual(got[key], SAMPLE[key])
        self.assertEqual(sorted(got["tags"]), sorted(SAMPLE["tags"]))
        self.assertEqual([i["name"] for i in got["ingredients"]], ["Spaghetti", "Eier", "Salz"])
        self.assertEqual(got["ingredients"][1], {"amount": 4, "unit": None, "name": "Eier", "note": "Größe M"})
        self.assertNotIn("image_url", got)
        self.assertFalse({"for_lunch", "for_dinner"} & (set(got) | set(recipes.list_recipes(self.conn)[0])))  # M16: flags gone
        self.assertIsNone(recipes.get_recipe(self.conn, rid + 1))

    def test_update_replaces_children(self):
        rid = self.make()
        draft, _ = recipes.validate_draft(
            with_(title="Neu", tags=["Schnell"], ingredients=[{"name": "Reis"}], steps=[], nutrition=None),
            self.tags, Path(self.tmp.name) / "images")
        self.assertTrue(recipes.update_recipe(self.conn, rid, draft))
        got = recipes.get_recipe(self.conn, rid)
        self.assertEqual((got["title"], got["tags"], got["steps"], got["nutrition"]), ("Neu", ["Schnell"], [], None))
        self.assertEqual([i["name"] for i in got["ingredients"]], ["Reis"])
        self.assertFalse(recipes.update_recipe(self.conn, rid + 1, draft))

    def test_archived_hidden_by_default(self):
        a, b = self.make(title="A"), self.make(title="B")
        self.assertTrue(recipes.set_archived(self.conn, a, True))
        self.assertEqual([r["title"] for r in recipes.list_recipes(self.conn)], ["B"])
        self.assertEqual([r["title"] for r in recipes.list_recipes(self.conn, archived=True)], ["A"])
        self.assertTrue(recipes.get_recipe(self.conn, a)["archived"])
        recipes.set_archived(self.conn, a, False)
        self.assertEqual(len(recipes.list_recipes(self.conn)), 2)
        self.assertFalse(recipes.set_archived(self.conn, b + 1, True))

    def test_list_search_and_tag_filter(self):
        self.make(title="Äpfel im Schlafrock", tags=["Schnell"], ingredients=[{"name": "Blätterteig"}])
        self.make(title="Suppe", tags=["Nudeln"], ingredients=[{"name": "Sellerie"}])
        titles = lambda **kw: [r["title"] for r in recipes.list_recipes(self.conn, **kw)]
        self.assertEqual(titles(q="äpfel"), ["Äpfel im Schlafrock"])
        self.assertEqual(titles(q="ÄPFEL"), ["Äpfel im Schlafrock"])
        self.assertEqual(titles(q="sellerie"), ["Suppe"])
        self.assertEqual(titles(tag="nudeln"), ["Suppe"])
        self.assertEqual(titles(q="suppe", tag="Schnell"), [])
        self.assertEqual(titles(), ["Äpfel im Schlafrock", "Suppe"])

    def test_deleting_tag_removes_associations(self):
        rid = self.make()
        tag_id = next(t["id"] for t in recipes.list_tags(self.conn) if t["name"] == "Nudeln")
        self.assertTrue(recipes.delete_tag(self.conn, tag_id))
        self.assertNotIn("Nudeln", recipes.get_recipe(self.conn, rid)["tags"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM recipe_tags WHERE tag_id = ?", (tag_id,)).fetchone()[0], 0)
        self.assertFalse(recipes.delete_tag(self.conn, tag_id))

    def test_tag_create_and_rename(self):
        tag = recipes.create_tag(self.conn, "  Grillen ")
        self.assertEqual(tag["name"], "Grillen")
        for bad in ("grillen", "", "  ", "x" * 51, None):
            with self.assertRaises(db.InvalidField):
                recipes.create_tag(self.conn, bad)
        self.assertEqual(recipes.update_tag(self.conn, tag["id"], "BBQ")["name"], "BBQ")
        self.assertEqual(recipes.update_tag(self.conn, tag["id"], "bbq")["name"], "bbq")  # same tag, new case
        with self.assertRaises(db.InvalidField):
            recipes.update_tag(self.conn, tag["id"], "Fleisch")
        self.assertIsNone(recipes.update_tag(self.conn, 9999, "Foo"))

    def test_tag_category_round_trip(self):
        tags = {t["name"]: t for t in recipes.list_tags(self.conn)}
        self.assertEqual(sorted(n for n, t in tags.items() if t["category"]),
                         sorted(["Schnell", "Meal Prep", "Sonntagsessen", "Leicht", "Proteinreich", "Lunchbox", "Ofengericht", "Gäste"]))
        self.assertFalse(tags["Nudeln"]["category"])
        vesper = recipes.create_tag(self.conn, "Vesper", True)  # a category and a tag are told apart when they are created
        self.assertEqual(vesper, {"id": vesper["id"], "name": "Vesper", "category": True, "slots": ALL})
        self.assertFalse(recipes.create_tag(self.conn, "Grillen")["category"])
        self.assertFalse(recipes.create_tag(self.conn, "Grillen2", False)["category"])
        self.assertTrue(next(t for t in recipes.list_tags(self.conn) if t["id"] == vesper["id"])["category"])
        for bad in (1, 0, None, "yes"):
            with self.assertRaises(db.InvalidField) as cm:
                recipes.create_tag(self.conn, "Foo", bad)
            self.assertEqual(cm.exception.field, "category")
        nudeln = tags["Nudeln"]["id"]  # updating never changes the flag
        self.assertEqual(recipes.update_tag(self.conn, nudeln, "Nudeln"), {"id": nudeln, "name": "Nudeln", "category": False, "slots": ALL})
        self.assertEqual(recipes.update_tag(self.conn, vesper["id"], "Abendbrot", ALL)["category"], True)
        self.assertTrue(next(t for t in recipes.list_tags(self.conn) if t["id"] == vesper["id"])["category"])

    def test_tag_slots_round_trip_and_validation(self):
        tags = {t["name"]: t for t in recipes.list_tags(self.conn)}
        self.assertTrue(all(t["slots"] == ALL for t in tags.values()))  # NULL reads as all slots
        self.assertEqual(recipes.create_tag(self.conn, "Vesper", True)["slots"], ALL)
        schnell = tags["Schnell"]["id"]
        dinners = [i % 2 == 1 for i in range(14)]
        self.assertEqual(recipes.update_tag(self.conn, schnell, "Schnell", dinners)["slots"], dinners)
        self.assertEqual(next(t for t in recipes.list_tags(self.conn) if t["id"] == schnell)["slots"], dinners)
        self.assertEqual(recipes.update_tag(self.conn, schnell, "Schnell", ALL)["slots"], ALL)
        self.assertIsNone(self.conn.execute("SELECT slots FROM tags WHERE id = ?", (schnell,)).fetchone()[0])  # all ticked = NULL
        for bad in (ALL[:13], ALL + [True], [1] * 14, [True] * 13 + [None], ["x"] * 14, "x" * 14, None, {}):
            with self.assertRaises(db.InvalidField, msg=bad) as cm:
                recipes.update_tag(self.conn, schnell, "Schnell", bad)
            self.assertEqual(cm.exception.field, "slots")
        with self.assertRaises(db.InvalidField) as cm:  # slots belong to categories only
            recipes.update_tag(self.conn, tags["Nudeln"]["id"], "Nudeln", dinners)
        self.assertEqual(cm.exception.field, "slots")

    def test_allowed_slots_of_a_recipe_are_the_intersection_of_its_categories(self):
        ids = {t["name"]: t["id"] for t in recipes.list_tags(self.conn)}
        dinners = [i % 2 == 1 for i in range(14)]
        monday_to_wednesday = [i < 6 for i in range(14)]
        recipes.update_tag(self.conn, ids["Schnell"], "Schnell", dinners)
        recipes.update_tag(self.conn, ids["Leicht"], "Leicht", monday_to_wednesday)
        self.conn.execute("UPDATE tags SET slots = ? WHERE name = 'Nudeln'", (json.dumps([False] * 14),))  # no category: ignored

        def slots(**kw):
            rid = self.make(**kw)
            return next(r["slots"] for r in recipes.planning_recipes(self.conn) if r["id"] == rid)

        self.assertEqual(slots(tags=["Schnell"]), dinners)
        self.assertEqual(slots(tags=["Schnell", "Leicht"]), [i in (1, 3, 5) for i in range(14)])
        self.assertEqual(slots(tags=["Schnell", "Leicht", "Nudeln"]), [i in (1, 3, 5) for i in range(14)])
        self.assertEqual(slots(tags=["Nudeln"]), ALL)
        self.assertEqual(slots(tags=[]), ALL)  # no categories

    def test_auto_categories_at_the_boundaries(self):
        auto = lambda minutes=None, **n: recipes.auto_categories({"total_minutes": minutes, "nutrition": {"source": "page", **n} if n else None})
        self.assertEqual((auto(30), auto(31), auto(1), auto(None)), ({"Schnell"}, set(), {"Schnell"}, set()))
        self.assertEqual((auto(kcal=500), auto(kcal=501)), ({"Leicht"}, set()))
        self.assertEqual(auto(kcal=400, protein_g=25), {"Leicht", "Proteinreich"})  # 100 kcal of 400 = exactly 25 %
        self.assertEqual(auto(kcal=400, protein_g=24.9), {"Leicht"})
        self.assertEqual(auto(kcal=800, protein_g=50), {"Proteinreich"})
        self.assertEqual(auto(protein_g=50), set())  # no kcal: no share
        self.assertEqual(auto(kcal=400), {"Leicht"})  # no protein: no Proteinreich
        self.assertEqual(auto(20, kcal=300, protein_g=30), {"Schnell", "Leicht", "Proteinreich"})
        self.assertEqual(recipes.auto_categories({}), set())
        self.assertEqual(recipes.auto_categories({"total_minutes": 20, "nutrition": {"kcal": None, "protein_g": None, "source": "ai"}}), {"Schnell"})

    def test_suggested_tags_are_existing_tags_only(self):
        draft = {"total_minutes": 10, "nutrition": {"kcal": 300, "protein_g": 30, "source": "ai"}}
        self.assertEqual(recipes.suggested_tags(draft, self.tags), ["Leicht", "Proteinreich", "Schnell"])
        self.assertEqual(recipes.suggested_tags(draft, ["schnell", "Nudeln"]), ["schnell"])  # renamed in case: still found
        self.assertEqual(recipes.suggested_tags(draft, ["Nudeln"]), [])  # deleted tags are not brought back

    def test_known_names_distinct_and_sorted(self):
        self.make(ingredients=[{"name": "zucker"}, {"name": "Äpfel"}, {"name": "Mehl"}])
        self.make(ingredients=[{"name": "Zucker"}, {"name": "mehl"}, {"name": "Butter"}])
        self.assertEqual(recipes.known_ingredient_names(self.conn), ["Äpfel", "Butter", "Mehl", "zucker"])

    def test_ratings_per_person(self):
        self.conn.execute("INSERT INTO users (id, name, display_name, lang, first_seen, last_seen) VALUES ('v', 'm', 'Mia', NULL, 'now', 'now')")
        self.conn.commit()
        a, b = self.make(title="A"), self.make(title="B", tags=["Suppe"])
        self.assertTrue(recipes.set_rating(self.conn, "u", a, 4))
        self.assertTrue(recipes.set_rating(self.conn, "v", a, 2))
        self.assertTrue(recipes.set_rating(self.conn, "v", a, 0))  # replaces, no second row
        self.assertFalse(recipes.set_rating(self.conn, "u", 9999, 3))
        info = recipes.rating_info(self.conn, a, "u")
        self.assertEqual(info["ratings"], [{"user_id": "v", "display_name": "Mia", "stars": 0},
                                           {"user_id": "u", "display_name": "N", "stars": 4}])
        self.assertEqual((info["my_stars"], info["my_prediction"], info["vetoed"], info["household_score"]), (4, None, True, 2))
        self.assertIsNotNone(recipes.rating_info(self.conn, b, "u")["my_prediction"])
        self.assertTrue(recipes.set_rating(self.conn, "v", a, None))
        self.assertEqual([r["user_id"] for r in recipes.rating_info(self.conn, a, "u")["ratings"]], ["u"])
        listed = {r["id"]: r for r in recipes.list_recipes(self.conn, user_id="v")}
        self.assertEqual((listed[a]["my_stars"], listed[b]["my_stars"]), (None, None))
        self.assertEqual([r["id"] for r in recipes.list_recipes(self.conn, user_id="u", unrated_by_me=True)], [b])
        with self.assertRaises(Exception):  # unknown user: foreign key
            recipes.set_rating(self.conn, "nobody", a, 3)


class BringImportUrlTest(unittest.TestCase):
    BASE = "https://api.getbring.com/rest/bringrecipes/deeplink?url="

    def test_url_is_encoded_completely(self):
        self.assertEqual(recipes.bring_import_url("https://www.chefkoch.de/rezepte/1/pasta.html"),
                         self.BASE + "https%3A%2F%2Fwww.chefkoch.de%2Frezepte%2F1%2Fpasta.html&source=web")
        self.assertEqual(recipes.bring_import_url("https://example.com/r?a=1&b=2#schritt"),
                         self.BASE + "https%3A%2F%2Fexample.com%2Fr%3Fa%3D1%26b%3D2%23schritt&source=web")
        self.assertEqual(recipes.bring_import_url("https://example.com/Käse & Brot?q=süß"),
                         self.BASE + "https%3A%2F%2Fexample.com%2FK%C3%A4se%20%26%20Brot%3Fq%3Ds%C3%BC%C3%9F&source=web")

    def test_quantities_only_when_both_are_given(self):
        url = "https://example.com/r"
        plain = recipes.bring_import_url(url)
        self.assertNotIn("Quantity", plain)
        self.assertEqual(recipes.bring_import_url(url, 4), plain)
        self.assertEqual(recipes.bring_import_url(url, 4, 2), plain + "&baseQuantity=4&requestedQuantity=2")


if __name__ == "__main__":
    unittest.main()
