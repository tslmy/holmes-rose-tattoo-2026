#!/usr/bin/env python3
"""Extract palette-qualified PC Rose Tattoo walk sprites into a NEW directory.

This command is intentionally independent of the older sprite extractors.
See docs/palette-walk-extraction.md for the format and scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


MAGIC = b"RT-PAL1\0"
TRANSPARENT = 255
MODE = {"skipPal": False, "animImages": False}
ARCHIVES = ("VGS.LIB", "WALK.LIB")
AMBIGUOUS = frozenset(("HTUPRIGH.VGS", "JTDOWNRG.VGS", "JTRIGHT.VGS", "JTUPRIGH.VGS", "NOHAT.VGS"))
# Directional walk-looking files not named by the installed Tattoo SEQs.
EXTRAS = ("3TDOWNRG.VGS", "3TRIGHT.VGS", "3TUPRIGH.VGS",
          "GTSDOWNR.VGS", "GTSRIGHT.VGS", "GTSUPRIG.VGS")
# This is a legacy fixed-layout sequence, not WalkSequence::load's format.
EXCLUDED_SEQUENCES = {"TINYWALK.SEQ": "legacy non-Tattoo sequence layout"}
LIMITATIONS = [
    "Projections use initial RRM palettes, not dynamic palette changes, fades, or other runtime effects.",
    "All selected room palettes are paired conservatively with every selected resource; scene lists do not imply actual character presence or reachability.",
    "Only PC walk ImageFile decoding with skipPal=false and animImages=false is represented; no other decode modes or non-walk assets are covered.",
    "The source images are native-size indexed sprites rendered as RGBA; no upscaling or in-game validation is performed.",
]
NAME = re.compile(r"[A-Z0-9_]{1,8}\.VGS\Z")


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError(f"truncated data at {self.pos}, need {n} bytes")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]


def decompress(data: bytes) -> bytes:
    """Decode an LZV resource stream, rejecting truncated input."""
    if not data.startswith(b"LZV\x1a"):
        return data
    r = Reader(data)
    r.read(4)
    size = r.u32()
    # Corrupt files should not allocate an unbounded output.
    if size > 256 * 1024 * 1024:
        raise ValueError(f"unreasonable LZV size: {size}")
    window = bytearray([255]) * 4096
    window_pos, command = 0xFEE, 0
    out = bytearray()
    while len(out) < size:
        command >>= 1
        if not command & 0x100:
            command = r.u8() | 0xFF00
        if command & 1:
            value = r.u8()
            out.append(value)
            window[window_pos] = value
            window_pos = (window_pos + 1) & 0xFFF
        else:
            pos, length = r.u8(), r.u8()
            pos |= (length & 0xF0) << 4
            for _ in range(min((length & 15) + 3, size - len(out))):
                value = window[pos]
                pos = (pos + 1) & 0xFFF
                out.append(value)
                window[window_pos] = value
                window_pos = (window_pos + 1) & 0xFFF
    return bytes(out)


class Library:
    def __init__(self, path: Path):
        self.name = path.name.upper()
        self.data = path.read_bytes()
        r = Reader(self.data)
        signature = r.read(4)
        if signature not in (b"LIB\x1a", b"LIC\x1a"):
            raise ValueError(f"{path}: invalid library signature")
        count = r.u16()
        if signature == b"LIC\x1a":
            r.read((count + 1) * 8)
        records = []
        for _ in range(count):
            name = r.read(13).split(b"\0", 1)[0].decode("ascii").upper()
            records.append((name, r.u32()))
        self.entries = {}
        for i, (name, start) in enumerate(records):
            end = records[i + 1][1] if i + 1 < count else len(self.data)
            if not name or name in self.entries or start < r.pos or end <= start or end > len(self.data):
                raise ValueError(f"{path}: invalid/duplicate archive entry {name!r}")
            self.entries[name] = (start, end)

    def get(self, name: str) -> tuple[bytes, dict]:
        start, end = self.entries[name]
        stored = self.data[start:end]
        decoded = decompress(stored)
        return decoded, {"archive": self.name, "resource": name,
                         "stored_size": len(stored), "stored_md5": md5(stored),
                         "decompressed_size": len(decoded), "decompressed_md5": md5(decoded)}


def parse_sequence(data: bytes) -> list[str]:
    """Read the names in Tattoo's count + WalkSequence::load records."""
    r = Reader(data)
    count = r.u8()
    if not count:
        raise ValueError("empty walk sequence")
    result = []
    for _ in range(count):
        raw = r.read(9).split(b"\0", 1)[0]
        try:
            name = raw.decode("ascii").upper() + ".VGS"
        except UnicodeDecodeError as exc:
            raise ValueError("non-ASCII walk resource name") from exc
        if not NAME.fullmatch(name):
            raise ValueError(f"invalid walk resource name {name!r}")
        r.u8()  # horizontal flip, applied at runtime, not to source frames
        size = r.u16()
        r.read(4)  # serialized pointer, ignored by the engine
        r.read(size)
        result.append(name)
    # Some real files have trailing unused bytes; the engine reads count records.
    return result


def discover_sequences(libraries: list[Library]) -> tuple[dict[str, list[str]], list[dict]]:
    references: dict[str, set[str]] = {}
    sequences = []
    for library in libraries:
        for name in sorted(library.entries):
            if not name.endswith(".SEQ") or name in EXCLUDED_SEQUENCES:
                continue
            data, provenance = library.get(name)
            try:
                resources = sorted(set(parse_sequence(data)))
            except ValueError as exc:
                raise ValueError(f"{library.name}:{name}: {exc}") from exc
            sequences.append({**provenance, "referenced_resources": resources})
            for resource in resources:
                references.setdefault(resource, set()).add(f"{library.name}:{name}")
    if not sequences:
        raise ValueError("no Tattoo walk sequences found")
    return {key: sorted(value) for key, value in references.items()}, sequences


@dataclass
class Frame:
    record: dict
    indices: bytes


def decode_frame(data: bytes, width: int, height: int, encoding: str) -> bytes:
    count = width * height
    if encoding == "raw":
        if len(data) != count:
            raise ValueError("bad raw frame length")
        return data
    out = bytearray([TRANSPARENT]) * count
    if encoding == "nibble":
        if len(data) != count // 2:
            raise ValueError("bad nibble frame length")
        for i, value in enumerate(data):
            out[2 * i] = value & 15
            out[2 * i + 1] = value >> 4
        return bytes(out)
    if encoding != "rle":
        raise ValueError(f"unknown encoding {encoding}")
    r = Reader(data)
    for y in range(height):
        x = 0
        while x < width:
            skip = r.u8()
            x += skip
            if x > width:
                raise ValueError("RLE skip exceeds row")
            if x == width:
                break
            run = r.u8()
            if (skip == 0 and run == 0) or x + run > width:
                raise ValueError("invalid RLE run")
            out[y * width + x:y * width + x + run] = r.read(run)
            x += run
    return bytes(out)


def decode_vgs(data: bytes) -> tuple[list[Frame], bool]:
    """Decode all native indices using ImageFile's s0a0 walk-frame layout."""
    r = Reader(data)
    has_palette = (len(data) >= 12 and data[:8] == struct.pack("<HHBBBB", 389, 1, 0, 0, 0, 0)
                   and data[8:12] == b"VGA ")
    if has_palette:
        r.read(8 + 12 + 768)  # parsed but not used by walk blits
    frames = []
    while r.pos < len(data):
        frame_offset = r.pos
        width, height = (r.u16() + 1) & 0xFFFF, (r.u16() + 1) & 0xFFFF
        palette_base, rle, x, y = r.u8(), r.u8(), r.u8(), r.u8()
        invalid = width > 32768 or height > 32768
        if width > 32768:
            width = 65536 - width
        if height > 32768:
            height = 65536 - height
        if not width or not height or width * height > 64 * 1024 * 1024:
            raise ValueError("invalid frame dimensions")
        marker = None
        if palette_base:
            encoding, size = "nibble", width * height // 2
        elif rle == 1:
            encoding, size = "rle", r.u16() - 11
            marker = r.u8()
            if size < 0:
                raise ValueError("invalid RLE frame length")
        else:
            encoding, size = "raw", width * height
        pixels = r.read(size)
        indices = bytes([TRANSPARENT]) * (width * height) if invalid else decode_frame(pixels, width, height, encoding)
        index = len(frames)
        record = {"index": index, "file": f"frame_{index:03d}.png", "width": width, "height": height,
                  "offset_x": x, "offset_y": y, "encoding": encoding, "palette_base": palette_base,
                  "rle_marker": marker, "encoded_size": size, "offset_in_resource": frame_offset,
                  "invalid_placeholder": invalid}
        frames.append(Frame(record, indices))
    if not frames:
        raise ValueError("VGS has no frames")
    return frames, has_palette


def used_indices(frames: list[Frame]) -> list[int]:
    used = set()
    for frame in frames:
        used.update(frame.indices)
    used.discard(TRANSPARENT)
    return sorted(used)


def palette_payload(indices: list[int], palette: bytes) -> bytes:
    """Encode RT-PAL1, the 256-bit index set, and ascending 8-bit triples."""
    if len(palette) != 768:
        raise ValueError("palette must have 256 RGB triples")
    if sorted(set(indices)) != indices or any(i < 0 or i >= TRANSPARENT for i in indices):
        raise ValueError("indices must be sorted, unique and exclude 255")
    bits = bytearray(32)
    colors = bytearray()
    for index in indices:
        bits[index // 8] |= 1 << (index % 8)
        colors.extend(palette[index * 3:index * 3 + 3])
    return MAGIC + bytes(bits) + bytes(colors)


def project_palettes(indices: list[int], scenes: dict[int, bytes]) -> list[dict]:
    groups: dict[str, dict] = {}
    for scene, palette in sorted(scenes.items()):
        payload = palette_payload(indices, palette)
        digest = md5(payload)
        if digest in groups and groups[digest]["payload"] != payload:
            raise ValueError(f"palette MD5 collision: {digest}")
        group = groups.setdefault(digest, {"digest": digest, "payload": payload, "palette": palette, "scenes": []})
        group["scenes"].append(scene)
    return [groups[key] for key in sorted(groups)]


def render(frame: Frame, palette: bytes) -> Image.Image:
    image = Image.frombytes("P", (frame.record["width"], frame.record["height"]), frame.indices)
    # Transparent pixel RGB is deterministic and independent of palette[255].
    colors = bytearray(palette)
    colors[765:768] = b"\0\0\0"
    image.putpalette(bytes(colors))
    image.info["transparency"] = TRANSPARENT
    return image.convert("RGBA")


def case_files(directory: Path) -> dict[str, Path]:
    result = {}
    for path in directory.iterdir():
        if path.is_file():
            key = path.name.upper()
            if key not in ARCHIVES and not re.fullmatch(r"RES\d+\.RRM", key):
                continue
            if key in result:
                raise ValueError(f"duplicate case-insensitive filenames: {path.name}")
            result[key] = path
    return result


def room_palettes(files: dict[str, Path], selected: list[int] | None) -> dict[int, bytes]:
    rooms = {}
    for name, path in files.items():
        match = re.fullmatch(r"RES(\d+)\.RRM", name)
        if match:
            scene = int(match.group(1))
            if scene in rooms:
                raise ValueError(f"duplicate scene number {scene}")
            rooms[scene] = path
    if selected is not None:
        if not selected or any(scene < 1 for scene in selected):
            raise ValueError("scene numbers must be positive")
        if len(set(selected)) != len(selected):
            raise ValueError("scene numbers must not be repeated")
        missing = sorted(set(selected) - set(rooms))
        if missing:
            raise ValueError(f"room files not found for scenes: {missing}")
        rooms = {scene: rooms[scene] for scene in selected}
    if not rooms:
        raise ValueError("no room palettes found")
    palettes = {}
    for scene, path in sorted(rooms.items()):
        with path.open("rb") as f:
            f.seek(40)
            offset = f.read(4)
            if len(offset) != 4:
                raise ValueError(f"{path}: truncated room header")
            f.seek(struct.unpack("<I", offset)[0] + 17)
            raw = f.read(768)
        if len(raw) != 768 or any(value > 63 for value in raw):
            raise ValueError(f"{path}: invalid 6-bit room palette")
        palettes[scene] = bytes(value * 255 // 63 for value in raw)
    return palettes


def resource_path(resource: str, resource_digest: str, palette_digest: str) -> Path:
    return Path(resource.lower().replace(".", "_")) / "variants" / f"{resource_digest}-s0a0" / "palettes" / palette_digest


def collect_variants(libraries: list[Library], resources: list[str]) -> list[dict]:
    result = []
    for resource in resources:
        variants = {}
        if resource in AMBIGUOUS:
            missing = [library.name for library in libraries if resource not in library.entries]
            if missing:
                raise ValueError(f"{resource}: missing required ambiguous variant in {', '.join(missing)}")
        for library in libraries:
            if resource not in library.entries:
                continue
            data, provenance = library.get(resource)
            digest = md5(data)
            if digest in variants and variants[digest]["data"] != data:
                raise ValueError(f"{resource}: resource MD5 collision {digest}")
            variant = variants.setdefault(digest, {"resource": resource, "digest": digest, "data": data, "sources": []})
            variant["sources"].append(provenance)
        if not variants:
            raise ValueError(f"missing VGS resource {resource} in both archives")
        for digest in sorted(variants):
            variant = variants[digest]
            try:
                variant["frames"], variant["embedded_palette"] = decode_vgs(variant["data"])
            except ValueError as exc:
                raise ValueError(f"{resource} {digest}: {exc}") from exc
            variant["indices"] = used_indices(variant["frames"])
            result.append(variant)
    return result


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")


def extract(data_dir: Path, output_dir: Path, selected_scenes: list[int] | None = None,
            selected_resources: list[str] | None = None) -> dict:
    if not data_dir.is_dir():
        raise ValueError(f"game data directory not found: {data_dir}")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    data_path, output_path = data_dir.resolve(), output_dir.resolve()
    if output_path == data_path or output_path.is_relative_to(data_path) or data_path.is_relative_to(output_path):
        raise ValueError("game data and output directories must not overlap")
    files = case_files(data_dir)
    try:
        libraries = [Library(files[name]) for name in ARCHIVES]
    except KeyError as exc:
        raise ValueError(f"missing required library {exc.args[0]}") from exc
    references, sequences = discover_sequences(libraries)
    available = set(references) | set(EXTRAS)
    if selected_resources is None:
        resources = sorted(available)
    else:
        resources = [resource.upper() for resource in selected_resources]
        if not resources or len(set(resources)) != len(resources) or any(not NAME.fullmatch(name) for name in resources):
            raise ValueError("resource names must be distinct simple .VGS filenames")
        unknown = sorted(set(resources) - available)
        if unknown:
            raise ValueError(f"resources not in walk sequences or extras: {unknown}")
        resources.sort()
    palettes = room_palettes(files, selected_scenes)
    variants = collect_variants(libraries, resources)
    # Validate all hashes, input files, frames and projections before creating output.
    for variant in variants:
        variant["projections"] = project_palettes(variant["indices"], palettes)
    output_dir.mkdir()  # parent exists; atomic refusal of an existing destination
    projections = []
    for variant in variants:
        resource, digest, frames = variant["resource"], variant["digest"], variant["frames"]
        for projection in variant["projections"]:
            relative = resource_path(resource, digest, projection["digest"])
            directory = output_dir / relative
            directory.mkdir(parents=True, exist_ok=False)
            with (directory / "palette.bin").open("xb") as f:
                f.write(projection["payload"])
            for frame in frames:
                with (directory / frame.record["file"]).open("xb") as f:
                    render(frame, projection["palette"]).save(f, format="PNG")
            metadata = {
                "resource_name": resource, "resource_md5": digest, "decode_mode": MODE,
                "source_archives": variant["sources"], "sequence_references": references.get(resource, []),
                "conservative_extra": resource in EXTRAS, "embedded_palette": variant["embedded_palette"],
                "palette_source": "initial RRM palette (embedded VGS palette not used for walk blits)",
                "palette_format": "RT-PAL1", "palette_md5": projection["digest"],
                "used_indices": variant["indices"], "source_scenes": projection["scenes"],
                "sidecars": ["palette.bin"], "alpha_policy": "index-255-transparent",
                "frame_count": len(frames), "frames": [frame.record for frame in frames],
                "limitations": LIMITATIONS,
            }
            write_json(directory / "metadata.json", metadata)
            projections.append({"resource": resource, "resource_md5": digest, "palette_md5": projection["digest"],
                                "path": relative.as_posix(), "scenes": projection["scenes"], "frames": len(frames)})
        print(f"{resource} {digest}: {len(variant['projections'])} palettes, {len(frames)} frames each", flush=True)
    manifest = {
        "format": "RT-palette-walk-sources-v1", "decode_mode": MODE,
        "scenes": sorted(palettes), "scene_count": len(palettes), "resources": resources,
        "resource_count": len(resources), "identity_count": len(variants),
        "projection_count": len(projections), "frame_count": sum(item["frames"] for item in projections),
        "sequences": sequences, "excluded_sequences": EXCLUDED_SEQUENCES,
        "conservative_extras": [name for name in EXTRAS if name in resources],
        "projections": projections, "limitations": LIMITATIONS,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--output-dir", type=Path, required=True, help="New output directory; its parent must exist")
    parser.add_argument("--scenes", type=int, nargs="+", help="Room numbers; default: all available RES*.RRM")
    parser.add_argument("--resources", nargs="+", help="Subset of discovered walk .VGS names and conservative extras")
    args = parser.parse_args()
    result = extract(args.data_dir, args.output_dir, args.scenes, args.resources)
    print(f"{result['scene_count']} scenes, {result['resource_count']} names, {result['identity_count']} identities, "
          f"{result['projection_count']} projections, {result['frame_count']} frame paths")


if __name__ == "__main__":
    main()
