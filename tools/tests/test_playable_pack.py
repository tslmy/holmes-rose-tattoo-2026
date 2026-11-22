"""The remaster pack builder puts per-room overrides where the runtime reads them: sprites/<resource>/."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PlayablePackTest(unittest.TestCase):
    def test_room_objects_and_canims_go_under_sprites(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bg, temporal, out = tmp / "bg", tmp / "temporal", tmp / "pack"
            for rel in ("scene_029/background@2x.png", "scene_029/room_004/frame_000@2x.png",
                        "scene_029/canim_002/frame_001@2x.png", "scene_028/background@2x.png",
                        "scene_029/pick.json"):
                (bg / rel).parent.mkdir(parents=True, exist_ok=True)
                (bg / rel).write_bytes(b"x")
            (temporal / "scene_029" / "room_004").mkdir(parents=True)
            (temporal / "scene_029" / "room_004" / "frame_000@2x.png").write_bytes(b"old")
            subprocess.run([sys.executable, str(ROOT / "tools/build_remaster_pack.py"),
                            "--background-dir", str(bg), "--sprite-dir", str(temporal), "--output-dir", str(out)],
                           check=True, capture_output=True)
            self.assertTrue((out / "scene_029/background@2x.png").is_file())
            self.assertTrue((out / "scene_028/background@2x.png").is_file())
            self.assertEqual((out / "sprites/scene_029/room_004/frame_000@2x.png").read_bytes(), b"x")
            self.assertTrue((out / "sprites/scene_029/canim_002/frame_001@2x.png").is_file())
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["room_override_frames_copied"], 2)


if __name__ == "__main__":
    unittest.main()
