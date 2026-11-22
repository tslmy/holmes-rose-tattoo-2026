"""Synthetic tests for the palette-qualified walk source contract."""

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_rosetattoo_palette_walks as walks
import audit_rosetattoo_palette_walks as audit_walks


def sprite(first=0, embedded=False):
    palette = (struct.pack("<HHBBBB", 389, 1, 0, 0, 0, 0) + b"VGA palette\0"
               + bytes([63, 0, 63]) * 256) if embedded else b""
    # The second frame adds a used index which is absent from the first.
    return (palette + struct.pack("<HHBBBB", 1, 1, 0, 0, 7, 9) + bytes([first, 9, 255, 0])
            + struct.pack("<HHBBBB", 0, 0, 0, 0, 4, 5) + bytes([42]))


def sequence(*names):
    records = []
    for name in names:
        records.append(name.encode("ascii").ljust(9, b"\0") + b"\x01" + struct.pack("<H", 3)
                       + b"\0" * 4 + b"\x01\x02\x00")
    return bytes([len(records)]) + b"".join(records) + b"unused trailing data"


def lzv_literals(data):
    return b"LZV\x1a" + struct.pack("<I", len(data)) + b"".join(
        b"\xff" + data[pos:pos + 8] for pos in range(0, len(data), 8))


def library(entries, lic=False):
    prefix = b"LIC\x1a" if lic else b"LIB\x1a"
    padding = b"\0" * ((len(entries) + 1) * 8) if lic else b""
    size = 6 + len(padding) + len(entries) * 17
    index = bytearray(prefix + struct.pack("<H", len(entries)) + padding)
    contents = bytearray()
    for name, data in entries:
        index.extend(name.encode("ascii").ljust(13, b"\0"))
        index.extend(struct.pack("<I", size + len(contents)))
        contents.extend(data)
    return bytes(index + contents)


def room(path, nine, unused=0):
    header = bytearray(64)
    header[40:44] = struct.pack("<I", 64)
    palette = bytearray(768)
    palette[0:3] = bytes([63, 1, 0])
    palette[9 * 3:9 * 3 + 3] = bytes(nine)
    palette[42 * 3:42 * 3 + 3] = bytes([1, 2, 3])
    palette[10 * 3] = unused
    path.write_bytes(header + bytes(17) + palette)


def game(root):
    root.mkdir()
    seq = sequence("ALPHA", "BETA", "ALPHA")
    a = sprite(embedded=True)
    b = sprite()
    other_a = sprite(first=9, embedded=True)
    (root / "vgs.lib").write_bytes(library([
        ("FIRST.SEQ", seq), ("ALPHA.VGS", lzv_literals(a)), ("BETA.VGS", lzv_literals(b))]))
    (root / "walk.lib").write_bytes(library([
        ("FIRST.SEQ", seq), ("TINYWALK.SEQ", b"legacy"), ("ALPHA.VGS", other_a), ("BETA.VGS", b)], lic=True))
    room(root / "res01.rrm", (0, 63, 0))
    room(root / "RES02.RRM", (0, 63, 0), unused=1)
    room(root / "RES03.RRM", (0, 0, 63))
    return a, b, other_a


class PaletteWalkTests(unittest.TestCase):
    def test_exact_payload_and_index_zero_nine_transparent(self):
        frames, _ = walks.decode_vgs(sprite())
        self.assertEqual(walks.used_indices(frames), [0, 9, 42])
        palette = bytes(i % 256 for i in range(768))
        payload = walks.palette_payload([0, 9], palette)
        expected_bits = bytearray(32)
        expected_bits[0] = 1
        expected_bits[1] = 2
        expected = b"RT-PAL1\0" + bytes(expected_bits) + palette[:3] + palette[27:30]
        self.assertEqual(payload, expected)
        self.assertEqual(walks.md5(payload), hashlib.md5(expected, usedforsecurity=False).hexdigest())
        for invalid in ([255], [9, 0], [0, 0], [-1], [256]):
            with self.assertRaises(ValueError):
                walks.palette_payload(invalid, palette)
        altered = bytearray(palette)
        altered[0] ^= 1
        with patch.object(walks, "md5", return_value="0" * 32):
            with self.assertRaisesRegex(ValueError, "palette MD5 collision"):
                walks.project_palettes([0], {1: palette, 2: bytes(altered)})

    def test_archive_identity_scene_dedup_path_pixels_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b, other_a = game(root / "game")
            out = root / "out"
            result = walks.extract(root / "game", out, selected_resources=["alpha.vgs", "BETA.VGS"])
            self.assertEqual((result["scene_count"], result["resource_count"], result["identity_count"],
                              result["projection_count"], result["frame_count"]), (3, 2, 3, 6, 12))
            self.assertEqual(result["scenes"], [1, 2, 3])
            self.assertEqual(result["excluded_sequences"]["TINYWALK.SEQ"], "legacy non-Tattoo sequence layout")
            for entry in result["projections"]:
                self.assertEqual(entry["scenes"] in ([1, 2], [3]), True)
                directory = out / entry["path"]
                payload = (directory / "palette.bin").read_bytes()
                self.assertEqual(entry["palette_md5"], hashlib.md5(payload, usedforsecurity=False).hexdigest())
                self.assertEqual(directory.relative_to(out), walks.resource_path(entry["resource"], entry["resource_md5"], entry["palette_md5"]))
                metadata = json.loads((directory / "metadata.json").read_text())
                self.assertEqual(metadata["sidecars"], ["palette.bin"])
                self.assertEqual(metadata["used_indices"], [0, 9, 42])
                self.assertEqual(metadata["source_scenes"], entry["scenes"])
                self.assertEqual(metadata["decode_mode"], {"skipPal": False, "animImages": False})
                self.assertEqual(metadata["frames"][0]["offset_x"], 7)
                self.assertEqual(metadata["frames"][0]["offset_y"], 9)
                self.assertEqual(metadata["frames"][1]["offset_x"], 4)
                self.assertEqual(len(metadata["sequence_references"]), 2)
                if entry["resource"] == "BETA.VGS":
                    self.assertEqual(len(metadata["source_archives"]), 2)
                    self.assertEqual(entry["resource_md5"], walks.md5(b))
                else:
                    self.assertEqual(len(metadata["source_archives"]), 1)
                    self.assertIn(entry["resource_md5"], (walks.md5(a), walks.md5(other_a)))
                    self.assertTrue(metadata["embedded_palette"])
                with Image.open(directory / "frame_000.png") as image:
                    self.assertEqual((image.mode, image.size), ("RGBA", (2, 2)))
                    expected_nine = (0, 255, 0, 255) if entry["scenes"] == [1, 2] else (0, 0, 255, 255)
                    self.assertEqual(image.getpixel((1, 0)), expected_nine)
                    self.assertEqual(image.getpixel((0, 1)), (0, 0, 0, 0))
                    self.assertEqual(image.getpixel((1, 1)), (255, 4, 0, 255))
                with Image.open(directory / "frame_001.png") as image:
                    self.assertEqual(image.size, (1, 1))
                    self.assertEqual(image.getpixel((0, 0)), (4, 8, 12, 255))
            self.assertEqual(json.loads((out / "manifest.json").read_text()), result)
            self.assertEqual(audit_walks.audit(root / "game", out), result)

    def test_audit_catches_changed_sidecar_frame_and_extra_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game(root / "game")
            out = root / "out"
            result = walks.extract(root / "game", out, [1], ["BETA.VGS"])
            directory = out / result["projections"][0]["path"]
            sidecar = directory / "palette.bin"
            original = sidecar.read_bytes()
            sidecar.write_bytes(original + b"x")
            with self.assertRaisesRegex(ValueError, "palette sidecar"):
                audit_walks.audit(root / "game", out)
            sidecar.write_bytes(original)
            frame = directory / "frame_000.png"
            original = frame.read_bytes()
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(frame)
            with self.assertRaisesRegex(ValueError, "pixels differ"):
                audit_walks.audit(root / "game", out)
            frame.write_bytes(original)
            (directory / "extra.txt").write_text("unexpected")
            with self.assertRaisesRegex(ValueError, "unexpected paths"):
                audit_walks.audit(root / "game", out)

    def test_rle_nibble_invalid_and_embedded_palette_modes(self):
        # RLE pixels 0 and 9 are opaque; skipped pixels are transparent.
        encoded = bytes([0, 2, 0, 9, 2])
        rle = struct.pack("<HHBBBBH", 3, 0, 0, 1, 3, 4, 11 + len(encoded)) + b"\0" + encoded
        frames, embedded = walks.decode_vgs(rle)
        self.assertFalse(embedded)
        self.assertEqual(frames[0].indices, bytes([0, 9, 255, 255]))
        self.assertEqual(frames[0].record["encoding"], "rle")
        nibble = struct.pack("<HHBBBB", 2, 0, 1, 0, 2, 3) + b"\x90"
        self.assertEqual(walks.decode_vgs(nibble)[0][0].indices, bytes([0, 9, 255]))
        with self.assertRaises(ValueError):
            walks.decode_vgs(rle[:-1])
        with self.assertRaises(ValueError):
            walks.decode_frame(b"\0\0", 1, 1, "rle")

    def test_safety_bad_selection_and_corrupt_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game(root / "game")
            (root / "existing").mkdir()
            (root / "existing" / "marker").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                walks.extract(root / "game", root / "existing", selected_resources=["BETA.VGS"])
            self.assertEqual((root / "existing" / "marker").read_bytes(), b"keep")
            with self.assertRaises(ValueError):
                walks.extract(root / "game", root / "game" / "new")
            with self.assertRaises(FileExistsError):
                walks.extract(root / "game", root, selected_resources=["BETA.VGS"])
            (root / "link").symlink_to(root / "missing")
            with self.assertRaises(FileExistsError):
                walks.extract(root / "game", root / "link")
            for scenes, resources in [([1, 1], ["BETA.VGS"]), ([999], ["BETA.VGS"]),
                                      (None, ["../BETA.VGS"]), (None, ["UNKNOWN.VGS"])]:
                with self.assertRaises(ValueError):
                    walks.extract(root / "game", root / "invalid", scenes, resources)
                self.assertFalse((root / "invalid").exists())
            with patch.object(walks, "md5", return_value="0" * 32):
                with self.assertRaisesRegex(ValueError, "MD5 collision"):
                    walks.extract(root / "game", root / "collision", selected_resources=["ALPHA.VGS"])
            self.assertFalse((root / "collision").exists())
            with self.assertRaises(ValueError):
                walks.decompress(b"LZV\x1a\x04\x00\x00\x00\xff\x01")
            with self.assertRaises(ValueError):
                walks.parse_sequence(b"\x01broken")
            libraries = [walks.Library(root / "game" / "vgs.lib"), walks.Library(root / "game" / "walk.lib")]
            del libraries[1].entries["ALPHA.VGS"]
            with patch.object(walks, "AMBIGUOUS", frozenset(["ALPHA.VGS"])):
                with self.assertRaisesRegex(ValueError, "missing required ambiguous variant"):
                    walks.collect_variants(libraries, ["ALPHA.VGS"])
            (root / "game" / "walk.lib").unlink()
            with self.assertRaisesRegex(ValueError, "missing required library"):
                walks.extract(root / "game", root / "missing-library", selected_resources=["BETA.VGS"])
            self.assertFalse((root / "missing-library").exists())


if __name__ == "__main__":
    unittest.main()
