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
            (room / "palette.bin").write_bytes(b"palette")
            (room / "metadata.json").write_text(json.dumps({
                "complete": True, "scale": 2,
                "sidecars": ["palette.bin"],
                "alpha_refinement": {"method": "native-alpha-lanczos-gaussian", "radius": 0.55,
                                     "thin_guard": True, "preserve_support": True},
                "frames": [{"file": "frame_000@2x.png", "width": 6, "height": 4}]
            }))
            destination = root / "pack"
            manifest = assemble(backgrounds.parent, [root / "room"], destination, 2)
            self.assertEqual((manifest["background_count"], manifest["sprite_frame_count"]), (1, 1))
            self.assertEqual(manifest["sprite_provenance"][0]["resource_count"], 1)
            self.assertEqual(manifest["sprite_provenance"][0]["alpha_radius"], 0.55)
            self.assertTrue(manifest["sprite_provenance"][0]["alpha_thin_guard"])
            self.assertTrue(manifest["sprite_provenance"][0]["alpha_preserve_support"])
            self.assertTrue((destination / "sprites/scene_026/room_004/frame_000@2x.png").exists())
            self.assertEqual((destination / "sprites/scene_026/room_004/palette.bin").read_bytes(), b"palette")
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

    def test_selects_entire_compatible_baseline_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backgrounds = root / "backgrounds" / "scene_044"
            candidate = root / "candidate" / "scene_044" / "canim_004"
            baseline = root / "baseline" / "scene_044" / "canim_004"
            for path in (backgrounds, candidate, baseline):
                path.mkdir(parents=True)
            Image.new("RGB", (6, 4)).save(backgrounds / "background@2x.png")
            frame = {"index": 0, "file": "frame_000@2x.png", "width": 6, "height": 4}
            common = {"complete": True, "scale": 2, "frames": [frame]}
            (baseline / "metadata.json").write_text(json.dumps(common))
            (candidate / "metadata.json").write_text(json.dumps({**common, "alpha_refinement": {"method": "contour"}}))
            Image.new("RGBA", (6, 4), (12, 34, 56, 255)).save(baseline / frame["file"])
            Image.new("RGBA", (6, 4), (200, 30, 40, 255)).save(candidate / frame["file"])
            destination = root / "pack"
            manifest = assemble(backgrounds.parent, [root / "candidate"], destination, 2,
                                {"scene_044/canim_004": root / "baseline"})
            with Image.open(destination / "sprites/scene_044/canim_004/frame_000@2x.png") as selected:
                self.assertEqual(selected.getpixel((0, 0)), (12, 34, 56, 255))
            self.assertEqual(manifest["sprite_fallbacks"][0]["resource"], "scene_044/canim_004")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                assemble(backgrounds.parent, [root / "candidate"], root / "candidate" / "pack", 2)

    def test_rejects_mismatched_fallback_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backgrounds = root / "backgrounds" / "scene_044"
            candidate = root / "candidate" / "scene_044" / "canim_004"
            baseline = root / "baseline" / "scene_044" / "canim_004"
            for path in (backgrounds, candidate, baseline):
                path.mkdir(parents=True)
            Image.new("RGB", (6, 4)).save(backgrounds / "background@2x.png")
            (candidate / "metadata.json").write_text(json.dumps({"complete": True, "scale": 2, "frames": []}))
            (baseline / "metadata.json").write_text(json.dumps({"complete": True, "scale": 4, "frames": []}))
            with self.assertRaisesRegex(ValueError, "metadata differs"):
                assemble(backgrounds.parent, [root / "candidate"], root / "pack", 2,
                         {"scene_044/canim_004": root / "baseline"})


if __name__ == "__main__":
    unittest.main()
