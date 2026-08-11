#!/usr/bin/env python3
"""Extract Rose Tattoo cursor/item/character sprite frames as PNGs.

Companion to extract_rosetattoo_assets.py (which only pulls room backgrounds).
This follows the same VGS.LIB / .VGS frame format ScummVM's Sherlock engine
uses for everything that *isn't* a room background: mouse cursors
(rmouse.vgs/omouse.vgs), on-screen items (hand icons, dartboard, journal
graphics, ...), and character walk-cycle sprites (svgawalk.vgs, watson.vgs,
NPC-specific walk files, ...). See:

- engines/sherlock/resources.cpp (Resources::loadLibraryIndex,
  Resources::decompressIfNecessary, Resources::decompressLZ) for the
  LIB/LIC archive format and its LZV-tagged LZ77 compression.
- engines/sherlock/image_file.cpp (ImageFile::load, ImageFile::loadPalette,
  ImageFrame::decompressFrame) for the per-file VGA palette and per-frame
  pixel encodings (nibble-packed, Rose-Tattoo-style RLE, or raw).

Every sprite/cursor/character file in this game is one of these VGS-format
resources, stored either directly on disk (rare) or packed inside one of the
game's *.LIB archives (the common case - e.g. RMOUSE.VGS lives inside
VGS.LIB, and every NPC walk cycle lives inside WALK.LIB).
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

PALETTE_SIZE = 256 * 3
# See graphics/palette.h: PALETTE_6BIT_TO_8BIT(x) = x * 255 / 63. The game's
# on-disk VGA palettes are 6-bit-per-channel (0-63), matching real VGA DAC
# registers; this rescales them to the 8-bit-per-channel range PNGs expect.
LIB_SIGNATURES = {b"LIB\x1a": False, b"LIC\x1a": True}
LZV_SIGNATURE = b"LZV\x1a"
TRANSPARENT_INDEX = 0xFF


def palette_6bit_to_8bit(value: int) -> int:
    return value * 255 // 63


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def seek(self, pos: int) -> None:
        self.pos = pos

    def skip(self, count: int) -> None:
        self.pos += count

    def read(self, count: int) -> bytes:
        out = self.data[self.pos : self.pos + count]
        if len(out) != count:
            raise EOFError(f"wanted {count} bytes at {self.pos}, got {len(out)}")
        self.pos += count
        return out

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def s32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32be(self) -> int:
        value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return value


def decompress_lz(reader: Reader, out_size: int, has_input_size: bool) -> bytes:
    """LZ77-family decompressor matching Resources::decompressLZ().

    Shared (duplicated rather than imported, to keep this tool runnable
    standalone) with extract_rosetattoo_assets.py's decompress_lz(). The
    12-bit sliding window is pre-filled with 0xFF, matching the engine's
    memset(lzWindow, 0xFF, 0xFEE) before decoding starts.
    """
    in_size = reader.s32() if has_input_size else -1
    end_pos = reader.pos + in_size if in_size != -1 else None
    window = bytearray([0xFF] * 4096)
    window_pos = 0xFEE
    cmd = 0
    out = bytearray()

    while len(out) < out_size and (end_pos is None or reader.pos < end_pos):
        cmd >>= 1
        if not (cmd & 0x100):
            cmd = reader.u8() | 0xFF00

        if cmd & 1:
            literal = reader.u8()
            out.append(literal)
            window[window_pos] = literal
            window_pos = (window_pos + 1) & 0x0FFF
        else:
            copy_pos = reader.u8()
            copy_len = reader.u8()
            copy_pos |= (copy_len & 0xF0) << 4
            copy_len = (copy_len & 0x0F) + 3

            for _ in range(copy_len):
                if len(out) >= out_size:
                    break
                literal = window[copy_pos]
                copy_pos = (copy_pos + 1) & 0x0FFF
                out.append(literal)
                window[window_pos] = literal
                window_pos = (window_pos + 1) & 0x0FFF

    if end_pos is not None and reader.pos < end_pos:
        reader.seek(end_pos)
    if len(out) < out_size:
        out.extend(b"\x00" * (out_size - len(out)))
    return bytes(out[:out_size])


def decompress_if_necessary(data: bytes) -> bytes:
    """Matches Resources::decompressIfNecessary(): LZV-tagged data is a
    4-byte 'LZV\\x1a' signature + a u32le output size, then LZ-compressed
    bytes with no separate stored input size (decoding just runs until
    out_size bytes have been produced)."""
    if data[:4] != LZV_SIGNATURE:
        return data
    reader = Reader(data)
    reader.skip(4)
    out_size = reader.u32()
    return decompress_lz(reader, out_size, has_input_size=False)


@dataclass
class LibraryEntry:
    index: int
    offset: int
    size: int


def load_library_index(data: bytes) -> dict[str, LibraryEntry]:
    """Parses a LIB/LIC archive's resource name table.

    See Resources::loadLibraryIndex()'s non-3DO branch: a 4-byte signature,
    a u16le resource count, an optional (count+1)*8-byte "new style" (LIC)
    padding block, then `count` fixed 17-byte records (13-byte
    null-terminated name + u32le offset). Each entry's size is inferred from
    the following entry's offset (or EOF for the last entry).
    """
    signature = data[:4]
    if signature not in LIB_SIGNATURES:
        raise ValueError(f"Not a Sherlock LIB/LIC archive (got {signature!r})")
    is_new_style = LIB_SIGNATURES[signature]

    reader = Reader(data)
    reader.seek(4)
    count = reader.u16()
    if is_new_style:
        reader.skip((count + 1) * 8)

    names: list[str] = []
    offsets: list[int] = []
    for _ in range(count):
        raw_name = reader.read(13)
        name = raw_name.split(b"\x00", 1)[0].decode("latin1").upper()
        offset = reader.u32()
        names.append(name)
        offsets.append(offset)

    index: dict[str, LibraryEntry] = {}
    for idx, (name, offset) in enumerate(zip(names, offsets)):
        next_offset = offsets[idx + 1] if idx + 1 < count else len(data)
        index[name] = LibraryEntry(idx, offset, next_offset - offset)
    return index


def load_resource(library_path: Path, resource_name: str) -> bytes:
    """Loads and decompresses one named resource from a LIB/LIC archive."""
    data = library_path.read_bytes()
    index = load_library_index(data)
    key = resource_name.upper()
    if key not in index:
        raise KeyError(f"{resource_name} not found in {library_path.name}")
    entry = index[key]
    raw = data[entry.offset : entry.offset + entry.size]
    return decompress_if_necessary(raw)


@dataclass
class SpriteFrame:
    width: int
    height: int
    offset_x: int
    offset_y: int
    image: Image.Image


def decompress_frame(
    src: bytes,
    width: int,
    height: int,
    palette_base: int,
    rle_encoded: bool,
) -> bytes:
    """Matches ImageFrame::decompressFrame() with isRoseTattoo always true.

    Returns a flat width*height array of palette index bytes (0xFF = the
    engine's transparent/background sentinel, matching
    CursorMan.replaceCursor(..., 0xff) and Common::fill(dest, ..., 0xff)
    before decoding starts).
    """
    dest = bytearray([TRANSPARENT_INDEX]) * (width * height)
    pos = 0

    if palette_base:
        # Nibble-packed: each source byte holds two 4-bit palette indices.
        out_idx = 0
        for byte in src:
            if out_idx >= len(dest):
                break
            dest[out_idx] = byte & 0xF
            out_idx += 1
            if out_idx >= len(dest):
                break
            dest[out_idx] = byte >> 4
            out_idx += 1
    elif rle_encoded:
        # Rose Tattoo's RLE has no marker byte: alternating (skip, run) pairs
        # per scanline, where `skip` pixels are left untouched (transparent)
        # and the following `run` pixels are copied literally from src.
        for y in range(height):
            x = 0
            while x < width:
                skip = src[pos]
                pos += 1
                x += skip
                if x >= width:
                    break
                run = src[pos]
                pos += 1
                row_start = y * width + x
                dest[row_start : row_start + run] = src[pos : pos + run]
                pos += run
                x += run
    else:
        # Uncompressed frame.
        dest[: width * height] = src[: width * height]

    return bytes(dest)


def load_vgs_image(
    data: bytes, default_palette: list[int] | None = None
) -> tuple[list[int], list[SpriteFrame]]:
    """Parses one VGS-format resource: an optional VGA palette followed by a
    sequence of frames, matching ImageFile::load()/loadPalette().

    Most character/item sprite files (unlike room backgrounds) have no
    embedded palette at all - at runtime they're drawn using whichever
    palette the currently active room already loaded. `default_palette`
    (a flat 768-entry 8-bit RGB list, e.g. borrowed from a room export via
    --palette-scene) is used whenever this resource has no palette of its
    own; without it such sprites decode to solid black.

    Returns (palette as a flat 768-entry 8-bit RGB list, decoded frames).
    """
    reader = Reader(data)
    palette = list(default_palette) if default_palette is not None else [0] * PALETTE_SIZE

    # loadPalette(): the very first "frame" header is checked for the
    # 390x2 "VGA palette" sentinel before falling back to normal frame
    # parsing.
    width = reader.u16() + 1
    height = reader.u16() + 1
    palette_base = reader.u8()
    rle_encoded = reader.u8()
    offset_x = reader.u8()
    offset_y = reader.u8()
    if width == 390 and height == 2 and not palette_base and not rle_encoded and not offset_x and not offset_y:
        signature = reader.u32be()
        if signature == struct.unpack(">I", b"VGA ")[0]:
            reader.skip(8)  # rest of "VGA palette" signature text
            for idx in range(PALETTE_SIZE):
                palette[idx] = palette_6bit_to_8bit(reader.u8())
        else:
            reader.seek(reader.pos - 12)
    else:
        reader.seek(reader.pos - 8)

    frames: list[SpriteFrame] = []
    size = len(data)
    while reader.pos < size:
        width = reader.u16() + 1
        height = reader.u16() + 1
        palette_base = reader.u8()
        rle_encoded = reader.u8() == 1
        offset_x = reader.u8()
        offset_y = reader.u8()

        if width > 32768 or height > 32768:
            # Matches the engine's handling of the intro's -320x-200 dummy
            # frame: signed-width sentinel entries with no real pixel data.
            break

        if palette_base:
            frame_size = (width * height) // 2
        elif rle_encoded:
            frame_size = reader.u16() - 11
            reader.skip(1)  # RLE marker byte (unused by Rose Tattoo's RLE)
        else:
            frame_size = width * height

        frame_data = reader.read(frame_size)
        indices = decompress_frame(frame_data, width, height, palette_base, rle_encoded)

        image = Image.new("RGBA", (width, height))
        pixels = image.load()
        for y in range(height):
            row_base = y * width
            for x in range(width):
                index = indices[row_base + x]
                if index == TRANSPARENT_INDEX:
                    pixels[x, y] = (0, 0, 0, 0)
                else:
                    r = palette[index * 3]
                    g = palette[index * 3 + 1]
                    b = palette[index * 3 + 2]
                    pixels[x, y] = (r, g, b, 255)

        frames.append(SpriteFrame(width, height, offset_x, offset_y, image))

    return palette, frames


def find_resource_library(root: Path, resource_name: str, candidate_libs: list[str]) -> Path | None:
    for lib_name in candidate_libs:
        lib_path = root / lib_name
        if not lib_path.exists():
            continue
        try:
            index = load_library_index(lib_path.read_bytes())
        except ValueError:
            continue
        if resource_name.upper() in index:
            return lib_path
    return None


DEFAULT_LIBRARIES = ["VGS.LIB", "WALK.LIB", "TALK.LIB"]


def load_scene_palette(root: Path, scene_id: int) -> list[int]:
    """Borrows a room's palette (as extracted by extract_rosetattoo_assets.py's
    parse of RESxx.RRM) for rendering palette-less character/item sprites.
    Mirrors the palette read in extract_scene() there: header parsed at the
    RRM's stored header offset, immediately followed by 768 raw 6-bit
    VGA palette bytes.
    """
    rrm_path = root / f"RES{scene_id:02d}.RRM"
    if not rrm_path.exists():
        raise FileNotFoundError(f"{rrm_path} not found (needed for --palette-scene)")
    reader = Reader(rrm_path.read_bytes())
    reader.seek(39)
    reader.u8()  # compressed flag, unused here
    header_offset = reader.u32()
    reader.seek(header_offset)
    # Header layout (see extract_rosetattoo_assets.py's parse_header): a
    # fixed 15-byte prelude before the scroll_size field we don't need here,
    # so just skip straight to the palette which follows the full header.
    # Reuse the sibling extractor's own header parser to stay in sync with
    # any future header-format changes rather than duplicating its layout.
    import importlib

    background_tool = importlib.import_module("extract_rosetattoo_assets")
    header = background_tool.parse_header(reader)
    del header  # only needed to advance reader.pos past the header
    raw_palette = reader.read(PALETTE_SIZE)
    return [palette_6bit_to_8bit(value) for value in raw_palette]


def extract_resource(
    root: Path, resource_name: str, output_dir: Path, palette_scene: int | None = None
) -> dict:
    physical_path = root / resource_name
    if physical_path.exists():
        data = decompress_if_necessary(physical_path.read_bytes())
        source = str(physical_path.relative_to(root))
    else:
        lib_path = find_resource_library(root, resource_name, DEFAULT_LIBRARIES)
        if lib_path is None:
            raise FileNotFoundError(
                f"{resource_name} not found on disk or in {DEFAULT_LIBRARIES} under {root}"
            )
        data = load_resource(lib_path, resource_name)
        source = f"{lib_path.name}:{resource_name}"

    default_palette = load_scene_palette(root, palette_scene) if palette_scene else None
    palette, frames = load_vgs_image(data, default_palette=default_palette)

    output_dir.mkdir(parents=True, exist_ok=True)
    frame_records = []
    for idx, frame in enumerate(frames):
        frame_path = output_dir / f"frame_{idx:03d}.png"
        frame.image.save(frame_path)
        frame_records.append(
            {
                "index": idx,
                "file": frame_path.name,
                "width": frame.width,
                "height": frame.height,
                "offset_x": frame.offset_x,
                "offset_y": frame.offset_y,
            }
        )

    metadata = {
        "resource_name": resource_name,
        "source": source,
        "frame_count": len(frames),
        "frames": frame_records,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Rose Tattoo cursor/item/character sprite frames "
            "(VGS-format resources) as PNGs, from either physical .VGS "
            "files or entries packed inside VGS.LIB/WALK.LIB/TALK.LIB."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("scummvm"),
        help="Directory containing the extracted Rose Tattoo game data files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("extracted") / "sprites",
        help="Directory to write extracted frame PNGs and metadata into.",
    )
    parser.add_argument(
        "--resources",
        nargs="+",
        default=["RMOUSE.VGS", "OMOUSE.VGS"],
        help=(
            "Resource names to extract (e.g. RMOUSE.VGS for the room cursor "
            "set, OMOUSE.VGS for the overhead-map cursor set, or a "
            "character walk file such as WATSON.VGS/SVGAWALK.VGS)."
        ),
    )
    parser.add_argument(
        "--palette-scene",
        type=int,
        default=None,
        help=(
            "Scene number (e.g. 1 for RES01.RRM) whose room palette to use "
            "for sprites that have no palette of their own (most character "
            "and item sprites - cursors are the exception, whose palette "
            "index 0 is black in every room). Omit to render with an "
            "all-black fallback palette."
        ),
    )
    args = parser.parse_args()

    for resource_name in args.resources:
        output_dir = args.output_dir / resource_name.replace(".", "_").lower()
        metadata = extract_resource(
            args.data_dir, resource_name, output_dir, palette_scene=args.palette_scene
        )
        print(
            f"{resource_name}: {metadata['frame_count']} frames "
            f"from {metadata['source']} -> {output_dir}"
        )


if __name__ == "__main__":
    main()
