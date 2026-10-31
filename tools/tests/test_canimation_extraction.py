"""Synthetic room/cAnim fixtures exercise the streaming format end-to-end."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_rosetattoo_canimations as extractor
import upscale_rosetattoo_sprites as upscaler


def lz_literals(data):
    result = bytearray()
    for pos in range(0, len(data), 8):
        part = data[pos:pos + 8]
        result.append((1 << len(part)) - 1)
        result.extend(part)
    return bytes(result)


def block(data):
    compressed = lz_literals(data)
    return struct.pack("<I", len(compressed)) + compressed


def frame(width, height, payload, palette_base=0, rle=0, x=0, y=0):
    return struct.pack("<HHBBBBHB", width - 1, height - 1, palette_base, rle,
                       x, y, len(payload) + 11, 42) + payload


def record(name, size, x, y, flags=0, scale=256):
    return (struct.pack("<12sIhhBh", name, size, x, y, flags, scale)
            + struct.pack("<12h", *range(12)))


def room_fixture(compressed, malformed=False):
    # First slot is empty/disabled; second slot must remain canim_001.
    rle_frame = frame(4, 2, bytes([1, 2, 1, 2, 1, 0, 4, 3, 255, 1, 2]),
                      rle=1, x=7, y=9)
    nibble_frame = frame(3, 1, bytes([0x21]), palette_base=1, x=200, y=4)
    if malformed:
        nibble_frame = nibble_frame[:-1]
    stream = (block(rle_frame) + block(nibble_frame) if compressed
              else rle_frame + nibble_frame)
    table = record(b"disabled", 0, -1, 0) + record(b"action", len(stream), 15, 25, 4, 128)
    palette = bytearray(768)
    palette[3:6] = bytes([63, 0, 0])
    palette[6:9] = bytes([0, 31, 0])
    palette[9:12] = bytes([0, 0, 63])
    header_offset = 64
    head = bytearray(header_offset)
    head[39] = int(compressed)
    struct.pack_into("<I", head, 40, header_offset)
    header = struct.pack("<6HIB", 1, 1, 2, 1, 1, 0, 0, 0)
    make_block = block if compressed else lambda data: data
    # The uncompressed loader consumes 591 bytes per shape, not 625.
    shapes = bytes(625 if compressed else extractor.OBJECT_SIZE)
    body = (header + palette + make_block(bytes(640 * 480))
            + struct.pack("<IB9s", 3, 1, b"shape") + make_block(shapes)
            + make_block(b"D") + make_block(b"S") + make_block(b"IMG")
            + make_block(table))
    data_offset = len(head) + len(body)
    struct.pack_into("<II", head, 44, 0, data_offset)
    return bytes(head) + body + stream


class CAnimationExtractionTests(unittest.TestCase):
    def test_lz_literals_and_overlapping_backreference(self):
        # Literal A followed by six bytes copied from the overlapping window.
        self.assertEqual(extractor.decompress_lz(bytes([1, 65, 0xEE, 0xF3]), 7), b"AAAAAAA")
        self.assertEqual(extractor.decompress_lz(lz_literals(b"hello world"), 11), b"hello world")
        with self.assertRaises(extractor.FormatError):
            extractor.decompress_lz(bytes([1, 65, 0xEE, 0xF3]), 6)

    def test_compressed_and_uncompressed_streams(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                data = room_fixture(compressed)
                flag, palette, entries = extractor.parse_room(data)
                self.assertEqual(flag, compressed)
                self.assertEqual(len(entries), 2)
                self.assertFalse(entries[0]["runtime_enabled"])
                self.assertEqual(entries[1]["position"], {"x": 15, "y": 25})
                self.assertEqual(entries[1]["flags"], 4)
                self.assertEqual(entries[1]["goto"][0], {"x": 0, "y": 1, "facing": 2})
                offset, size = entries[1]["data_offset"], entries[1]["data_size"]
                frames = list(extractor.decode_frames(data[offset:offset + size], flag, palette, offset))
                self.assertEqual(len(frames), 2)
                first, image = frames[0]
                self.assertEqual((first["index"], first["offset_x"], first["offset_y"]), (0, 7, 9))
                self.assertEqual(first["stream_offset"], offset)
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
                self.assertEqual(image.getpixel((1, 0)), (255, 0, 0, 255))
                self.assertEqual(image.getpixel((2, 0)), (0, 125, 0, 255))
                self.assertEqual(image.getpixel((1, 1)), (0, 0, 0, 0))
                second, image = frames[1]
                self.assertEqual((second["index"], second["offset_x"]), (1, 200))
                self.assertEqual([image.getpixel((x, 0)) for x in range(3)],
                                 [(255, 0, 0, 255), (0, 125, 0, 255), (0, 0, 0, 0)])

    def test_raw_frame_and_reject_bad_rle(self):
        palette = [0] * 768
        palette[12:15] = [4, 5, 6]
        records = list(extractor.decode_frames(frame(2, 1, bytes([4, 255])), False, palette))
        self.assertEqual([records[0][1].getpixel((x, 0)) for x in range(2)],
                         [(4, 5, 6, 255), (0, 0, 0, 0)])
        with self.assertRaises(extractor.FormatError):
            extractor.decode_indices(bytes([3]), 2, 1, 0, True)
        with self.assertRaises(extractor.FormatError):
            extractor.decode_indices(bytes([0, 0]), 2, 1, 0, True)
        with self.assertRaises(extractor.FormatError):
            list(extractor.decode_frames(frame(2, 1, bytes([4])), False, palette))

    @patch.object(upscaler, "run_esrgan", side_effect=lambda model, device, image: image.resize((image.width * 2, image.height * 2)))
    def test_output_contract_upscaler_and_no_overwrite(self, inference):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            room = root / "RES01.RRM"
            room.write_bytes(room_fixture(True))
            output = root / "extracted" / "canimations"
            self.assertEqual(extractor.extract_scene(room, output), (1, 2, []))
            source = output / "scene_001" / "canim_001"
            metadata = json.loads((source / "metadata.json").read_text())
            self.assertEqual(metadata["resource_name"], "scene_001/canim_001")
            self.assertEqual([frame["file"] for frame in metadata["frames"]], ["frame_000.png", "frame_001.png"])
            with Image.open(source / "frame_000.png") as image:
                self.assertEqual(image.mode, "RGBA")
            enhanced = root / "enhanced" / "scene_001" / "canim_001"
            scaled = upscaler.upscale_resource_dir(source, enhanced, 2, None, "cpu", "fixture")
            self.assertEqual(scaled["frames"][0]["file"], "frame_000@2x.png")
            self.assertEqual((scaled["frames"][0]["offset_x"], scaled["frames"][0]["offset_y"]), (14, 18))
            original = (source / "frame_000.png").read_bytes()
            animations, frames, errors = extractor.extract_scene(room, output)
            self.assertEqual((animations, frames), (0, 0))
            self.assertIn("refusing to overwrite", errors[0])
            self.assertEqual((source / "frame_000.png").read_bytes(), original)

    def test_malformed_stream_leaves_no_animation_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            room = root / "RES01.RRM"
            room.write_bytes(room_fixture(True, malformed=True))
            animations, frames, errors = extractor.extract_scene(room, root / "out")
            self.assertEqual((animations, frames), (0, 0))
            self.assertEqual(len(errors), 1)
            self.assertFalse((root / "out" / "scene_001" / "canim_001").exists())
            self.assertEqual(list((root / "out" / "scene_001").iterdir()), [])

    def test_bad_offset_does_not_prevent_later_animations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            room = root / "RES01.RRM"
            data = bytearray(room_fixture(False))
            stream_offset = struct.unpack_from("<I", data, 48)[0]
            struct.pack_into("<I", data, 44, len(data) + 10)
            struct.pack_into("<I", data, stream_offset - 2 * extractor.CANIM_SIZE + 12, 1)
            room.write_bytes(data)
            animations, frames, errors = extractor.extract_scene(room, root / "out")
            self.assertEqual((animations, frames), (1, 2))
            self.assertIn("cAnim 0: data range", errors[0])
            self.assertTrue((root / "out" / "scene_001" / "canim_001" / "frame_000.png").exists())


if __name__ == "__main__":
    unittest.main()
