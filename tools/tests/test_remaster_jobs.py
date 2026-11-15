"""Job queue, runner recovery and review queue; uses a throwaway config."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import runner
from remaster.config import load_config
from remaster.jobs import JobQueue, JobSpec
from remaster.review import ReviewItem, ReviewQueue

TOOLS = Path(__file__).resolve().parents[1]


def write_config(root: Path, min_free: float = 0) -> Path:
    path = root / "remaster.toml"
    path.write_text(f'[paths]\nwork = "work"\nreview = "review"\n[thresholds]\nmin_free_disk_gb = {min_free}\n')
    return path


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = JobQueue(Path(self.tmp.name) / "jobs.sqlite")

    def tearDown(self):
        self.queue.close()
        self.tmp.cleanup()

    def test_enqueue_is_idempotent_and_fingerprint_reruns(self):
        spec = JobSpec("s", "a", {"x": 1}, "fp1")
        self.assertEqual(self.queue.enqueue(spec), "pending")
        job = self.queue.claim_next()
        self.queue.complete(job.id, {"ok": True})
        self.assertEqual(self.queue.enqueue(spec), "done")
        self.assertEqual(self.queue.enqueue(JobSpec("s", "a", {"x": 1}, "fp2")), "pending")

    def test_dependencies_gate_claiming(self):
        self.queue.enqueue(JobSpec("s", "b", after=["s:a"]))
        self.queue.enqueue(JobSpec("s", "a"))
        first = self.queue.claim_next()
        self.assertEqual(first.id, "s:a")
        self.assertIsNone(self.queue.claim_next())
        self.queue.complete(first.id)
        self.assertEqual(self.queue.claim_next().id, "s:b")

    def test_recover_dead_runner_and_retry_limit(self):
        self.queue.enqueue(JobSpec("s", "a"), max_attempts=2)
        job = self.queue.claim_next()
        self.queue.set_pid(job.id, 999999)  # no such process: the runner "crashed"
        self.assertEqual(self.queue.recover(), ["s:a"])
        self.assertEqual(self.queue.get("s:a").status, "pending")
        job = self.queue.claim_next()
        self.assertEqual(self.queue.fail(job.id, "boom"), "failed")

    def test_requeue_by_review_tag(self):
        self.queue.enqueue(JobSpec("s", "a", tags=["backend-choice"]))
        self.queue.enqueue(JobSpec("s", "b"))
        for _ in range(2):
            self.queue.complete(self.queue.claim_next().id)
        self.assertEqual(self.queue.requeue_tag("backend-choice"), ["s:a"])
        self.assertEqual(self.queue.get("s:b").status, "done")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = write_config(self.root)
        self.env = dict(os.environ, PYTHONPATH=str(TOOLS), REMASTER_CAFFEINATED="1")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, **kwargs):
        return subprocess.Popen([sys.executable, "-m", "remaster.cli", "--config", str(self.config), *args],
                                cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, **kwargs)

    def test_runner_resumes_after_kill(self):
        proc = self.cli("selftest", "--count", "3", "--seconds", "1.5", start_new_session=True)
        out = self.root / "work" / "selftest"
        deadline = time.time() + 30
        while not (out / "job_00.txt").exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue((out / "job_00.txt").exists(), proc.stdout.read() if proc.poll() is not None else "")
        os.killpg(proc.pid, signal.SIGKILL)  # runner and its child die mid-job
        proc.wait()
        proc.stdout.close()
        cfg = load_config(self.config)
        queue = runner.queue_for(cfg)
        statuses = {job.key: job.status for job in queue.iter_jobs()}
        queue.close()
        self.assertEqual(statuses["job/00"], "done")
        self.assertIn("running", statuses.values())
        resumed = self.cli("run")
        output, _ = resumed.communicate(timeout=60)
        self.assertEqual(resumed.returncode, 0, output)
        self.assertIn("recovered selftest:job/01", output)
        queue = runner.queue_for(cfg)
        self.assertEqual(queue.counts()["done"], 3)
        queue.close()
        starts = (out / "starts.log").read_text().split()
        self.assertEqual(starts.count("0"), 1)  # the finished job was not redone

    def test_low_disk_pauses_and_queues_review_item(self):
        write_config(self.root, min_free=10 ** 9)
        proc = self.cli("selftest", "--count", "1", "--seconds", "0")
        output, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, runner.EXIT_DISK, output)
        items = json.loads((self.root / "review" / "queue.json").read_text())["items"]
        self.assertEqual(items[0]["id"], "disk-low")
        self.assertIn("disk-low", (self.root / "review" / "queue.md").read_text())


class ReviewTest(unittest.TestCase):
    def test_add_keeps_decision_and_effective_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = ReviewQueue(Path(tmp) / "review", Path(tmp))
            review.add(ReviewItem(id="backend", kind="decision", title="t", default="zimage"))
            self.assertEqual(review.effective("backend"), "zimage")
            _, overturned = review.decide("backend", "flux2")
            self.assertTrue(overturned)
            review.add(ReviewItem(id="backend", kind="decision", title="t2", default="zimage"))
            self.assertEqual(review.effective("backend"), "flux2")
            self.assertIn("overturned", (Path(tmp) / "review" / "queue.md").read_text())
            self.assertTrue((Path(tmp) / "review" / "sheet.html").exists())

    def test_numbers_resolve_and_thumbnails_are_embedded(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGB", (64, 48), (200, 10, 10)).save(root / "pic.png")
            review = ReviewQueue(root / "review", root)
            review.add(ReviewItem(id="first", kind="decision", title="a", default="x"))
            review.add(ReviewItem(id="second", kind="decision", title="b", default="y", images=["pic.png"]))
            _, overturned = review.decide("1", "keep")
            self.assertFalse(overturned)
            self.assertEqual(review.get("first").status, "confirmed")
            self.assertFalse(review.decide("1", "  Yes ")[1])
            self.assertFalse(review.decide("1", "x")[1])  # the default text itself
            self.assertFalse(review.decide("1", "keep, but it hardly matters now.")[1])
            self.assertTrue(review.decide("1", "keeper model")[1])
            item, _ = review.decide("#2", "z")
            self.assertEqual(item.id, "second")
            self.assertEqual(review.resolve("1"), "first")
            with self.assertRaises(KeyError):
                review.resolve("3")
            sheet = (root / "review" / "sheet.html").read_text()
            self.assertIn('src="data:image/jpeg;base64,', sheet)
            self.assertIn("#2", (root / "review" / "queue.md").read_text())


if __name__ == "__main__":
    unittest.main()
