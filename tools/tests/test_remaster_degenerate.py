"""Acceptance test for delegate task `degenerate` (Phase 1c): a degenerate SIFT homography must not
poison object prediction or the warp fit. Written by Opus; do not edit it to make it pass."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.stages import geometry  # noqa: E402

SIZE = (640, 480)  # width, height: deliberately not square


class DegenerateTest(unittest.TestCase):
    def test_sane_homographies_pass(self):
        self.assertFalse(geometry.homography_degenerate(np.eye(3), SIZE))
        shift = np.array([[1.0, 0, 12], [0, 1.0, -7], [0, 0, 1]])
        self.assertFalse(geometry.homography_degenerate(shift, SIZE))
        zoom = np.array([[1.3, 0, -90], [0, 1.3, -70], [0, 0, 1]])
        self.assertFalse(geometry.homography_degenerate(zoom, SIZE))

    def test_degenerate_homographies_fail(self):
        # the one measured on scene 1 under ControlNet (scale 0.017, strong perspective)
        bad = np.array([[-26.77, 10.96, 599.1], [-14.08, 5.76, 315.1], [-0.0447, 0.0183, 1.0]])
        self.assertTrue(geometry.homography_degenerate(bad, SIZE))
        self.assertTrue(geometry.homography_degenerate(np.diag([0.1, 0.1, 1.0]), SIZE))   # shrinks 10x
        self.assertTrue(geometry.homography_degenerate(np.diag([-1.0, 1.0, 1.0]), SIZE))  # mirror
        far = np.array([[1.0, 0, 400], [0, 1.0, 0], [0, 0, 1]])  # centre moves > 25% of the width
        self.assertTrue(geometry.homography_degenerate(far, SIZE))
        self.assertTrue(geometry.homography_degenerate(np.full((3, 3), np.nan), SIZE))

    def test_estimate_uses_the_check(self):
        source = Path(geometry.__file__).read_text()
        body = source.split("def estimate")[1]
        self.assertIn("homography_degenerate(", body)
        self.assertIn('"degenerate"', body)


if __name__ == "__main__":
    unittest.main()
