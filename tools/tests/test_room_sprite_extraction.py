"""Synthetic PC RRM fixtures; no copyrighted game data is needed."""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_rosetattoo_room_sprites as room_sprites
from extract_rosetattoo_assets import Reader


def literal_lz(data):
    result = bytearray()
    for pos in range(0, len(data), 8):
        group = data[pos : pos + 8]
        result.append((1 << len(group)) - 1)
        result.extend(group)
    return struct.pack("<i", len(result)) + result


def frame(width, height, payload, *, encoding="raw", x=0, y=0, base=0):
    flags = 1 if encoding == "rle" else 0
    header = struct.pack("<HHBBBB", width - 1, height - 1, base, flags, x, y)
    if encoding == "rle":
        header += struct.pack("<HB", len(payload) + 11, 99)
    return header + payload


def room_fixture(compressed):
    raw = frame(2, 2, bytes([1, 255, 2, 3]), x=6, y=7)
    rle = frame(3, 2, bytes([1, 2, 1, 2, 3]), encoding="rle", x=8, y=9)
    nibble = frame(3, 1, bytes([0x21]), encoding="nibble", base=4, x=10)
    chunks = [raw + rle + nibble, frame(1, 1, bytes([3]), x=5)]
    palette = bytearray(768)
    palette[3:6] = bytes([1, 2, 3])
    palette[6:9] = bytes([16, 32, 48])
    palette[9:12] = bytes([63, 0, 0])
    header = struct.pack("<HHHHHHIB", 2, 2, 0, 3, 2, 0, 0, 0)
    info = b"".join(
        struct.pack("<IB9s", len(chunk), 1, name)
        for chunk, name in zip(chunks, [b"FIRST", b"SECOND"])
    )
    block = literal_lz if compressed else lambda data: data
    shape_size = 625 if compressed else 591
    return (
        b"\x00" * 39 + bytes([int(compressed)]) + struct.pack("<I", 44)
        + header + bytes(palette) + block(bytes(640 * 480)) + info
        + block(bytes(2 * shape_size)) + block(b"abc") + block(b"de")
        + b"".join(block(chunk) for chunk in chunks)
    )


class RoomSpriteExtractionTests(unittest.TestCase):
    def test_compressed_and_uncompressed_rooms_with_all_frame_encodings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for compressed in (False, True):
                with self.subTest(compressed=compressed):
                    source = root / ("RES26.RRM" if compressed else "RES27.RRM")
                    source.write_bytes(room_fixture(compressed))
                    output = root / ("compressed" if compressed else "uncompressed")
                    scene = room_sprites.extract_scene(source, output)
                    scene_dir = output / f"scene_{scene['scene_id']:03d}"
                    room_dir = scene_dir / "room_001"
                    metadata = json.loads((room_dir / "metadata.json").read_text())
                    self.assertEqual((scene["room_count"], scene["frame_count"]), (2, 4))
                    self.assertEqual(metadata["room_index"], 1)
                    self.assertEqual(metadata["chunk_index"], 0)
                    self.assertEqual(metadata["source_filename"], "FIRST")
                    self.assertEqual(metadata["compressed"], compressed)
                    self.assertEqual(metadata["max_frames"], 1)  # hint is not a frame limit
                    self.assertEqual(metadata["frame_count"], 3)
                    source_bytes = source.read_bytes()
                    chunk_offset = metadata["chunk_offset"]
                    if compressed:
                        self.assertEqual(struct.unpack_from("<i", source_bytes, chunk_offset)[0] + 4, metadata["stored_size"])
                    else:
                        self.assertEqual(metadata["stored_size"], metadata["uncompressed_size"])
                        self.assertEqual(source_bytes[chunk_offset : chunk_offset + 4], b"\x01\x00\x01\x00")
                    self.assertEqual([f["encoding"] for f in metadata["frames"]], ["raw", "rle", "nibble"])
                    self.assertEqual((metadata["frames"][0]["offset_x"], metadata["frames"][0]["offset_y"]), (6, 7))
                    self.assertEqual(metadata["frames"][1]["rle_marker"], 99)
                    self.assertEqual(metadata["frames"][1]["offset_in_chunk"], 12)
                    with Image.open(room_dir / "frame_000.png") as image:
                        self.assertEqual(image.mode, "RGBA")
                        self.assertEqual(image.tobytes(), bytes([4, 8, 12, 255, 0, 0, 0, 0, 64, 129, 194, 255, 255, 0, 0, 255]))
                    with Image.open(room_dir / "frame_001.png") as image:
                        self.assertEqual(image.tobytes(), bytes([0, 0, 0, 0, 4, 8, 12, 255, 64, 129, 194, 255] + [0] * 12))
                    with Image.open(room_dir / "frame_002.png") as image:
                        self.assertEqual(image.tobytes(), bytes([4, 8, 12, 255, 64, 129, 194, 255, 0, 0, 0, 0]))
                    second = json.loads((scene_dir / "room_002/metadata.json").read_text())
                    self.assertEqual((second["room_index"], second["chunk_index"], second["frame_count"]), (2, 1, 1))

    def test_lz_backreference_and_truncation(self):
        block = bytes([1, 4, 0xEE, 0xF0])
        reader = Reader(struct.pack("<i", len(block)) + block + b"next")
        self.assertEqual(room_sprites.read_block(reader, 4, True, decode=True), bytes([4] * 4))
        self.assertEqual(reader.read(4), b"next")
        with self.assertRaises(EOFError):
            room_sprites.read_block(Reader(struct.pack("<i", 2) + b"\x01\x04"), 4, True, decode=True)

    def test_embedded_palette_is_skipped_but_scene_palette_is_used(self):
        palette = [0] * 768
        palette[3:6] = [12, 34, 56]
        embedded = struct.pack("<HHBBBB", 389, 1, 0, 0, 0, 0) + b"VGA palette\x00" + bytes([63] * 768)
        frames = list(room_sprites.iter_frames(embedded + frame(1, 1, b"\x01"), palette))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0]["offset_in_chunk"], len(embedded))
        self.assertEqual(frames[0][1].getpixel((0, 0)), (12, 34, 56, 255))

    def test_palette_sentinel_requires_signature(self):
        frames = list(room_sprites.iter_frames(frame(390, 2, bytes(780)), [0] * 768))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1].size, (390, 2))

    def test_invalid_frame_placeholder_keeps_frame_index(self):
        placeholder = struct.pack("<HHBBBB", 65533, 65534, 0, 0, 3, 4) + b"\x01\x02"
        frames = list(room_sprites.iter_frames(placeholder + frame(1, 1, b"\x01"), [0] * 768))
        self.assertEqual([f[0]["index"] for f in frames], [0, 1])
        self.assertTrue(frames[0][0]["invalid_placeholder"])
        self.assertEqual(frames[0][1].size, (2, 1))
        self.assertEqual(frames[0][1].getpixel((0, 0))[3], 0)

    def test_rle_invalid_rows_are_rejected(self):
        for data in (b"\x04", b"\x00\x04abcd", b"\x00\x00", b"\x00\x03ab"):
            with self.subTest(data=data), self.assertRaises((ValueError, EOFError)):
                room_sprites.decode_frame(data, 3, 1, "rle")

    def test_cli_writes_manifest_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "RES26.RRM").write_bytes(room_fixture(True))
            output = root / "output"
            command = [sys.executable, str(Path(room_sprites.__file__)), "--data-dir", str(root), "--output-dir", str(output), "--scenes", "26"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual((manifest["scene_count"], manifest["room_count"], manifest["frame_count"]), (1, 2, 4))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be new or empty", result.stderr)
            self.assertTrue((output / "scene_026/room_001/frame_000.png").exists())


if __name__ == "__main__":
    unittest.main()
