"""Coverage checks must distinguish a complete pack from missing assets."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_rosetattoo_pack import audit_backgrounds, audit_sprites


class PackAuditTests(unittest.TestCase):
    def test_room_frames_and_backgrounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backgrounds = root / "backgrounds" / "scene_26"
            room = root / "rooms" / "scene_026" / "room_004"
            pack = root / "pack"
            backgrounds.mkdir(parents=True)
            room.mkdir(parents=True)
            Image.new("RGB", (6, 4)).save(backgrounds / "background.png")
            (room / "metadata.json").write_text(json.dumps({
                "frames": [{"file": "frame_000.png", "width": 3, "height": 2},
                           {"file": "frame_001.png", "width": 3, "height": 2}]
            }))
            (pack / "scene_026").mkdir(parents=True)
            Image.new("RGB", (12, 8)).save(pack / "scene_026" / "background@2x.png")
            frames = pack / "sprites" / "scene_026" / "room_004"
            frames.mkdir(parents=True)
            Image.new("RGBA", (6, 4)).save(frames / "frame_000@2x.png")
            self.assertEqual(audit_backgrounds(backgrounds.parent, pack, 2)["valid"], 1)
            partial = audit_sprites(room.parent.parent, pack, 2)
            self.assertEqual((partial["expected"], partial["valid"]), (2, 1))
            self.assertEqual(partial["problems"][0]["problem"], "missing")
            self.assertEqual(audit_sprites(room.parent.parent, pack, 2, {1})["expected"], 0)
            self.assertEqual(audit_backgrounds(backgrounds.parent, pack, 2, {1})["expected"], 0)
            self.assertEqual(audit_backgrounds(backgrounds.parent, pack, 2, {1})["problems"][0]["problem"],
                             "selected source scene is missing")
            Image.new("RGBA", (6, 4)).save(frames / "frame_001@2x.png")
            self.assertFalse(audit_sprites(room.parent.parent, pack, 2)["problems"])


if __name__ == "__main__":
    unittest.main()
