import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assemble_rosetattoo_modernized import assemble


class ModernizedAssemblyTests(unittest.TestCase):
    def test_build_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backgrounds = root / "backgrounds" / "scene_026"
            room = root / "room" / "scene_026" / "room_004"
            backgrounds.mkdir(parents=True)
            room.mkdir(parents=True)
            Image.new("RGB", (6, 4)).save(backgrounds / "background@2x.png")
            Image.new("RGBA", (6, 4)).save(room / "frame_000@2x.png")
            (room / "metadata.json").write_text(json.dumps({
                "complete": True, "scale": 2,
                "frames": [{"file": "frame_000@2x.png", "width": 6, "height": 4}]
            }))
            destination = root / "pack"
            manifest = assemble(backgrounds.parent, [root / "room"], destination, 2)
            self.assertEqual((manifest["background_count"], manifest["sprite_frame_count"]), (1, 1))
            self.assertEqual(manifest["sprite_provenance"][0]["resource_count"], 1)
            self.assertTrue((destination / "sprites/scene_026/room_004/frame_000@2x.png").exists())
            with self.assertRaises(FileExistsError):
                assemble(backgrounds.parent, [root / "room"], destination, 2)

    def test_incomplete_run_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backgrounds = root / "backgrounds" / "scene_026"
            room = root / "room" / "scene_026" / "room_004"
            backgrounds.mkdir(parents=True)
            room.mkdir(parents=True)
            Image.new("RGB", (6, 4)).save(backgrounds / "background@2x.png")
            (room / "metadata.json").write_text(json.dumps({"complete": False, "scale": 2, "frames": []}))
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                assemble(backgrounds.parent, [root / "room"], root / "pack", 2)
            self.assertFalse((root / "pack").exists())


if __name__ == "__main__":
    unittest.main()
