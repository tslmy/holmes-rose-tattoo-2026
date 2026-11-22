#!/usr/bin/env python3
"""Extract the ImageFile chunks embedded in PC Rose Tattoo room resources.

See Scene::loadScene() and ImageFile::load()/decompressFrame() in ScummVM's
Sherlock engine. Room image slots are one-based; ImageFile frames are not.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Iterator

from PIL import Image

from extract_rosetattoo_assets import (
    OBJECT_RECORD_SIZE,
    PALETTE_SIZE,
    ROSE_OBJECT_SERIALIZED_SIZE,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    Reader,
    c_string,
    parse_header,
    scene_paths,
)
from extract_rosetattoo_sprites import palette_6bit_to_8bit


TRANSPARENT_INDEX = 0xFF


def read_block(reader: Reader, size: int, compressed: bool, *, decode: bool) -> bytes:
    """Consume one RRM section; compressed sections have a signed size prefix.

    Unneeded sections can be skipped without expanding the background. Image
    blocks are decoded within their own bounded input, never into the next
    chunk. Reject truncated output instead of silently publishing padded art.
    """
    if not compressed:
        data = reader.read(size)
        return data if decode else b""

    input_size = reader.s32()
    if input_size < 0:
        raise ValueError(f"invalid compressed block size {input_size}")
    data = reader.read(input_size)
    if not decode:
        return b""

    source = Reader(data)
    window = bytearray([0xFF] * 4096)
    window_pos = 0xFEE
    command = 0
    result = bytearray()
    while len(result) < size:
        command >>= 1
        if not command & 0x100:
            command = source.u8() | 0xFF00
        if command & 1:
            value = source.u8()
            result.append(value)
            window[window_pos] = value
            window_pos = (window_pos + 1) & 0xFFF
        else:
            copy_pos = source.u8()
            copy_len = source.u8()
            copy_pos |= (copy_len & 0xF0) << 4
            copy_len = (copy_len & 0x0F) + 3
            for _ in range(min(copy_len, size - len(result))):
                value = window[copy_pos]
                copy_pos = (copy_pos + 1) & 0xFFF
                result.append(value)
                window[window_pos] = value
                window_pos = (window_pos + 1) & 0xFFF
    return bytes(result)


def decode_frame(data: bytes, width: int, height: int, encoding: str) -> bytes:
    """Decode native palette indices, leaving skipped pixels as index 255."""
    pixel_count = width * height
    if encoding == "raw":
        if len(data) != pixel_count:
            raise ValueError("incorrect raw frame size")
        return data

    indices = bytearray([TRANSPARENT_INDEX]) * pixel_count
    if encoding == "nibble":
        if len(data) != pixel_count // 2:
            raise ValueError("incorrect nibble frame size")
        for index, value in enumerate(data):
            indices[index * 2] = value & 0xF
            indices[index * 2 + 1] = value >> 4
        return bytes(indices)

    if encoding != "rle":
        raise ValueError(f"unknown frame encoding {encoding!r}")
    reader = Reader(data)
    for y in range(height):
        x = 0
        while x < width:
            skip = reader.u8()
            x += skip
            if x > width:
                raise ValueError(f"RLE skip exceeds row {y}")
            if x == width:
                break
            run = reader.u8()
            if not skip and not run:
                raise ValueError(f"RLE makes no progress in row {y}")
            if x + run > width:
                raise ValueError(f"RLE run exceeds row {y}")
            start = y * width + x
            indices[start : start + run] = reader.read(run)
            x += run
    return bytes(indices)


def rgba_from_indices(indices: bytes, width: int, height: int, palette: list[int]) -> Image.Image:
    image = Image.frombytes("P", (width, height), indices)
    # Pixel 255 is the engine's transparent key, independent of palette RGB.
    png_palette = palette.copy()
    png_palette[255 * 3 : 256 * 3] = [0, 0, 0]
    image.putpalette(png_palette)
    image.info["transparency"] = TRANSPARENT_INDEX
    return image.convert("RGBA")


def iter_frames(data: bytes, palette: list[int]) -> Iterator[tuple[dict, Image.Image]]:
    """Read an ImageFile stream without relying on bgInfo's max-frame hint."""
    reader = Reader(data)
    # ImageFile::loadPalette() only treats the first header as a palette, and
    # only if its special dimensions and the VGA signature both match. Room
    # drawing still uses the active scene palette, not ImageFile::_palette.
    if len(data) >= 12 and data[:8] == struct.pack("<HHBBBB", 389, 1, 0, 0, 0, 0) and data[8:12] == b"VGA ":
        reader.read(8 + 12 + PALETTE_SIZE)

    index = 0
    while reader.pos < len(data):
        frame_offset = reader.pos
        width = (reader.u16() + 1) & 0xFFFF
        height = (reader.u16() + 1) & 0xFFFF
        palette_base = reader.u8()
        rle_flag = reader.u8()
        offset_x = reader.u8()
        offset_y = reader.u8()
        invalid = width > 32768 or height > 32768
        if width > 32768:
            width = 65536 - width
        if height > 32768:
            height = 65536 - height
        if not width or not height:
            raise ValueError(f"zero-sized frame {index}")

        marker = None
        if palette_base:
            encoding = "nibble"
            frame_size = width * height // 2
        elif rle_flag == 1:
            encoding = "rle"
            frame_size = reader.u16() - 11
            marker = reader.u8()  # Rose Tattoo RLE does not use this byte.
            if frame_size < 0:
                raise ValueError(f"invalid RLE size in frame {index}")
        else:
            encoding = "raw"
            frame_size = width * height
        frame_data = reader.read(frame_size)
        indices = (
            bytes([TRANSPARENT_INDEX]) * (width * height)
            if invalid
            else decode_frame(frame_data, width, height, encoding)
        )
        record = {
            "index": index,
            "file": f"frame_{index:03d}.png",
            "width": width,
            "height": height,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "encoding": encoding,
            "palette_base": palette_base,
            "rle_marker": marker,
            "encoded_size": frame_size,
            "offset_in_chunk": frame_offset,
            "invalid_placeholder": invalid,
        }
        yield record, rgba_from_indices(indices, width, height, palette)
        index += 1


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_scene(path: Path, output_root: Path) -> dict:
    match = re.fullmatch(r"RES(\d+)\.RRM", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot infer scene number from {path}")
    scene_id = int(match.group(1))
    reader = Reader(path.read_bytes())
    reader.seek(39)
    compressed = reader.u8() > 0
    reader.seek(reader.u32())
    header = parse_header(reader)
    palette = [palette_6bit_to_8bit(value) for value in reader.read(PALETTE_SIZE)]
    read_block(reader, (SCREEN_WIDTH + header.scroll_size) * SCREEN_HEIGHT, compressed, decode=False)

    bg_info = [
        {"filesize": reader.u32(), "max_frames": reader.u8(), "filename": c_string(reader.read(9))}
        for _ in range(header.num_structs)
    ]
    if header.num_images > len(bg_info):
        raise ValueError(f"{path}: more image chunks than shape-header entries")

    # Uncompressed objects are read one at a time (591 serialized bytes);
    # the 625-byte allocation size applies only to the compressed buffer.
    shape_size = header.num_structs * (OBJECT_RECORD_SIZE if compressed else ROSE_OBJECT_SERIALIZED_SIZE)
    read_block(reader, shape_size, compressed, decode=False)
    read_block(reader, header.desc_size, compressed, decode=False)
    read_block(reader, header.seq_size, compressed, decode=False)

    scene_dir = output_root / f"scene_{scene_id:03d}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    rooms = []
    for chunk_index in range(header.num_images):
        info = bg_info[chunk_index]
        room_index = chunk_index + 1
        room_name = f"room_{room_index:03d}"
        chunk_offset = reader.pos
        try:
            chunk = read_block(reader, info["filesize"], compressed, decode=True)
            stored_size = reader.pos - chunk_offset
            room_dir = scene_dir / room_name
            room_dir.mkdir()
            frames = []
            for record, image in iter_frames(chunk, palette):
                image.save(room_dir / record["file"])
                frames.append(record)
        except (EOFError, struct.error, ValueError, IndexError) as exc:
            raise ValueError(f"{path}, {room_name}: {exc}") from exc

        metadata = {
            "scene_id": scene_id,
            "room_index": room_index,
            "chunk_index": chunk_index,
            "source": str(path),
            "source_filename": info["filename"],
            "compressed": compressed,
            "chunk_offset": chunk_offset,
            "stored_size": stored_size,
            "uncompressed_size": info["filesize"],
            "max_frames": info["max_frames"],
            "frame_count": len(frames),
            "frames": frames,
        }
        write_json(room_dir / "metadata.json", metadata)
        rooms.append({"room_index": room_index, "directory": room_name, "frame_count": len(frames)})

    scene = {
        "scene_id": scene_id,
        "source": str(path),
        "compressed": compressed,
        "room_count": len(rooms),
        "frame_count": sum(room["frame_count"] for room in rooms),
        "rooms": rooms,
    }
    write_json(scene_dir / "metadata.json", scene)
    return scene


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--output-dir", type=Path, default=Path("extracted/room-sprites"))
    parser.add_argument("--scenes", type=int, nargs="+", help="Scene numbers; default: all RES*.RRM files")
    args = parser.parse_args()

    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        parser.error(f"output directory must be new or empty: {args.output_dir}")
    paths = scene_paths(args.data_dir, args.scenes)
    if not paths:
        parser.error(f"no RES*.RRM files found in {args.data_dir}")
    if len(set(paths)) != len(paths):
        parser.error("scene numbers must not be repeated")
    for path in paths:
        if not path.is_file():
            parser.error(f"room resource not found: {path}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenes = []
    for path in paths:
        scene = extract_scene(path, args.output_dir)
        scenes.append(scene)
        print(f"scene {scene['scene_id']:03d}: {scene['room_count']} chunks, {scene['frame_count']} frames", flush=True)
    manifest = {
        "data_dir": str(args.data_dir),
        "scene_count": len(scenes),
        "room_count": sum(scene["room_count"] for scene in scenes),
        "frame_count": sum(scene["frame_count"] for scene in scenes),
        "scenes": scenes,
    }
    write_json(args.output_dir / "manifest.json", manifest)
    print(f"total: {manifest['scene_count']} scenes, {manifest['room_count']} chunks, {manifest['frame_count']} frames")


if __name__ == "__main__":
    main()
