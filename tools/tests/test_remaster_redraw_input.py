"""Regression test (iteration 2): the redraw input is rebuilt from the current plate, never a stale cache.

Iteration 2 re-prepared every plate without animated objects, but redraw_bg reused the cached input.png
(same size), so the model redrew the old plate with its people while QA compared against the new one.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import imagegen  # noqa: E402
from remaster.config import load_config  # noqa: E402
from remaster.stages import brief, prepare, redraw_bg  # noqa: E402

CONFIG = """
[bakeoff]
seed = 7
backends = ["web"]
levels = ["L2"]
[image.backends.web]
tool = "http"
url = "http://x/v1/images/generations"
model = "m"
api_key_file = "{tmp}/key"
"""


class Stop(Exception):
    pass


class RedrawInputTest(unittest.TestCase):
    def test_stale_cached_input_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "remaster.toml").write_text(CONFIG.replace("{tmp}", tmp))
            (Path(tmp) / "key").write_text("k")
            cfg = load_config(Path(tmp) / "remaster.toml")
            prep = prepare.out_root(cfg) / "scene_012"
            prep.mkdir(parents=True)
            Image.new("RGB", (96, 64), (10, 200, 30)).save(prep / "plate_dedither.png")  # the new plate
            (prep / "plate.json").write_text('{"layers": [], "skipped": [], "size": [96, 64]}')
            out = brief.out_dir(cfg, 12)
            out.mkdir(parents=True)
            (out / "visual_brief.txt").write_text("BRIEF")
            size = redraw_bg.size_for(cfg, 12)
            stale = redraw_bg.input_path(cfg, 12)
            stale.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, (255, 0, 255)).save(stale)  # the old plate, already at the right size
            seen = {}

            def fake_generate(cfg_, backend, *, images, **kwargs):
                seen["pixels"] = np.asarray(Image.open(images[0]).convert("RGB"))
                raise Stop

            with mock.patch.object(imagegen, "generate", side_effect=fake_generate):
                with self.assertRaises(Stop):
                    redraw_bg.run_job(cfg, {"scene": 12, "backend": "web", "level": "L2", "seed": 7}, "fp")
            centre = seen["pixels"][seen["pixels"].shape[0] // 2, seen["pixels"].shape[1] // 2]
            self.assertEqual(tuple(int(v) for v in centre), (10, 200, 30))


if __name__ == "__main__":
    unittest.main()
