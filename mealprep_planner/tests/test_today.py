import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from mealprep import db, ha, plans, planner, worker

TODAY = date(2026, 10, 14)  # Wednesday of 2026-W42 (Monday 2026-10-12)


def dated(day, meal, title):
    return {"date": day, "meal": meal, "title": title}


def state(slots, hour, minute=0, day=TODAY):
    return planner.sensor_payload(slots, datetime(day.year, day.month, day.day, hour, minute))[0]


class SensorPayloadTest(unittest.TestCase):
    SLOTS = [dated(TODAY, "lunch", "Linsensuppe"), dated(TODAY, "dinner", "Carbonara"),
             dated(TODAY + timedelta(days=1), "lunch", "Curry"), dated(TODAY + timedelta(days=1), "dinner", "Pizza")]

    def test_state_switches_at_14_and_21(self):
        self.assertEqual([state(self.SLOTS, h, m) for h, m in ((8, 0), (13, 59), (14, 0), (20, 59), (21, 0))],
                         ["Linsensuppe", "Linsensuppe", "Carbonara", "Carbonara", "Curry"])

    def test_without_an_active_lunch(self):
        slots = [s for s in self.SLOTS if (s["date"], s["meal"]) != (TODAY, "lunch")]
        self.assertEqual([state(slots, h, m) for h, m in ((8, 0), (13, 59), (14, 0), (20, 59), (21, 0))],
                         ["Carbonara", "Carbonara", "Carbonara", "Carbonara", "Curry"])

    def test_without_a_dinner_today_and_tomorrow_starts_with_dinner(self):
        slots = [dated(TODAY, "lunch", "Linsensuppe"), dated(TODAY + timedelta(days=1), "dinner", "Pizza")]
        self.assertEqual([state(slots, h) for h in (8, 14, 21)], ["Linsensuppe", "Pizza", "Pizza"])

    def test_empty_week_and_nothing_planned_later(self):
        self.assertEqual(state([], 8), "–")
        self.assertEqual(state([dated(TODAY, "lunch", "Linsensuppe")], 15), "–")
        self.assertEqual(state([dated(TODAY - timedelta(days=1), "dinner", "Gestern")], 8), "–")

    def test_title_truncated_to_255_chars(self):
        self.assertEqual(state([dated(TODAY, "lunch", "x" * 300)], 8), "x" * 255)

    def test_attributes(self):
        sunday = date(2026, 10, 18)  # tomorrow lies in the next week: not in `week`, but in tomorrow_*
        slots = [dated(sunday, "dinner", "Eintopf"), dated(date(2026, 10, 12), "dinner", "Gemüsecurry"),
                 dated(sunday + timedelta(days=1), "lunch", "Salat"), dated(date(2026, 10, 12), "lunch", "Suppe")]
        s, attrs = planner.sensor_payload(slots, datetime(2026, 10, 18, 9, 0))
        self.assertEqual(s, "Eintopf")
        self.assertEqual(attrs, {
            "friendly_name": "Essensplan", "icon": "mdi:silverware-fork-knife",
            "today_lunch": None, "today_dinner": "Eintopf", "tomorrow_lunch": "Salat", "tomorrow_dinner": None,
            "week": [{"date": "2026-10-12", "meal": "lunch", "title": "Suppe"},
                     {"date": "2026-10-12", "meal": "dinner", "title": "Gemüsecurry"},
                     {"date": "2026-10-18", "meal": "dinner", "title": "Eintopf"}]})


class PlanDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)
        with self.conn:
            self.conn.executemany("INSERT INTO users (id, name, display_name, first_seen, last_seen) VALUES (?, ?, ?, 'x', 'x')",
                                  [("me", "me", "Me"), ("other", "other", "Other")])
        self.recipes = {}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def recipe(self, title):
        if title not in self.recipes:
            with self.conn:
                self.recipes[title] = self.conn.execute(
                    "INSERT INTO recipes (title, source_kind, servings, created_at, updated_at) "
                    "VALUES (?, 'manual', 2, 'x', 'x')", (title,)).lastrowid
        return self.recipes[title]

    def slot(self, day, meal, title, status="confirmed", active=1, skipped=0, guests=1, eaters=("me",)):
        """Put a recipe on a date (creates the plan of its week); cooked portions = eaters + guests."""
        week = planner.week_of(day)
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO plans (week, status) VALUES (?, ?)", (week, status))
            self.conn.execute("DELETE FROM plan_slots WHERE week = ? AND day = ? AND meal = ?", (week, day.weekday(), meal))
            self.conn.execute(
                "INSERT INTO plan_slots (week, day, meal, active, recipe_id, guests, skipped) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (week, day.weekday(), meal, active, self.recipe(title) if title else None, guests, skipped))
            self.conn.executemany("INSERT INTO slot_eaters (week, day, meal, user_id) VALUES (?, ?, ?, ?)",
                                  [(week, day.weekday(), meal, u) for u in eaters])

    def rate(self, user, title, stars=3):
        with self.conn:
            self.conn.execute("INSERT INTO ratings (user_id, recipe_id, stars, updated_at) VALUES (?, ?, ?, 'x')",
                              (user, self.recipe(title), stars))

    def rated(self, user="me"):
        return [(x["title"], x["date"], x["meal"]) for x in plans.rate_list(self.conn, user, TODAY)]


class RateListTest(PlanDataTest):
    def days_ago(self, n):
        return TODAY - timedelta(days=n)

    def test_unrated_meal_of_yesterday(self):
        self.slot(self.days_ago(1), "dinner", "Gestern")
        self.assertEqual(self.rated(), [("Gestern", "2026-10-13", "dinner")])
        self.assertEqual(plans.rate_list(self.conn, "me", TODAY)[0]["recipe_id"], self.recipe("Gestern"))

    def test_rated_by_me_is_left_out_rated_by_someone_else_is_not(self):
        self.slot(self.days_ago(1), "lunch", "Meins")
        self.slot(self.days_ago(1), "dinner", "Seins")
        self.rate("me", "Meins", 0)  # a 0 is a rating too
        self.rate("other", "Seins")
        self.assertEqual(self.rated("me"), [("Seins", "2026-10-13", "dinner")])
        self.assertEqual(self.rated("other"), [("Meins", "2026-10-13", "lunch")])

    def test_skipped_inactive_draft_and_empty_slots_are_left_out(self):
        self.slot(self.days_ago(1), "lunch", "Ausgefallen", skipped=1)
        self.slot(self.days_ago(1), "dinner", "Aus", active=0)
        self.slot(self.days_ago(8), "lunch", "Entwurf", status="draft")  # another week
        self.slot(self.days_ago(2), "dinner", None)
        self.assertEqual(self.rated(), [])

    def test_window_is_the_14_days_before_today(self):
        self.slot(self.days_ago(15), "dinner", "Zu alt")
        self.slot(self.days_ago(14), "dinner", "Gerade noch")
        self.slot(TODAY, "lunch", "Heute")
        self.slot(TODAY + timedelta(days=1), "lunch", "Morgen")
        self.assertEqual(self.rated(), [("Gerade noch", "2026-09-30", "dinner")])

    def test_one_entry_per_recipe_newest_first(self):
        self.slot(self.days_ago(5), "lunch", "Nudeln")
        self.slot(self.days_ago(2), "dinner", "Nudeln")
        self.slot(self.days_ago(3), "lunch", "Reis")
        self.slot(self.days_ago(2), "lunch", "Suppe")
        self.assertEqual(self.rated(), [("Nudeln", "2026-10-12", "dinner"), ("Suppe", "2026-10-12", "lunch"),
                                        ("Reis", "2026-10-11", "lunch")])

    def test_at_most_ten(self):
        for n in range(1, 8):
            self.slot(self.days_ago(n), "lunch", f"L{n}")
            self.slot(self.days_ago(n), "dinner", f"D{n}")
        listed = self.rated()
        self.assertEqual((len(listed), listed[0], listed[-1]), (10, ("D1", "2026-10-13", "dinner"), ("L5", "2026-10-09", "lunch")))

    def test_rating_removes_the_entry(self):
        self.slot(self.days_ago(1), "dinner", "Gestern")
        self.rate("me", "Gestern", 4)
        self.assertEqual(self.rated(), [])


class TodayViewTest(PlanDataTest):
    def test_today_and_tomorrow(self):
        self.slot(TODAY, "dinner", "Carbonara", status="draft", guests=2)
        self.slot(TODAY + timedelta(days=1), "lunch", "Curry")
        self.slot(TODAY + timedelta(days=1), "dinner", "Pizza", skipped=1)
        view = plans.today_view(self.conn, "me", TODAY)
        self.assertEqual(view["today"], {"date": "2026-10-14", "lunch": None, "dinner": {
            "recipe_id": self.recipe("Carbonara"), "title": "Carbonara", "image": None, "cooked_portions": 3}})
        self.assertEqual(view["tomorrow"], {"date": "2026-10-15", "dinner": None, "lunch": {
            "recipe_id": self.recipe("Curry"), "title": "Curry", "image": None, "cooked_portions": 2}})
        self.assertEqual(view["rate"], [])

    def test_does_not_create_plans(self):
        plans.today_view(self.conn, "me", TODAY)
        planner.sensor_payload(plans.sensor_slots(self.conn, TODAY), datetime(2026, 10, 14, 8))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0], 0)

    def test_tomorrow_in_the_next_week(self):
        sunday = date(2026, 10, 18)
        self.slot(sunday + timedelta(days=1), "lunch", "Montag")
        self.assertEqual(plans.today_view(self.conn, "me", sunday)["tomorrow"]["lunch"]["title"], "Montag")
        self.assertEqual([s["title"] for s in plans.sensor_slots(self.conn, sunday)], ["Montag"])


class WorkerSensorTest(PlanDataTest):
    def test_notify_sets_the_event(self):
        worker.plan_changed.clear()
        worker.notify_plan_changed()
        self.assertTrue(worker.plan_changed.is_set())
        worker.plan_changed.clear()

    def test_post_sensor_posts_the_payload(self):
        tomorrow = date.today() + timedelta(days=1)
        self.slot(tomorrow, "lunch", "Curry")
        with mock.patch.object(ha, "post_state") as post:
            worker.post_sensor(self.tmp.name)
        (entity, state_, attrs), _ = post.call_args
        self.assertEqual((entity, attrs["tomorrow_lunch"], attrs["friendly_name"]), ("sensor.essensplan", "Curry", "Essensplan"))
        self.assertIsInstance(state_, str)

    def test_post_failures_are_logged_not_raised(self):
        for error in (ha.HAError(None, "ha_unavailable"), ha.HAError(500, "ha_error"), RuntimeError("boom")):
            with mock.patch.object(ha, "post_state", side_effect=error), mock.patch("builtins.print") as out, \
                    mock.patch("traceback.print_exc"):
                worker.post_sensor(self.tmp.name)  # must not raise
            if isinstance(error, ha.HAError):
                self.assertIn("sensor post failed", out.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
