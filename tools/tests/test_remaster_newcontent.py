"""Acceptance test for delegate task `qa-new-content` (Phase 1b). Written by Opus; do not edit to make it pass."""

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.stages import qa  # noqa: E402
from remaster.warp import GridWarp  # noqa: E402


class NewContentTest(unittest.TestCase):
    def setUp(self):
        self.warp = GridWarp.identity(100, 80, (2.0, 2.0), 20)     # original 100x80 -> picture 200x160
        self.queries = ["table", "lamp"]
        # original: a table at (10, 40, 20, 10) original px
        self.orig = [{"query": 0, "score": 0.5, "box": [10, 40, 20, 10]}]
        self.final = [{"query": 0, "score": 0.6, "box": [21, 79, 40, 21]},     # same table, picture px
                      {"query": 1, "score": 0.4, "box": [120, 100, 20, 40]},   # new lamp
                      {"query": 1, "score": 0.1, "box": [150, 10, 10, 10]}]    # too weak to count

    def test_new_objects_are_those_without_a_match(self):
        found = qa.new_objects(self.orig, self.final, self.warp, self.queries)
        self.assertEqual([(n["label"], n["box"]) for n in found], [("lamp", [120, 100, 20, 40])])

    def test_blocking_only_where_they_meet_walk_zones_or_hotspots(self):
        new = qa.new_objects(self.orig, self.final, self.warp, self.queries)
        walk = [[[100, 120], [200, 120], [200, 160], [100, 160]]]   # bottom strip, overlaps half the lamp
        self.assertEqual(len(qa.blocking(new, walk, [], (200, 160))), 1)
        far = [[[0, 0], [50, 0], [50, 20], [0, 20]]]
        self.assertEqual(qa.blocking(new, far, far, (200, 160)), [])

    def test_queries_and_gates_exist(self):
        for q in ("table", "chair", "lamp", "statue", "person"):
            self.assertIn(q, qa.OBSTACLE_QUERIES)
        self.assertIn("new_light_sources", qa.VLM_DIFF)
        src = inspect.getsource(qa.evaluate)
        self.assertIn('gates["obstacles"]', src)
        self.assertIn('gates["light"]', src)


if __name__ == "__main__":
    unittest.main()
