"""Coverage checks must distinguish a complete pack from missing assets."""

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin

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
                "sidecars": ["palette.bin"],
                "frames": [{"file": "frame_000.png", "width": 3, "height": 2},
                           {"file": "frame_001.png", "width": 3, "height": 2}]
            }))
            (pack / "scene_026").mkdir(parents=True)
            Image.new("RGB", (12, 8)).save(pack / "scene_026" / "background@2x.png")
            frames = pack / "sprites" / "scene_026" / "room_004"
            frames.mkdir(parents=True)
            (room / "palette.bin").write_bytes(b"palette")
            (frames / "palette.bin").write_bytes(b"palette")
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
            (frames / "palette.bin").write_bytes(b"different")
            self.assertEqual(audit_sprites(room.parent.parent, pack, 2)["problems"][0]["problem"],
                             "sidecar differs from source")

    def test_source_hash_detects_stale_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "character"
            destination = root / "pack" / "sprites" / "character"
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            image = Image.new("RGBA", (2, 1), (12, 34, 56, 255))
            image.save(source / "frame_000.png")
            (source / "metadata.json").write_text(json.dumps({"frames": [
                {"file": "frame_000.png", "width": 2, "height": 1}
            ]}))
            digest = hashlib.sha256(b"2x1:" + image.tobytes()).hexdigest()
            info = PngImagePlugin.PngInfo()
            info.add_text("source_sha256", digest)
            Image.new("RGBA", (4, 2)).save(destination / "frame_000@2x.png", pnginfo=info)
            self.assertFalse(audit_sprites(source.parent, root / "pack", 2, verify_source=True)["problems"])
            Image.new("RGBA", (2, 1), (0, 0, 0, 255)).save(source / "frame_000.png")
            problem = audit_sprites(source.parent, root / "pack", 2, verify_source=True)["problems"][0]
            self.assertEqual(problem["problem"], "source pixel hash missing or mismatched")


if __name__ == "__main__":
    unittest.main()
