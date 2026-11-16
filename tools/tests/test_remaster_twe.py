"""Acceptance test for delegate task `sprite-twe` (Phase 1b). Written by Opus; do not edit to make it pass."""

import inspect
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.stages import sprite_test  # noqa: E402


def texture(seed, size=(160, 200)):
    rng = np.random.default_rng(seed)
    t = rng.uniform(0, 255, (size[0], size[1], 3)).astype(np.float32)
    return cv2.GaussianBlur(t, (0, 0), 1.5)


def sequence(textures, n=6, step=3):
    """A textured square figure moving `step` px right per frame on grey."""
    frames, alphas = [], []
    for k in range(n):
        img = np.full((120, 160, 3), 128, np.float32)
        a = np.zeros((120, 160), np.float32)
        x = 30 + k * step
        img[30:90, x:x + 60] = textures[k][30:90, 30:90]
        a[30:90, x:x + 60] = 1.0
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
        alphas.append(a)
    return frames, alphas


class TweTest(unittest.TestCase):
    def test_crawling_texture_scores_far_worse_than_moving_texture(self):
        same = texture(1)
        moving = [np.roll(same, 3 * k, axis=1) for k in range(6)]
        orig, alphas = sequence(moving)
        floor = sprite_test.temporal_warp_error(orig, alphas)
        consistent = sprite_test.temporal_warp_error([np.clip(f * 0.9 + 10, 0, 255).astype(np.uint8) for f in orig],
                                                     alphas, flow_frames=orig)
        crawl, _ = sequence([np.roll(texture(10 + k), 3 * k, axis=1) for k in range(6)])
        crawling = sprite_test.temporal_warp_error(crawl, alphas, flow_frames=orig)
        for r in (floor, consistent, crawling):
            self.assertEqual(set(r) >= {"e", "e_hf", "e_hf_max", "coverage", "pairs"}, True)
        self.assertEqual(floor["pairs"], 6)                    # 5 consecutive pairs + the loop pair
        self.assertGreater(floor["coverage"], 0.2)
        self.assertLess(consistent["e_hf"] / max(floor["e_hf"], 1e-6), 1.5)
        self.assertGreater(crawling["e_hf"] / max(floor["e_hf"], 1e-6), 3.0)

    def test_no_loop_pair(self):
        orig, alphas = sequence([texture(1)] * 6)
        self.assertEqual(sprite_test.temporal_warp_error(orig, alphas, loop=False)["pairs"], 5)

    def test_run_test_gates_on_ratio(self):
        src = inspect.getsource(sprite_test.run_test)
        self.assertIn("temporal_warp_error(", src)
        self.assertIn("twe_ratio", src)
        self.assertIn("sprite_max_twe_ratio", src)


if __name__ == "__main__":
    unittest.main()
