import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mealprep import db, ha, worker

BLUEPRINT = Path(__file__).resolve().parent.parent.parent / "blueprints/automation/mealprep_planner/share_to_inbox.yaml"
INBOX = "todo.inbox"


class ExtractUrlsTest(unittest.TestCase):
    def test_typical_share_texts(self):
        for text, expected in [
            ("Schau dir dieses Video an! https://vm.tiktok.com/ZMabc/ 😋", ["https://vm.tiktok.com/ZMabc/"]),
            ("a https://example.com/a und http://example.org/b?x=1.", ["https://example.com/a", "http://example.org/b?x=1"]),
            ("(siehe https://example.com/rezept), super!", ["https://example.com/rezept"]),
            ("Link: https://example.com/a;\nhttps://example.com/a", ["https://example.com/a"]),
            ("<https://example.com/x>", ["https://example.com/x"]),
            ("Kein Link hier, nur Text. ftp://example.com/x", []),
            ("", []),
        ]:
            self.assertEqual(worker.extract_urls(text), expected, text)

    def test_at_most_ten_urls(self):
        urls = worker.extract_urls("\n".join(f"https://example.com/{i}" for i in range(15)))
        self.assertEqual(len(urls), 10)

    def test_overlong_url_dropped(self):
        self.assertEqual(worker.extract_urls("https://example.com/" + "a" * 2100), [])


class StubInbox:
    """In-memory todo list standing in for HA: answers get_items, records remove_item."""

    def __init__(self, items, remove_fails=False):
        self.items, self.remove_fails, self.calls = items, remove_fails, []

    def __call__(self, domain, service, data, return_response=False, timeout=10):
        self.calls.append((domain, service, data))
        if service == "get_items":
            return {"service_response": {data["entity_id"]: {"items": list(self.items)}}}
        if self.remove_fails:
            raise ha.HAError(500, "ha_error")
        self.items = [i for i in self.items if i["uid"] != data["item"]]
        return {}


def item(summary, description="", uid="u1"):
    return {"summary": summary, "uid": uid, "status": "needs_action", "description": description}


class PollInboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def configure(self):
        with mock.patch.object(ha, "get_state", return_value={}):
            db.set_settings(self.conn, {"inbox_entity": INBOX})

    def jobs(self):
        return [tuple(r) for r in self.conn.execute("SELECT url, text, origin, status, created_by FROM import_jobs ORDER BY id")]

    def poll(self, stub):
        with mock.patch.object(ha, "call_service", stub):
            worker.poll_inbox(self.tmp.name)

    def test_no_inbox_entity_makes_no_ha_calls(self):
        stub = StubInbox([item("https://example.com/a")])
        self.poll(stub)
        self.assertEqual((stub.calls, self.jobs()), ([], []))

    def test_links_become_jobs_and_items_are_removed(self):
        self.configure()
        stub = StubInbox([
            item("https://vm.tiktok.com/ZMabc/", "Schau dir das an! https://vm.tiktok.com/ZMabc/ 😋", "u1"),
            item("Zwei", "https://example.com/a und https://example.com/b", "u2"),
        ])
        self.poll(stub)
        self.assertEqual(self.jobs(), [
            ("https://vm.tiktok.com/ZMabc/", None, "inbox", "queued", None),
            ("https://example.com/a", None, "inbox", "queued", None),
            ("https://example.com/b", None, "inbox", "queued", None)])
        self.assertEqual(stub.items, [])
        self.assertEqual(stub.calls[0], ("todo", "get_items", {"entity_id": INBOX, "status": ["needs_action"]}))
        self.assertEqual([c[2] for c in stub.calls[1:]], [{"entity_id": INBOX, "item": "u1"}, {"entity_id": INBOX, "item": "u2"}])

    def test_text_without_link_becomes_text_job(self):
        self.configure()
        recipe = "Pfannkuchen: 200 g Mehl, 2 Eier, 300 ml Milch"
        stub = StubInbox([item(recipe[:20], recipe, "u1"), item("zu kurz", "", "u2")])
        self.poll(stub)
        self.assertEqual(self.jobs(), [(None, recipe, "inbox", "queued", None)])
        self.assertEqual([i["uid"] for i in stub.items], ["u2"])  # nothing usable: left in the list

    def test_failed_removal_does_not_duplicate_the_job(self):
        self.configure()
        stub = StubInbox([item("https://example.com/a", "", "u1"), item("Ein Rezepttext mit genug Zeichen", "", "u2")],
                         remove_fails=True)
        with mock.patch("builtins.print"):
            self.poll(stub)
            self.poll(stub)
        self.assertEqual(len(self.jobs()), 2)
        stub.remove_fails = False
        self.poll(stub)
        self.assertEqual((len(self.jobs()), stub.items), (2, []))

    def test_old_job_does_not_block_a_new_share(self):
        self.configure()
        self.poll(StubInbox([item("https://example.com/a")]))
        self.conn.execute("UPDATE import_jobs SET created_at = '2000-01-01T00:00:00'")
        self.conn.commit()
        self.poll(StubInbox([item("https://example.com/a")]))
        self.assertEqual(len(self.jobs()), 2)

    def test_ha_errors_and_odd_responses_are_survived(self):
        self.configure()
        with mock.patch.object(ha, "call_service", side_effect=ha.HAError(None, "ha_unavailable")), \
                mock.patch("builtins.print"):
            worker.poll_inbox(self.tmp.name)
        with mock.patch.object(ha, "call_service", return_value={"service_response": []}), mock.patch("builtins.print"):
            worker.poll_inbox(self.tmp.name)
        stub = StubInbox(["junk", {"summary": None, "uid": 5, "description": 7}])
        self.poll(stub)
        self.assertEqual(self.jobs(), [])


class BlueprintTest(unittest.TestCase):
    def test_blueprint_has_the_moving_parts(self):
        text = BLUEPRINT.read_text(encoding="utf-8")
        for part in ("domain: automation", "event_type: mobile_app.share", "action: todo.add_item",
                     "entity_id: !input inbox", "domain: todo", "[:250]"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
