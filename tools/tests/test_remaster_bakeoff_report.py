"""`remaster bakeoff-report`: end-to-end report on a throwaway repo.

Builds a minimal remaster.toml and the artifacts `bakeoff.collect()` reads,
then checks the picks, the contact sheet, results.md and the review queue.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import bakeoff  # noqa: E402
from remaster.config import load_config  # noqa: E402
from remaster.stages import baseline, geometry, prepare, qa, redraw_bg  # noqa: E402

CONFIG = """\
[paths]
work = "work"
review = "review"

[golden]
scenes = [4]

[bakeoff]
backends = ["a", "b"]
levels = ["L0", "L2"]

[image.backends.a]
tool = "http"
url = "http://127.0.0.1:9/v1/images/generations"
model = "a"

[image.backends.b]
tool = "http"
url = "http://127.0.0.1:9/v1/images/generations"
model = "b"
"""


def png(path: Path, color=(120, 180, 90)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 18), color).save(path, "PNG")


def build_repo(root: Path) -> None:
    (root / "remaster.toml").write_text(CONFIG, encoding="utf-8")
    cfg = load_config(root / "remaster.toml")
    scene, backend = 4, "a"
    for level in ("L0", "L2"):
        red = redraw_bg.job_dir(cfg, scene, backend, level)
        red.mkdir(parents=True, exist_ok=True)
        (red / "record.json").write_text(json.dumps(
            {"status": "ok", "seconds": 10, "peak_memory_gb": None}), encoding="utf-8")
        gdir = geometry.job_dir(cfg, scene, backend, level)
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "geometry.json").write_text(json.dumps(
            {"summary": {"status": "ok", "disp_p95": 1.0, "max_stretch": 1.1}}), encoding="utf-8")
        png(gdir / "final.png", (120, 180, 90))
        png(gdir / "overlay.png", (230, 200, 60))
        q = {"summary": {"pass": True, "score": 0.8, "gates": {"warp": True}}} if level == "L0" \
            else {"summary": {"pass": False, "score": 0.6, "gates": {"warp": False}}}
        qdir = qa.job_dir(cfg, scene, backend, level)
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "qa.json").write_text(json.dumps(q), encoding="utf-8")

    for level in ("L0", "L2"):
        red = redraw_bg.job_dir(cfg, scene, "b", level)
        red.mkdir(parents=True, exist_ok=True)
        (red / "record.json").write_text(json.dumps(
            {"status": "unavailable", "reason": "no API key"}), encoding="utf-8")

    png(prepare.out_root(cfg) / "scene_004" / "plate.png", (90, 110, 160))
    png(baseline.out_root(cfg) / "scene_004" / "plate_esrgan.png", (100, 120, 170))


class ReportTest(unittest.TestCase):
    def test_report_picks_outputs_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_repo(root)
            cfg = load_config(root / "remaster.toml")
            result = bakeoff.report(cfg, queue=True)

            # 1. picks
            picks = result["picks"]
            self.assertEqual(picks["backend"], "a")
            self.assertEqual(picks["levels"][4]["level"], "L0")

            # 2. contact sheet
            sheet = cfg.work / "bakeoff" / "contact.html"
            self.assertTrue(sheet.exists())
            page = sheet.read_text(encoding="utf-8")
            self.assertIn("Scene 4", page)
            self.assertIn("unavailable: no API key", page)
            match = re.search(r'<img[^>]*src="([^"]+)"', page)
            self.assertIsNotNone(match)
            self.assertTrue((cfg.work / "bakeoff" / match.group(1)).exists(),
                            f"src {match.group(1)!r} missing relative to work/bakeoff/")

            # 3. results.md
            md = (cfg.work / "bakeoff" / "results.md").read_text(encoding="utf-8")
            self.assertIn("| `a` |", md)
            self.assertIn("no API key", md)

            # 4. review queue
            queue = json.loads((cfg.review_dir / "queue.json").read_text(encoding="utf-8"))
            ids = [item["id"] for item in queue["items"]]
            for expected in ("bakeoff-backend", "bakeoff-room-levels", "bakeoff-sprite-route"):
                self.assertIn(expected, ids)
            room = next(i for i in queue["items"] if i["id"] == "bakeoff-room-levels")
            self.assertIn("4: L0", room["default"])


if __name__ == "__main__":
    unittest.main()
