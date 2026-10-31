import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import refine_rosetattoo_sprite_alpha as refinement
import upscale_rosetattoo_sprites as upscaler


class SpriteAlphaRefinementTests(unittest.TestCase):
    def test_thin_guard_reduces_stroke_opacity_loss(self):
        source = Image.new("L", (11, 11), 0)
        for y in range(2, 9):
            source.putpixel((5, y), 255)
        baseline = np.asarray(refinement.refine_alpha(source, (22, 22), 0), dtype=np.int16)
        unguarded = np.asarray(refinement.refine_alpha(source, (22, 22), 0.55), dtype=np.int16)
        guarded = np.asarray(refinement.refine_alpha(source, (22, 22), 0.55, thin_guard=True), dtype=np.int16)
        region = np.s_[6:16, 10:12]
        self.assertLess(np.abs(guarded[region] - baseline[region]).mean(),
                        np.abs(unguarded[region] - baseline[region]).mean())

    def prepare(self, root: Path):
        source, enhanced = root / "source", root / "enhanced"
        source.mkdir()
        frame = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
        for y in range(1, 4):
            for x in range(1, 4):
                frame.putpixel((x, y), (210, 45, 20, 255))
        frame.save(source / "frame_000.png")
        (source / "palette.bin").write_bytes(b"palette")
        (source / "metadata.json").write_text(json.dumps({
            "sidecars": ["palette.bin"],
            "frames": [{"file": "frame_000.png", "width": 5, "height": 5,
                        "offset_x": -2, "offset_y": 3}]
        }))
        with patch.object(upscaler, "run_esrgan", side_effect=lambda model, device, image: image.resize((20, 20))):
            upscaler.upscale_resource_dir(source, enhanced, 2, None, "cpu", "model", alpha_mode="soft")
        return source, enhanced

    def test_refines_alpha_only_and_retains_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, enhanced = self.prepare(root)
            output = root / "output"
            result = refinement.refine_resource(source, enhanced, output, 2, 0.55)
            self.assertTrue(result["complete"])
            self.assertEqual(result["frames"][0]["offset_x"], -4)
            self.assertEqual(result["frames"][0]["offset_y"], 6)
            self.assertEqual((output / "palette.bin").read_bytes(), b"palette")
            name = "frame_000@2x.png"
            with Image.open(enhanced / name) as old, Image.open(output / name) as new:
                self.assertTrue(np.array_equal(np.asarray(old)[..., :3], np.asarray(new)[..., :3]))
                self.assertFalse(np.array_equal(np.asarray(old)[..., 3], np.asarray(new)[..., 3]))
                self.assertEqual(old.info["source_sha256"], new.info["source_sha256"])
                self.assertIn("alpha_input_sha256", new.info)
            before = (output / name).read_bytes()
            refinement.refine_resource(source, enhanced, output, 2, 0.55, skip_existing=True)
            self.assertEqual(before, (output / name).read_bytes())
            with self.assertRaisesRegex(ValueError, "settings differ"):
                refinement.refine_resource(source, enhanced, output, 2, 0.8, skip_existing=True)
            with self.assertRaisesRegex(ValueError, "settings differ"):
                refinement.refine_resource(source, enhanced, output, 2, 0.55, skip_existing=True, thin_guard=True)

    def test_refuses_untrusted_inputs_and_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, enhanced = self.prepare(root)
            with self.assertRaisesRegex(ValueError, "must differ"):
                refinement.refine_resource(source, enhanced, enhanced, 2, 0.55)
            (enhanced / "palette.bin").write_bytes(b"different")
            with self.assertRaisesRegex(ValueError, "differs from source"):
                refinement.refine_resource(source, enhanced, root / "output", 2, 0.55)
            self.assertFalse((root / "output").exists())

    def test_bitmap_font_alpha_is_not_feathered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, enhanced = self.prepare(root)
            metadata = json.loads((enhanced / "metadata.json").read_text())
            metadata["upscaler"] = "lanczos-alpha"
            (enhanced / "metadata.json").write_text(json.dumps(metadata))
            output = root / "output"
            result = refinement.refine_resource(source, enhanced, output, 2, 0.55)
            self.assertEqual(result["alpha_refinement"]["method"], "preserve-font-alpha")
            with Image.open(enhanced / "frame_000@2x.png") as before, Image.open(output / "frame_000@2x.png") as after:
                self.assertEqual(before.tobytes(), after.tobytes())


if __name__ == "__main__":
    unittest.main()
