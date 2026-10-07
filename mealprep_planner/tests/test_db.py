import sqlite3
import tempfile
import unittest
from unittest import mock

from mealprep import db, ha


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def version(self):
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def test_migrates_to_latest_version_and_is_idempotent(self):
        self.assertEqual(self.version(), len(db.MIGRATIONS))
        db.migrate(self.conn)
        self.assertEqual(self.version(), len(db.MIGRATIONS))
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"settings", "users", "tags", "recipes", "ingredients", "recipe_tags", "import_jobs", "ratings", "plans", "plan_slots", "pantry", "pushed_items", "slot_eaters"}, tables)

    def test_default_pantry_is_filled_by_migration(self):
        names = [r[0] for r in self.conn.execute("SELECT name FROM pantry")]
        self.assertEqual(sorted(names), sorted(db.DEFAULT_PANTRY))
        self.assertEqual(len(names), 11)
        with self.assertRaises(sqlite3.IntegrityError):  # names are unique, case-insensitively
            self.conn.execute("INSERT INTO pantry (name) VALUES ('salz')")

    def test_settings_defaults(self):
        self.assertEqual(db.get_settings(self.conn), {"bring_entity": None, "ai_enabled": True, "ai_entity": None, "default_portions": 2,
                                                      "inbox_entity": None, "slot_pattern": [True] * 14, "repeat_window_days": 14,
                                                      "new_per_week": 2, "slot_rules": [None] * 14})

    def test_invalid_values_rejected(self):
        for patch, field in [
            ({"bring_entity": "light.x"}, "bring_entity"),
            ({"ai_entity": "todo.x"}, "ai_entity"),
            ({"ai_enabled": "yes"}, "ai_enabled"),
            ({"nope": 1}, "nope"),
        ]:
            with self.assertRaises(db.InvalidField) as cm:
                db.set_settings(self.conn, patch)
            self.assertEqual(cm.exception.field, field)

    def test_planner_settings_validated_and_stored(self):
        for patch, field in [
            ({"slot_pattern": [True] * 13}, "slot_pattern"),
            ({"slot_pattern": [True] * 13 + [1]}, "slot_pattern"),
            ({"slot_pattern": "x" * 14}, "slot_pattern"),
            ({"repeat_window_days": -1}, "repeat_window_days"),
            ({"repeat_window_days": 61}, "repeat_window_days"),
            ({"repeat_window_days": True}, "repeat_window_days"),
            ({"new_per_week": 15}, "new_per_week"),
            ({"new_per_week": 1.5}, "new_per_week"),
        ]:
            with self.assertRaises(db.InvalidField) as cm:
                db.set_settings(self.conn, patch)
            self.assertEqual(cm.exception.field, field)
        pattern = [i % 2 == 0 for i in range(14)]
        s = db.set_settings(self.conn, {"slot_pattern": pattern, "repeat_window_days": 0, "new_per_week": 14})
        self.assertEqual((s["slot_pattern"], s["repeat_window_days"], s["new_per_week"]), (pattern, 0, 14))
        s = db.set_settings(self.conn, {"slot_pattern": [True] * 14, "repeat_window_days": 60, "new_per_week": 0})
        self.assertEqual((s["slot_pattern"], s["repeat_window_days"], s["new_per_week"]), ([True] * 14, 60, 0))
        db.get_settings(self.conn)["slot_pattern"][0] = False  # defaults are not shared between callers
        self.assertEqual(db.get_settings(self.conn)["slot_pattern"], [True] * 14)

    def test_unknown_entity_rejected_and_valid_stored(self):
        with mock.patch.object(ha, "get_state", side_effect=ha.HAError(404, "ha_error")):
            with self.assertRaises(db.InvalidField):
                db.set_settings(self.conn, {"bring_entity": "todo.gone"})
        with mock.patch.object(ha, "get_state", return_value={}):
            s = db.set_settings(self.conn, {"bring_entity": "todo.bring", "ai_enabled": False})
        self.assertEqual(s["bring_entity"], "todo.bring")
        self.assertFalse(db.get_settings(self.conn)["ai_enabled"])

    def test_inbox_entity_must_be_a_todo_list_other_than_bring(self):
        with self.assertRaises(db.InvalidField) as cm:
            db.set_settings(self.conn, {"inbox_entity": "ai_task.x"})
        self.assertEqual(cm.exception.field, "inbox_entity")
        with mock.patch.object(ha, "get_state", return_value={}):
            db.set_settings(self.conn, {"bring_entity": "todo.bring", "inbox_entity": "todo.inbox"})
            for patch, field in [({"inbox_entity": "todo.bring"}, "inbox_entity"),
                                 ({"bring_entity": "todo.inbox"}, "bring_entity"),
                                 ({"bring_entity": "todo.x", "inbox_entity": "todo.x"}, "inbox_entity")]:
                with self.assertRaises(db.InvalidField) as cm:
                    db.set_settings(self.conn, patch)
                self.assertEqual(cm.exception.field, field)
            self.assertIsNone(db.set_settings(self.conn, {"inbox_entity": None})["inbox_entity"])
            self.assertEqual(db.get_settings(self.conn)["bring_entity"], "todo.bring")

    def test_migration_7_on_a_db_with_existing_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(tmp)
            try:
                for n, script in enumerate(db.MIGRATIONS[:6], start=1):  # the database as shipped with M8-M10
                    conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
                with conn:
                    conn.executemany("INSERT INTO users (id, name, display_name, first_seen, last_seen) VALUES (?, ?, ?, 'x', 'x')",
                                     [("a", "a", "A"), ("b", "b", "B")])
                    conn.execute("INSERT INTO recipes (id, title, source_kind, servings, created_at, updated_at) "
                                 "VALUES (1, 'r', 'manual', 2, 'x', 'x')")
                    conn.execute("INSERT INTO plans (week, status) VALUES ('2026-W41', 'confirmed')")
                    conn.executemany("INSERT INTO plan_slots (week, day, meal, active, recipe_id, portions, reason) VALUES ('2026-W41', ?, ?, ?, ?, ?, ?)",
                                     [(0, "lunch", 1, 1, 4, '{"kind": "manual"}'), (0, "dinner", 0, None, 2, None), (1, "lunch", 1, 1, 6, None)])
                db.migrate(conn)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
                self.assertNotIn("portions", [r["name"] for r in conn.execute("PRAGMA table_info(plan_slots)")])
                self.assertEqual([tuple(r) for r in conn.execute("SELECT day, meal, user_id FROM slot_eaters ORDER BY day, meal, user_id")],
                                 [(0, "lunch", "a"), (0, "lunch", "b"), (1, "lunch", "a"), (1, "lunch", "b")])  # active slots only
                self.assertEqual([tuple(r) for r in conn.execute("SELECT day, meal, recipe_id, guests, reason FROM plan_slots ORDER BY day, meal")],
                                 [(0, "dinner", None, 0, None), (0, "lunch", 1, 0, '{"kind": "manual"}'), (1, "lunch", 1, 0, None)])
                self.assertEqual([r["eats"] for r in conn.execute("SELECT eats FROM users")], [1, 1])
                conn.execute("DELETE FROM plan_slots WHERE day = 1")  # eaters follow their slot
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM slot_eaters").fetchone()[0], 2)
            finally:
                conn.close()

    def test_migration_9_on_an_existing_db(self):
        categories = {"Schnell", "Meal Prep", "Sonntagsessen", "Leicht", "Proteinreich", "Lunchbox", "Ofengericht", "Gäste"}
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(tmp)
            try:
                for n, script in enumerate(db.MIGRATIONS[:8], start=1):  # the database as shipped with M12
                    conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
                with conn:
                    conn.execute("INSERT INTO tags (name) VALUES ('MEAL PREP')")  # a hand-made tag, other case
                    conn.execute("INSERT INTO plans (week, status) VALUES ('2026-W41', 'draft')")
                    conn.execute("INSERT INTO plan_slots (week, day, meal, active) VALUES ('2026-W41', 0, 'lunch', 1)")
                db.migrate(conn)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
                names = [r["name"] for r in conn.execute("SELECT name FROM tags")]
                self.assertEqual(len(names), 31)  # 25 defaults + 5 new ('Meal Prep' already existed) + the hand-made one
                self.assertEqual(len({n.casefold() for n in names}), 31)
                flagged = {r["name"] for r in conn.execute("SELECT name FROM tags WHERE category = 1")}
                self.assertEqual({n.casefold() for n in flagged}, {c.casefold() for c in categories})
                self.assertEqual(len(flagged), 8)
                self.assertIn("MEAL PREP", names)  # kept as it was
                tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Schnell'").fetchone()[0]
                conn.execute("UPDATE plan_slots SET rule_tag_id = ?", (tag_id,))
                conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))  # ON DELETE SET NULL
                self.assertIsNone(conn.execute("SELECT rule_tag_id FROM plan_slots").fetchone()[0])
            finally:
                conn.close()

    def test_slot_rules_setting(self):
        ids = {r["name"]: r["id"] for r in self.conn.execute("SELECT id, name FROM tags")}
        rules = [ids["Schnell"] if i in (1, 3) else ids["Leicht"] if i == 5 else None for i in range(14)]
        self.assertEqual(db.set_settings(self.conn, {"slot_rules": rules})["slot_rules"], rules)
        self.assertEqual(db.set_settings(self.conn, {"slot_rules": [None] * 14})["slot_rules"], [None] * 14)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'slot_rules'").fetchone()[0], 0)  # default: not stored
        for bad in ([None] * 13, [None] * 15, [ids["Nudeln"]] + [None] * 13, [99999] + [None] * 13, [True] + [None] * 13,
                    [str(ids["Schnell"])] + [None] * 13, [float(ids["Schnell"])] + [None] * 13, "x", None):
            with self.assertRaises(db.InvalidField, msg=bad) as cm:
                db.set_settings(self.conn, {"slot_rules": bad})
            self.assertEqual(cm.exception.field, "slot_rules")
        db.set_settings(self.conn, {"slot_rules": rules})
        self.conn.execute("DELETE FROM tags WHERE name = 'Schnell'")  # a deleted tag reads as no rule
        self.assertEqual(db.get_settings(self.conn)["slot_rules"], [ids["Leicht"] if i == 5 else None for i in range(14)])
        db.get_settings(self.conn)["slot_rules"][0] = 1  # defaults are not shared between callers
        self.assertEqual(db.get_settings(self.conn)["slot_rules"][0], None)

    def test_household_validation(self):
        db.upsert_user(self.conn, {"id": "u1", "name": "n", "display_name": "B"})
        db.upsert_user(self.conn, {"id": "u2", "name": "m", "display_name": "a"})
        defaults = {"kcal_target": None, "protein_target_g": None, "canteen_kcal": 700, "canteen_days": []}
        self.assertEqual(db.household(self.conn), [{"user_id": "u2", "display_name": "a", "eats": True, **defaults},
                                                   {"user_id": "u1", "display_name": "B", "eats": True, **defaults}])
        self.assertEqual(db.set_household(self.conn, "u1", {"eats": False}),
                         {"user_id": "u1", "display_name": "B", "eats": False, **defaults})
        self.assertEqual(db.set_household(self.conn, "u1", {}), {"user_id": "u1", "display_name": "B", "eats": False, **defaults})
        self.assertIsNone(db.set_household(self.conn, "nobody", {"eats": True}))
        for patch, field in [({"eats": 1}, "eats"), ({"eats": "yes"}, "eats"), ({"eats": None}, "eats"), ({"nope": True}, "nope"),
                             ({"eats": True, "nope": True}, "nope"), ([], "body")]:
            with self.assertRaises(db.InvalidField) as cm:
                db.set_household(self.conn, "u1", patch)
            self.assertEqual(cm.exception.field, field)
        self.assertFalse(db.household(self.conn)[1]["eats"])  # a rejected patch changes nothing

    def test_household_targets_and_canteen(self):
        db.upsert_user(self.conn, {"id": "u1", "name": "n", "display_name": "N"})
        full = {"kcal_target": 1700, "protein_target_g": 120, "canteen_kcal": 900, "canteen_days": [4, 0, 2]}
        self.assertEqual(db.set_household(self.conn, "u1", full)["canteen_days"], [0, 2, 4])  # stored sorted
        self.assertEqual({k: db.household(self.conn)[0][k] for k in full}, {**full, "canteen_days": [0, 2, 4]})
        for patch in ({"kcal_target": 300}, {"kcal_target": 5000}, {"protein_target_g": 10}, {"protein_target_g": 400},
                      {"canteen_kcal": 0}, {"canteen_kcal": 2000}, {"canteen_days": []}, {"canteen_days": [0, 6]},
                      {"kcal_target": None, "protein_target_g": None}):
            db.set_household(self.conn, "u1", patch)  # the ranges' edges are valid; the targets may be cleared
        self.assertEqual((db.household(self.conn)[0]["kcal_target"], db.household(self.conn)[0]["protein_target_g"]), (None, None))
        for patch, field in [({"kcal_target": 299}, "kcal_target"), ({"kcal_target": 5001}, "kcal_target"),
                             ({"kcal_target": 1700.5}, "kcal_target"), ({"kcal_target": "1700"}, "kcal_target"),
                             ({"kcal_target": True}, "kcal_target"), ({"protein_target_g": 9}, "protein_target_g"),
                             ({"protein_target_g": 401}, "protein_target_g"), ({"canteen_kcal": -1}, "canteen_kcal"),
                             ({"canteen_kcal": 2001}, "canteen_kcal"), ({"canteen_kcal": None}, "canteen_kcal"),
                             ({"canteen_days": [7]}, "canteen_days"), ({"canteen_days": [-1]}, "canteen_days"),
                             ({"canteen_days": [1, 1]}, "canteen_days"), ({"canteen_days": [True]}, "canteen_days"),
                             ({"canteen_days": [[1]]}, "canteen_days"), ({"canteen_days": "1"}, "canteen_days"),
                             ({"canteen_days": None}, "canteen_days")]:
            with self.assertRaises(db.InvalidField, msg=patch) as cm:
                db.set_household(self.conn, "u1", patch)
            self.assertEqual(cm.exception.field, field)
        db.set_household(self.conn, "u1", {"kcal_target": 2000})
        with self.assertRaises(db.InvalidField):  # a rejected patch changes nothing, also for its valid keys
            db.set_household(self.conn, "u1", {"kcal_target": 1800, "canteen_kcal": 5000})
        self.assertEqual(db.household(self.conn)[0]["kcal_target"], 2000)

    def test_migration_8_on_a_db_at_version_7(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(tmp)
            try:
                for n, script in enumerate(db.MIGRATIONS[:7], start=1):  # the database as shipped with M11
                    conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
                with conn:
                    conn.execute("INSERT INTO users (id, name, display_name, first_seen, last_seen) VALUES ('a', 'a', 'A', 'x', 'x')")
                    conn.execute("INSERT INTO plans (week, status) VALUES ('2026-W41', 'draft')")
                db.migrate(conn)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
                self.assertEqual(db.household(conn), [{"user_id": "a", "display_name": "A", "eats": True, "kcal_target": None,
                                                       "protein_target_g": None, "canteen_kcal": 700, "canteen_days": []}])
                with conn:
                    conn.execute("INSERT INTO plan_canteen (week, day, user_id) VALUES ('2026-W41', 2, 'a')")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("INSERT INTO plan_canteen (week, day, user_id) VALUES ('2026-W41', 7, 'a')")
            finally:
                conn.close()

    def test_user_upsert_and_lang(self):
        user = {"id": "u1", "name": "n", "display_name": "N"}
        self.assertIsNone(db.upsert_user(self.conn, user))
        db.set_user_lang(self.conn, "u1", "en")
        self.assertEqual(db.upsert_user(self.conn, user), "en")


if __name__ == "__main__":
    unittest.main()
