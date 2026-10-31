#!/usr/bin/env python3
"""Extract Rose Tattoo room cAnim streams as transparent PNG frame sequences.

The PC room loader reads 47-byte CAnim records after the embedded room images,
but startCAnim() obtains the actual data through the u32 offset table at byte
44 of the RRM. StreamingImageFile then reads one independently LZ-compressed
frame at a time (or a plain frame in an uncompressed room). These streams are
not ordinary ImageFile resources. See engines/sherlock/{scene,objects,
image_file}.cpp and tattoo/tattoo_scene.cpp in ScummVM.

Output is extracted/canimations/scene_NNN/canim_NNN/frame_NNN.png, where
both indices are zero-based, matching the runtime override lookup. Each frame
directory has metadata.json in the format consumed by the sprite upscaler.
Frames use the room's initial palette; scripted palette changes and screen
effects cannot be inferred from these resource streams alone. The stream
contains no timestamps: runtime controls playback timing. The current Rose
Tattoo startCAnim implementation advances through frames in stream order.
Existing animation directories are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PALETTE_SIZE = 256 * 3
OBJECT_SIZE = 591  # Object::load() serialized size; 625 is the compressed buffer allocation.
CANIM_SIZE = 47
STREAMING_BUFFER_SIZE = 65536


class FormatError(ValueError):
    """The room or one of its animation streams is malformed or truncated."""


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self, size: int) -> bytes:
        if size < 0 or self.pos < 0 or self.pos + size > len(self.data):
            raise FormatError(f"cannot read {size} bytes at {self.pos} of {len(self.data)}")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def seek(self, offset: int) -> None:
        if not 0 <= offset <= len(self.data):
            raise FormatError(f"offset {offset} outside stream of {len(self.data)} bytes")
        self.pos = offset

    def skip(self, size: int) -> None:
        self.seek(self.pos + size)

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))

    def u8(self) -> int:
        return self.unpack("<B")[0]

    def u16(self) -> int:
        return self.unpack("<H")[0]

    def u32(self) -> int:
        return self.unpack("<I")[0]


def decompress_lz(data: bytes, limit: int) -> bytes:
    """Decode one input-size-delimited Rose Tattoo LZ block.

    Resources::decompressLZ initializes the 0..0xFED dictionary to 0xff and
    starts writing at 0xFEE. The unwritten tail is zero here; valid streams
    should not refer to that uninitialized portion of ScummVM's stack buffer.
    Unlike the engine's fixed output buffers, return only the decoded bytes.
    """
    source = Reader(data)
    window = bytearray(b"\xff" * 0xFEE + b"\x00" * (4096 - 0xFEE))
    window_pos = 0xFEE
    command = 0
    out = bytearray()
    while source.pos < len(data):
        command >>= 1
        if not command & 0x100:
            command = source.u8() | 0xFF00
        if command & 1:
            values = [source.u8()]
        else:
            low, length = source.unpack("<BB")
            copy_pos = low | ((length & 0xF0) << 4)
            count = (length & 0x0F) + 3
            if len(out) + count > limit:
                raise FormatError(f"LZ output exceeds {limit} bytes")
            # Read from the window as it is written: overlapping copies are valid.
            for _ in range(count):
                value = window[copy_pos]
                copy_pos = (copy_pos + 1) & 0xFFF
                out.append(value)
                window[window_pos] = value
                window_pos = (window_pos + 1) & 0xFFF
            continue
        if len(out) + len(values) > limit:
            raise FormatError(f"LZ output exceeds {limit} bytes")
        for value in values:
            out.append(value)
            window[window_pos] = value
            window_pos = (window_pos + 1) & 0xFFF
    return bytes(out)


def compressed_block(reader: Reader, limit: int) -> bytes:
    size = reader.u32()
    return decompress_lz(reader.read(size), limit)


def skip_block(reader: Reader, size: int, compressed: bool) -> None:
    if compressed:
        reader.skip(reader.u32())
    else:
        reader.skip(size)


def parse_canim_record(data: bytes, index: int, data_offset: int) -> dict:
    reader = Reader(data)
    name = reader.read(12).split(b"\0", 1)[0].decode("cp437", errors="replace")
    size = reader.u32()
    x, y = reader.unpack("<hh")
    flags = reader.u8()
    scale = reader.unpack("<h")[0]
    points = []
    for _ in range(4):
        px, py, facing = reader.unpack("<hhh")
        points.append({"x": px, "y": py, "facing": facing})
    return {
        "index": index, "name": name, "data_offset": data_offset, "data_size": size,
        "position": {"x": x, "y": y}, "flags": flags, "scale_val": scale,
        "goto": points[:2], "teleport": points[2:], "runtime_enabled": x != -1,
    }


def parse_room(data: bytes) -> tuple[bool, list[int], list[dict]]:
    """Return the compression flag, initial room palette, and cAnim records."""
    reader = Reader(data)
    reader.seek(39)
    compressed = reader.u8() > 0
    header_offset = reader.u32()
    reader.seek(header_offset)
    num_structs, num_images, num_canims, desc_size, seq_size, scroll = reader.unpack("<6H")
    reader.skip(5)  # bytesWritten and fadeStyle
    palette = [v * 255 // 63 for v in reader.read(PALETTE_SIZE)]
    if max(palette) > 255:
        raise FormatError("room palette contains values above the 6-bit VGA range")
    skip_block(reader, (640 + scroll) * 480, compressed)
    image_sizes = []
    for _ in range(num_structs):
        image_sizes.append(reader.u32())
        reader.skip(10)  # maxFrames and 9-byte name
    if num_images > len(image_sizes):
        raise FormatError("more room images than shape header records")
    skip_block(reader, num_structs * OBJECT_SIZE, compressed)
    skip_block(reader, desc_size, compressed)
    skip_block(reader, seq_size, compressed)
    for size in image_sizes[:num_images]:
        skip_block(reader, size, compressed)
    if not num_canims:
        return compressed, palette, []
    table_size = num_canims * CANIM_SIZE
    table = compressed_block(reader, table_size) if compressed else reader.read(table_size)
    if len(table) != table_size:
        raise FormatError(f"cAnim table has {len(table)} bytes, expected {table_size}")
    offsets = Reader(data)
    offsets.seek(44)
    entries = []
    for index in range(num_canims):
        offset = offsets.u32()
        start = index * CANIM_SIZE
        entries.append(parse_canim_record(table[start:start + CANIM_SIZE], index, offset))
    return compressed, palette, entries


def decode_indices(payload: bytes, width: int, height: int, palette_base: int, rle: bool) -> bytes:
    """Apply ImageFrame::decompressFrame, including transparent index 255."""
    result = bytearray(b"\xff" * (width * height))
    if palette_base:
        expected = width * height // 2
        if len(payload) < expected:
            raise FormatError("truncated nibble-packed frame")
        for index, value in enumerate(payload[:expected]):
            result[index * 2] = value & 15
            result[index * 2 + 1] = value >> 4
    elif rle:
        source = Reader(payload)
        for y in range(height):
            x = 0
            while x < width:
                skip = source.u8()
                x += skip
                if x > width:
                    raise FormatError("RLE skip crosses a scanline")
                if x == width:
                    break
                run = source.u8()
                if x + run > width or (not skip and not run):
                    raise FormatError("invalid RLE run")
                result[y * width + x:y * width + x + run] = source.read(run)
                x += run
    else:
        count = width * height
        if len(payload) < count:
            raise FormatError("truncated raw frame")
        result[:] = payload[:count]
    return bytes(result)


def decode_frames(data: bytes, compressed: bool, palette: list[int], data_offset: int = 0):
    """Yield metadata and RGBA images in StreamingImageFile frame-number order."""
    source = Reader(data)
    index = 0
    while source.pos < len(data):
        frame_offset = source.pos
        if compressed:
            block_size = source.u32()
            frame_data = decompress_lz(source.read(block_size), STREAMING_BUFFER_SIZE)
            frame = Reader(frame_data)
        else:
            # The engine's uncompressed branch reads the payload but currently
            # decompresses its stale _buffer instead. Recover the intended frame
            # bytes here; the installed game's rooms are all compressed.
            block_size = None
            frame = source
        width, height = frame.u16() + 1, frame.u16() + 1
        palette_base, rle_byte, x, y = frame.unpack("<BBBB")
        encoded_size = frame.u16()
        marker = frame.u8()
        if encoded_size < 11:
            raise FormatError(f"frame {index}: encoded size {encoded_size} < 11")
        if width > 32768 or height > 32768 or width * height > 65536 * 16:
            raise FormatError(f"frame {index}: unreasonable dimensions {width}x{height}")
        payload = frame.read(encoded_size - 11)
        indices = decode_indices(payload, width, height, palette_base, rle_byte == 1)
        image = Image.frombytes("P", (width, height), indices)
        image.putpalette(palette)
        image.info["transparency"] = 255
        record = {
            "index": index, "file": f"frame_{index:03d}.png", "width": width,
            "height": height, "offset_x": x, "offset_y": y,
            "stream_offset": data_offset + frame_offset, "encoded_size": encoded_size,
            "compressed_size": block_size, "palette_base": palette_base,
            "rle_encoded": rle_byte == 1, "rle_marker": marker,
        }
        yield record, image.convert("RGBA")
        index += 1


def extract_scene(path: Path, output_root: Path) -> tuple[int, int, list[str]]:
    match = re.fullmatch(r"RES(\d+)\.RRM", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot infer scene number from {path}")
    scene_id = int(match.group(1))
    data = path.read_bytes()
    compressed, palette, entries = parse_room(data)
    scene_dir = output_root / f"scene_{scene_id:03d}"
    frames_written = 0
    animations_written = 0
    errors = []
    for entry in entries:
        if not entry["data_size"]:
            continue
        index = entry["index"]
        output_dir = scene_dir / f"canim_{index:03d}"
        try:
            offset = entry["data_offset"]
            if offset < 44 + 4 * len(entries) or offset + entry["data_size"] > len(data):
                raise FormatError(f"data range {offset}+{entry['data_size']} outside room")
            if output_dir.exists():
                raise FileExistsError(f"refusing to overwrite {output_dir}")
            # Stage one image at a time: long animations need not fit in RAM,
            # and malformed streams never get a destination or metadata.json.
            scene_dir.mkdir(parents=True, exist_ok=True)
            stream = data[offset:offset + entry["data_size"]]
            with tempfile.TemporaryDirectory(prefix=f".canim_{index:03d}_", dir=scene_dir) as temporary:
                staging = Path(temporary)
                records = []
                for record, image in decode_frames(stream, compressed, palette, offset):
                    image.save(staging / record["file"])
                    records.append(record)
                metadata = {
                    "resource_name": f"scene_{scene_id:03d}/canim_{index:03d}",
                    "source": path.name, "scene_id": scene_id, "canim_index": index,
                    "compressed": compressed, "palette_source": "initial room palette",
                    "animation": entry, "frame_count": len(records), "frames": records,
                }
                output_dir.mkdir(exist_ok=False)
                for record in records:
                    (staging / record["file"]).rename(output_dir / record["file"])
                (output_dir / "metadata.json").write_text(
                    json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            animations_written += 1
            frames_written += len(records)
        except (FormatError, OSError) as exc:
            errors.append(f"{path.name} cAnim {index}: {exc}")
    return animations_written, frames_written, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "scummvm")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "extracted" / "canimations")
    parser.add_argument("--scenes", type=int, nargs="+", help="Scene numbers; default: all RES*.RRM files")
    args = parser.parse_args()
    if args.scenes:
        paths = [args.data_dir / f"RES{scene:02d}.RRM" for scene in args.scenes]
    else:
        paths = sorted(path for path in args.data_dir.iterdir() if re.fullmatch(r"RES\d+\.RRM", path.name, re.IGNORECASE))
    if not paths:
        parser.error(f"no room resources found in {args.data_dir}")
    total_animations = total_frames = 0
    errors = []
    for path in paths:
        try:
            animation_count, frame_count, scene_errors = extract_scene(path, args.output_dir)
        except (FormatError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        total_animations += animation_count
        total_frames += frame_count
        errors.extend(scene_errors)
        print(f"{path.name}: {animation_count} animations, {frame_count} frames, {len(scene_errors)} errors")
    print(f"Total: {total_animations} animations, {total_frames} frames, {len(errors)} errors")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
