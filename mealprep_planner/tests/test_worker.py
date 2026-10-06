import json
import tempfile
import unittest
from unittest import mock

from mealprep import db, importer, worker


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(self.tmp.name)
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def job(self, job_id):
        return self.conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()

    def test_queued_to_review_with_draft(self):
        (job_id,) = worker.enqueue(self.conn, ["https://example.com/a"], "single", None)
        self.assertEqual(self.job(job_id)["status"], "queued")
        seen = []

        def build(job, conn, data_dir):
            seen.append(self.job(job_id)["status"])  # running while the build is in progress
            return {"title": "T"}

        with mock.patch.object(importer, "build_draft", build):
            self.assertTrue(worker.process_one(self.tmp.name))
        self.assertEqual(seen, ["running"])
        row = self.job(job_id)
        self.assertEqual((row["status"], row["error"], json.loads(row["draft"])), ("review", None, {"title": "T"}))

    def test_failures_store_the_error_code(self):
        ids = worker.enqueue(self.conn, ["https://example.com/a", "https://example.com/b"], "bulk", None)
        errors = [importer.FetchError("fetch_blocked"), RuntimeError("boom")]
        with mock.patch.object(importer, "build_draft", side_effect=errors), mock.patch("traceback.print_exc"):
            self.assertTrue(worker.process_one(self.tmp.name))
            self.assertTrue(worker.process_one(self.tmp.name))
        self.assertEqual([(self.job(i)["status"], self.job(i)["error"]) for i in ids],
                         [("failed", "fetch_blocked"), ("failed", "fetch_failed")])

    def test_oldest_first_and_idle(self):
        first, second = worker.enqueue(self.conn, ["https://example.com/a", "https://example.com/b"], "bulk", None)
        with mock.patch.object(importer, "build_draft", return_value={"title": "T"}):
            worker.process_one(self.tmp.name)
            self.assertEqual((self.job(first)["status"], self.job(second)["status"]), ("review", "queued"))
            self.assertTrue(worker.process_one(self.tmp.name))
            self.assertFalse(worker.process_one(self.tmp.name))

    def test_discarded_while_running_stays_discarded(self):
        (job_id,) = worker.enqueue(self.conn, ["https://example.com/a"], "single", None)

        def build(job, conn, data_dir):
            conn.execute("UPDATE import_jobs SET status = 'discarded' WHERE id = ?", (job_id,))
            conn.commit()
            return {"title": "T"}

        with mock.patch.object(importer, "build_draft", build):
            worker.process_one(self.tmp.name)
        self.assertEqual(self.job(job_id)["status"], "discarded")

    def test_running_jobs_are_requeued_at_start(self):
        (job_id,) = worker.enqueue(self.conn, ["https://example.com/a"], "single", None)
        self.conn.execute("UPDATE import_jobs SET status = 'running' WHERE id = ?", (job_id,))
        self.conn.commit()
        worker.reset_running(self.conn)
        self.assertEqual(self.job(job_id)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
