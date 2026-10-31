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
    def test_scale2x_reconstructs_corner_without_changing_flat_regions(self):
        native = np.array([[0, 0, 0], [0, 255, 255], [0, 255, 255]], dtype=np.uint8)
        result = refinement.scale2x_mask(native)
        self.assertEqual(result.shape, (6, 6))
        self.assertEqual(result[2, 2], 0)
        self.assertEqual(result[2, 3], 255)
        self.assertEqual(result[3, 2], 255)
        self.assertEqual(result[3, 3], 255)
        self.assertTrue((result[:2, :] == 0).all())

    def test_scale4_requires_binary_source_and_exact_2x_output(self):
        source = Image.new("L", (3, 3), 0)
        source.putpixel((1, 1), 128)
        with self.assertRaisesRegex(ValueError, "binary native alpha"):
            refinement.refine_alpha(source, (6, 6), 0.35, thin_guard=True, method="scale4-box")
        source.putpixel((1, 1), 255)
        with self.assertRaisesRegex(ValueError, "requires 2x output"):
            refinement.refine_alpha(source, (12, 12), 0.35, thin_guard=True, method="scale4-box")

    def test_constant_alpha_skips_geometric_work(self):
        for value in (0, 255):
            source = Image.new("L", (100, 100), value)
            with patch.object(refinement, "scale2x_mask", side_effect=AssertionError("no contour")):
                result = refinement.refine_alpha(source, (200, 200), 0.35,
                                                 thin_guard=True, method="scale4-box", preserve_support=True)
            self.assertEqual(result.getextrema(), (value, value))

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

    def test_scale4_metadata_and_rgb_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, enhanced = self.prepare(root)
            output = root / "contour"
            result = refinement.refine_resource(source, enhanced, output, 2, 0.35,
                                               thin_guard=True, method="scale4-box")
            self.assertEqual(result["alpha_refinement"]["method"], "native-alpha-scale4-box")
            self.assertTrue(result["alpha_refinement"]["thin_guard"])
            with Image.open(enhanced / "frame_000@2x.png") as before, Image.open(output / "frame_000@2x.png") as after:
                self.assertTrue(np.array_equal(np.asarray(before)[..., :3], np.asarray(after)[..., :3]))
                self.assertEqual(before.info["source_sha256"], after.info["source_sha256"])

    def test_preserve_support_keeps_original_coverage(self):
        source = Image.new("L", (9, 9), 0)
        for y in range(2, 7):
            for x in range(2, 7):
                if x + y >= 7:
                    source.putpixel((x, y), 255)
        baseline = np.asarray(refinement.refine_alpha(source, (18, 18), 0), dtype=np.uint8)
        candidate = np.asarray(refinement.refine_alpha(source, (18, 18), 0.35, thin_guard=True,
                                                        method="scale4-box", preserve_support=True), dtype=np.uint8)
        self.assertTrue(np.array_equal(baseline != 0, candidate != 0))
        self.assertFalse(np.array_equal(baseline, candidate))

    def test_contour_rejects_previously_modified_input_alpha(self):
        from PIL import PngImagePlugin
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, enhanced = self.prepare(root)
            path = enhanced / "frame_000@2x.png"
            with Image.open(path) as image:
                frame = image.copy()
                source_hash = image.info["source_sha256"]
            frame.putpixel((0, 0), (*frame.getpixel((0, 0))[:3], 255))
            info = PngImagePlugin.PngInfo()
            info.add_text("source_sha256", source_hash)
            frame.save(path, pnginfo=info)
            with self.assertRaisesRegex(ValueError, "unmodified Lanczos alpha"):
                refinement.refine_resource(source, enhanced, root / "contour", 2, 0.35,
                                           thin_guard=True, method="scale4-box", preserve_support=True)


if __name__ == "__main__":
    unittest.main()
