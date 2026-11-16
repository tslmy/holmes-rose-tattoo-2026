"""Acceptance test for delegate task `paste-relight` (Phase 1b). Written by Opus; do not edit to make it pass."""

import inspect
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.stages import geometry  # noqa: E402


class RelightTest(unittest.TestCase):
    def scene(self):
        rng = np.random.default_rng(0)
        base = rng.uniform(40, 200, (120, 160, 3)).astype(np.float32)   # the ESRGAN baseline, warped
        gain, offset = np.array([0.6, 0.8, 1.3]), np.array([20.0, -10.0, 5.0])
        target = base * gain + offset                                   # the redraw relit the whole room
        mask = np.zeros((120, 160), np.float32)
        mask[40:80, 50:110] = 1.0
        target[40:80, 50:110] = 255.0                                   # the redraw's own (garbled) sign
        return base, target, mask, gain, offset

    def test_patch_takes_the_relight_measured_around_it(self):
        base, target, mask, gain, offset = self.scene()
        out = geometry.relight_patch(base, target, mask)
        self.assertEqual(out.shape, base.shape)
        want = base[40:80, 50:110] * gain + offset
        self.assertLess(float(np.abs(out[40:80, 50:110] - want).mean()), 2.0)

    def test_gain_is_clipped(self):
        base, target, mask, _, _ = self.scene()
        out = geometry.relight_patch(base, base * 5.0, mask)   # absurd relight: gain must clip to 2.0
        contrast = out[40:80, 50:110].std() / base[40:80, 50:110].std()
        self.assertAlmostEqual(float(contrast), 2.0, places=2)

    def test_empty_ring_returns_patch_unchanged(self):
        base, target, _, _, _ = self.scene()
        full = np.ones(base.shape[:2], np.float32)
        np.testing.assert_allclose(geometry.relight_patch(base, target, full), base)

    def test_estimate_relights_before_pasting(self):
        self.assertIn("relight_patch(", inspect.getsource(geometry.estimate))


if __name__ == "__main__":
    unittest.main()
