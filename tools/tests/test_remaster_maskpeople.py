"""Acceptance test for delegate task `mask-people` (Phase 1b). Written by Opus; do not edit to make it pass."""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.config import load_config  # noqa: E402
from remaster.stages import brief, geometry, prepare, redraw_bg  # noqa: E402


class MaskPeopleTest(unittest.TestCase):
    def build(self, tmp):
        path = Path(tmp) / "remaster.toml"
        path.write_text('[paths]\nwork = "work"\n')
        cfg = load_config(path)
        out = brief.out_dir(cfg, 4)
        out.mkdir(parents=True)
        (out / "objects.json").write_text(json.dumps({"size": [64, 48], "objects": [
            {"id": "o01", "label": "Constable", "query": "police officer", "kind": "person", "box": [10, 8, 12, 30]},
            {"id": "o02", "label": "Hydrant", "query": "fire hydrant", "kind": "object", "box": [40, 30, 6, 10]}]}))
        prep = prepare.out_root(cfg) / "scene_004"
        prep.mkdir(parents=True)
        layers = np.zeros((48, 64), np.uint8)
        layers[16:30, 14:18] = 3              # the constable's exact silhouette is layer 3, inside his box
        Image.fromarray(layers).save(prep / "plate_layers.png")
        return cfg

    def test_people_mask_uses_layer_silhouette_inside_person_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.build(tmp)
            m = redraw_bg.people_mask(cfg, 4)
            self.assertEqual(m.shape, (48, 64))
            self.assertEqual(m.dtype, np.uint8)
            self.assertEqual(m[20, 15], 255)          # on the silhouette
            self.assertEqual(m[35, 40], 0)            # hydrant is not a person
            self.assertEqual(m[9, 10], 0)             # inside the box but far off the silhouette (dilate <= 3 px)
            big = redraw_bg.people_mask(cfg, 4, (128, 96))
            self.assertEqual(big.shape, (96, 128))
            self.assertEqual(big[40, 30], 255)

    def test_people_mask_falls_back_to_box_without_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.build(tmp)
            Image.fromarray(np.zeros((48, 64), np.uint8)).save(
                prepare.out_root(cfg) / "scene_004" / "plate_layers.png")
            m = redraw_bg.people_mask(cfg, 4)
            self.assertEqual(m[9, 11], 255)

    def test_strip_people_keeps_style_paragraph(self):
        text = ("STYLE sentence with real-looking people in period costume.\n\n"
                "A grey stone facade. A uniformed police officer stands in the doorway. A black hydrant stands left.")
        out = redraw_bg.strip_people(text)
        self.assertTrue(out.startswith("STYLE sentence with real-looking people in period costume."))
        self.assertIn("A grey stone facade.", out)
        self.assertIn("A black hydrant stands left.", out)
        self.assertNotIn("officer", out)

    def test_run_job_and_geometry_handle_mask_people(self):
        run = inspect.getsource(redraw_bg.run_job)
        self.assertIn("mask_people", run)
        self.assertIn("inpaint", run)
        self.assertIn("strip_people(", inspect.getsource(redraw_bg))
        self.assertIn("people_mask(", inspect.getsource(geometry.estimate))


if __name__ == "__main__":
    unittest.main()
