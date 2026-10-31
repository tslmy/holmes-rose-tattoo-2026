"""Synthetic archives exercise the shared VGS variant and upscaler contract."""

import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_rosetattoo_vgs_variants as variants


def sprite(first_pixel=1):
    # 2x2 normal-mode frame, including a transparent pixel and nonzero offsets.
    return struct.pack("<HHBBBB", 1, 1, 0, 0, 7, 9) + bytes([first_pixel, 255, 2, 3])


def rle_sprite():
    # skipPal=false permits RLE, and animImages=false keeps the 8-bit X offset.
    encoded = bytes([0, 1, 1, 1, 0, 2, 2, 3])
    return (struct.pack("<HHBBBBH", 1, 1, 0, 1, 7, 9, 11 + len(encoded))
            + b"\x00" + encoded)


def lzv_literals(data):
    # Literal-only LZV: the byte stream differs from the decompressed bytes.
    encoded = bytearray(b"LZV\x1a" + struct.pack("<I", len(data)))
    for offset in range(0, len(data), 8):
        encoded.append(0xFF)
        encoded.extend(data[offset:offset + 8])
    return bytes(encoded)


def library(entries):
    size = 6 + len(entries) * 17
    index = bytearray(b"LIB\x1a" + struct.pack("<H", len(entries)))
    contents = bytearray()
    for name, data in entries:
        index.extend(name.encode("ascii").ljust(13, b"\x00"))
        index.extend(struct.pack("<I", size + len(contents)))
        contents.extend(data)
    return bytes(index + contents)


def game_data(root):
    root.mkdir()
    originals = {name: sprite() for name in variants.RESOURCES}
    originals[variants.RESOURCES[1]] = rle_sprite()
    walk = dict(originals)
    walk[variants.RESOURCES[0]] = sprite(3)
    (root / "VGS.LIB").write_bytes(library([(name, lzv_literals(data)) for name, data in originals.items()]))
    (root / "WALK.LIB").write_bytes(library(list(walk.items())))
    room = bytearray(64)
    room[40:44] = struct.pack("<I", 64)
    room.extend(struct.pack("<HHHHHHIB", 0, 0, 0, 0, 0, 0, 0, 0))
    palette = bytearray(768)
    palette[3:12] = bytes([63, 0, 0, 0, 63, 0, 0, 0, 63])
    room.extend(palette)
    (root / "RES01.RRM").write_bytes(room)
    return originals, walk


class VgsVariantsTests(unittest.TestCase):
    def test_decoded_identity_dedup_provenance_pixels_and_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals, walk = game_data(root / "game")
            output = root / "output"
            manifest = variants.extract(root / "game", output, 1)
            self.assertEqual((manifest["resource_count"], manifest["variant_count"], manifest["frame_count"]), (5, 6, 6))
            self.assertEqual(variants.audit(root / "game", output, 1), manifest)
            for name in variants.RESOURCES:
                original_hash = hashlib.md5(originals[name], usedforsecurity=False).hexdigest()
                directory = output / variants.resource_path(name, original_hash)
                metadata = json.loads((directory / "metadata.json").read_text())
                self.assertEqual(metadata["resource_md5"], original_hash)
                self.assertEqual(metadata["decode_mode"], {"skipPal": False, "animImages": False})
                self.assertEqual(metadata["fallback_palette"], "scene:1")
                self.assertIn("dynamic palette", metadata["palette_note"])
                self.assertEqual(metadata["frames"][0], {"index": 0, "file": "frame_000.png", "width": 2,
                                                         "height": 2, "offset_x": 7, "offset_y": 9})
                sources = metadata["source_archives"]
                self.assertEqual(sources[0]["archive"], "VGS.LIB")
                self.assertNotEqual(sources[0]["stored_md5"], original_hash)
                if originals[name] == walk[name]:
                    self.assertEqual([s["archive"] for s in sources], ["VGS.LIB", "WALK.LIB"])
                    self.assertEqual(sources[1]["decompressed_md5"], original_hash)
                else:
                    self.assertEqual(len(sources), 1)
                    walk_hash = hashlib.md5(walk[name], usedforsecurity=False).hexdigest()
                    walk_metadata = json.loads((output / variants.resource_path(name, walk_hash) / "metadata.json").read_text())
                    self.assertEqual(walk_metadata["source_archives"][0]["archive"], "WALK.LIB")
                with Image.open(directory / "frame_000.png") as frame:
                    pixels = [frame.getpixel((x, y)) for y in range(2) for x in range(2)]
                    self.assertEqual(pixels, [(255, 0, 0, 255), (0, 0, 0, 0),
                                              (0, 255, 0, 255), (0, 0, 255, 255)])

    def test_never_overwrites_or_writes_into_game_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_data(root / "game")
            output = root / "existing"
            output.mkdir()
            (output / "marker").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                variants.extract(root / "game", output, 1)
            self.assertEqual((output / "marker").read_bytes(), b"keep")
            with self.assertRaises(ValueError):
                variants.extract(root / "game", root / "game" / "new", 1)
            with self.assertRaises(ValueError):
                variants.extract(root / "game", root, 1)
            link = root / "dangling"
            link.symlink_to(root / "missing")
            with self.assertRaises(FileExistsError):
                variants.extract(root / "game", link, 1)

    def test_missing_archive_entry_and_md5_collision_fail_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_data(root / "game")
            with patch.object(variants, "md5", return_value="0" * 32):
                with self.assertRaisesRegex(ValueError, "MD5 collision"):
                    variants.extract(root / "game", root / "collision", 1)
            self.assertFalse((root / "collision").exists())
            (root / "game" / "WALK.LIB").write_bytes(library([(variants.RESOURCES[0], sprite())]))
            with self.assertRaisesRegex(ValueError, "missing JTDOWNRG.VGS"):
                variants.extract(root / "game", root / "missing", 1)
            self.assertFalse((root / "missing").exists())

    def test_audit_rejects_changed_pixels_provenance_and_missing_variant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals, _ = game_data(root / "game")
            output = root / "output"
            variants.extract(root / "game", output, 1)
            digest = hashlib.md5(originals[variants.RESOURCES[1]], usedforsecurity=False).hexdigest()
            directory = output / variants.resource_path(variants.RESOURCES[1], digest)
            frame = directory / "frame_000.png"
            saved = frame.read_bytes()
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(frame)
            with self.assertRaisesRegex(ValueError, "pixels differ"):
                variants.audit(root / "game", output, 1)
            frame.write_bytes(saved)
            metadata_file = directory / "metadata.json"
            original_metadata = metadata_file.read_text()
            metadata = json.loads(original_metadata)
            metadata["source_archives"].pop()
            metadata_file.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                variants.audit(root / "game", output, 1)
            metadata_file.write_text(original_metadata)
            directory.rename(directory.with_name("incorrect-s0a0"))
            with self.assertRaisesRegex(ValueError, "variant directories"):
                variants.audit(root / "game", output, 1)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "upscaler requires numpy")
    def test_unmodified_upscaler_finds_nested_variants_and_preserves_paths(self):
        import upscale_rosetattoo_sprites as upscaler

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_data(root / "game")
            manifest = variants.extract(root / "game", root / "source", 1)
            model_path = root / "fake.pth"
            model_path.write_bytes(b"test checkpoint")
            args = ["upscaler", "--input-dir", str(root / "source"), "--output-dir", str(root / "scaled"),
                    "--scale", "2", "--device", "cpu", "--model-path", str(model_path)]
            def fake_inference(model, device, image, *args, **kwargs):
                return image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST)
            with patch.object(sys, "argv", args), patch.object(upscaler, "load_esrgan_model", return_value=None), \
                    patch.object(upscaler, "run_esrgan", side_effect=fake_inference):
                upscaler.main()
            for variant in manifest["variants"]:
                directory = root / "scaled" / variant["path"]
                with Image.open(directory / "frame_000@2x.png") as image:
                    self.assertEqual((image.mode, image.size), ("RGBA", (4, 4)))
                metadata = json.loads((directory / "metadata.json").read_text())
                self.assertEqual(metadata["frames"][0]["file"], "frame_000@2x.png")
                self.assertEqual(metadata["frames"][0]["offset_x"], 14)
                self.assertEqual(metadata["resource_md5"], variant["md5"])


if __name__ == "__main__":
    unittest.main()
