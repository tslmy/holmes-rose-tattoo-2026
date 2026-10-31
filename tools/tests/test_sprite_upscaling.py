"""Tests for the sprite upscaler's alpha and resumable output contract."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import upscale_rosetattoo_sprites as upscaler
from sprite_metadata import resource_metadata_paths


class SpriteUpscalingTests(unittest.TestCase):
    def setUp(self):
        self.frame = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
        for y in range(1, 4):
            for x in range(1, 4):
                self.frame.putpixel((x, y), (220, 30, 20, 255))

    def test_padding_extends_foreground_color_without_changing_alpha(self):
        padded = upscaler.extend_transparent_rgb(self.frame, radius=1)
        self.assertEqual(padded.getpixel((0, 2)), (220, 30, 20))
        self.assertEqual(self.frame.getpixel((0, 2)), (0, 0, 0, 0))

    def test_auto_mode_only_selects_fonts_for_font_resources(self):
        self.assertEqual(upscaler.resource_mode("auto", Path("font1_vgs")), "font")
        self.assertEqual(upscaler.resource_mode("auto", Path("FONT8_VGS")), "font")
        self.assertEqual(upscaler.resource_mode("auto", Path("scene_026/room_004")), "photo")
        self.assertEqual(upscaler.resource_mode("photo", Path("font1_vgs")), "photo")

    def test_nested_scene_summary_is_not_a_sprite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene_026"
            resource = scene / "room_004"
            resource.mkdir(parents=True)
            (scene / "metadata.json").write_text(json.dumps({"scene_id": 26, "rooms": []}))
            (resource / "metadata.json").write_text(json.dumps({"frames": []}))
            self.assertEqual(resource_metadata_paths(root), [resource / "metadata.json"])

    def test_refuses_to_replace_input_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            metadata = source / "metadata.json"
            metadata.write_text(json.dumps({"frames": []}))
            with self.assertRaisesRegex(ValueError, "must differ"):
                upscaler.upscale_resource_dir(source, source, 2, None, "cpu", "model")
            self.assertEqual(json.loads(metadata.read_text()), {"frames": []})

    @patch.object(upscaler, "run_esrgan_whole", side_effect=lambda model, device, image: image.resize(
        (image.width * 4, image.height * 4), Image.Resampling.NEAREST))
    def test_tiled_inference_preserves_dimensions_and_tile_placement(self, inference):
        source = Image.new("RGB", (13, 9))
        for y in range(source.height):
            for x in range(source.width):
                source.putpixel((x, y), (x * 10, y * 10, 0))
        tiled = upscaler.run_esrgan(None, "cpu", source, tile_size=4, tile_pad=2)
        expected = source.resize((52, 36), Image.Resampling.NEAREST)
        self.assertEqual(tiled.tobytes(), expected.tobytes())
        self.assertGreater(inference.call_count, 1)

    @patch.object(upscaler, "run_esrgan", side_effect=lambda model, device, image: image.resize((20, 20)))
    def test_soft_and_binary_alpha_preserve_size_and_foreground(self, inference):
        soft = upscaler.upscale_frame(self.frame, 2, None, "cpu", "soft")
        binary = upscaler.upscale_frame(self.frame, 2, None, "cpu", "binary")
        self.assertEqual(soft.size, (10, 10))
        self.assertEqual(soft.getpixel((5, 5))[:3], (220, 30, 20))
        soft_alpha = np.asarray(soft.getchannel("A"))
        self.assertTrue(((soft_alpha > 0) & (soft_alpha < 255)).any())
        self.assertLessEqual(set(np.asarray(binary.getchannel("A")).flat), {0, 255})

    @patch.object(upscaler, "run_esrgan", side_effect=lambda model, device, image: image.resize((20, 20)))
    def test_resume_checks_signature_and_writes_complete_metadata(self, inference):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            self.frame.save(source / "frame_000.png")
            (source / "metadata.json").write_text(json.dumps({
                "frames": [{"file": "frame_000.png", "width": 5, "height": 5,
                            "offset_x": -2, "offset_y": 3}]
            }))
            upscaler.upscale_resource_dir(source, output, 2, None, "cpu", "model", alpha_mode="soft")
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertTrue(metadata["complete"])
            self.assertEqual(metadata["frames"][0]["offset_x"], -4)
            self.assertEqual(metadata["alpha_mode"], "soft")
            inference.reset_mock()
            upscaler.upscale_resource_dir(source, output, 2, None, "cpu", "model", alpha_mode="soft", skip_existing=True)
            inference.assert_not_called()
            with self.assertRaisesRegex(ValueError, "alpha_mode differs"):
                upscaler.upscale_resource_dir(source, output, 2, None, "cpu", "model", alpha_mode="binary", skip_existing=True)
            self.frame.putpixel((2, 2), (0, 255, 0, 255))
            self.frame.save(source / "frame_000.png")
            upscaler.upscale_resource_dir(source, output, 2, None, "cpu", "model", alpha_mode="soft", skip_existing=True)
            inference.assert_called_once()

    @patch.object(upscaler, "run_esrgan", side_effect=lambda model, device, image: image.resize((20, 20)))
    def test_identical_frames_are_reused_without_sharing_output_files(self, inference):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = {}
            for name in ("a", "b"):
                source = root / name
                source.mkdir()
                self.frame.save(source / "frame_000.png")
                (source / "metadata.json").write_text(json.dumps({
                    "frames": [{"file": "frame_000.png", "width": 5, "height": 5,
                                "offset_x": 0, "offset_y": 0}]
                }))
                upscaler.upscale_resource_dir(source, root / (name + "-out"), 2, None, "cpu", "model",
                                               alpha_mode="soft", frame_cache=cache)
            inference.assert_called_once()
            a = root / "a-out" / "frame_000@2x.png"
            b = root / "b-out" / "frame_000@2x.png"
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertNotEqual(a.stat().st_ino, b.stat().st_ino)

    @patch.object(upscaler, "run_esrgan")
    def test_invisible_placeholder_does_not_run_the_model(self, inference):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            Image.new("RGBA", (5, 5), (0, 0, 0, 0)).save(source / "frame_000.png")
            (source / "metadata.json").write_text(json.dumps({
                "frames": [{"file": "frame_000.png", "width": 5, "height": 5,
                            "offset_x": 0, "offset_y": 0}]
            }))
            output = root / "output"
            upscaler.upscale_resource_dir(source, output, 2, None, "cpu", "model", alpha_mode="soft")
            inference.assert_not_called()
            with Image.open(output / "frame_000@2x.png") as frame:
                self.assertEqual(frame.size, (10, 10))
                self.assertIsNone(frame.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
