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
        self.assertLessEqual({"settings", "users", "tags", "recipes", "ingredients", "recipe_tags", "import_jobs", "ratings", "plans", "plan_slots", "pantry", "pushed_items"}, tables)

    def test_default_pantry_is_filled_by_migration(self):
        names = [r[0] for r in self.conn.execute("SELECT name FROM pantry")]
        self.assertEqual(sorted(names), sorted(db.DEFAULT_PANTRY))
        self.assertEqual(len(names), 11)
        with self.assertRaises(sqlite3.IntegrityError):  # names are unique, case-insensitively
            self.conn.execute("INSERT INTO pantry (name) VALUES ('salz')")

    def test_settings_defaults(self):
        self.assertEqual(db.get_settings(self.conn), {"bring_entity": None, "ai_enabled": True, "ai_entity": None, "default_portions": 2,
                                                      "inbox_entity": None, "slot_pattern": [True] * 14, "repeat_window_days": 14,
                                                      "new_per_week": 2})

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

    def test_user_upsert_and_lang(self):
        user = {"id": "u1", "name": "n", "display_name": "N"}
        self.assertIsNone(db.upsert_user(self.conn, user))
        db.set_user_lang(self.conn, "u1", "en")
        self.assertEqual(db.upsert_user(self.conn, user), "en")


if __name__ == "__main__":
    unittest.main()
