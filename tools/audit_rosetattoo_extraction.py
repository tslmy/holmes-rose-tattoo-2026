#!/usr/bin/env python3
"""Check room and cAnim frame extractions against the installed RRM data.

This verifies decoded frame pixels, dimensions, indices, and offsets. It does
not establish that runtime overrides render in the right order or palette.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

from PIL import Image

from extract_rosetattoo_assets import Reader
from extract_rosetattoo_canimations import decode_frames as decode_canim_frames, parse_room
from extract_rosetattoo_room_sprites import iter_frames as decode_room_frames, read_block


def compare_resource(directory: Path, expected, identity: dict) -> int:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    for key, value in identity.items():
        if metadata.get(key) != value:
            raise ValueError(f"{directory}: {key} mismatch")
    records = metadata["frames"]
    if metadata.get("frame_count") != len(records):
        raise ValueError(f"{directory}: inconsistent frame count")
    count = 0
    for decoded_record, decoded_image in expected:
        if count >= len(records):
            raise ValueError(f"{directory}: extraction has too few frames")
        record = records[count]
        for key in ("index", "file", "width", "height", "offset_x", "offset_y"):
            if record.get(key) != decoded_record.get(key):
                raise ValueError(f"{directory}: frame {count} {key} mismatch")
        with Image.open(directory / record["file"]) as saved:
            rgba = saved.convert("RGBA")
            if rgba.size != decoded_image.size or rgba.tobytes() != decoded_image.tobytes():
                raise ValueError(f"{directory}: frame {count} pixels differ from decoded resource")
        count += 1
    if count != len(records):
        raise ValueError(f"{directory}: extraction has too many frames")
    return count


def audit_scene(path: Path, room_root: Path, canim_root: Path) -> dict:
    match = re.fullmatch(r"RES(\d+)\.RRM", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid scene resource name: {path}")
    scene = int(match.group(1))
    data = path.read_bytes()
    compressed, palette, entries = parse_room(data)
    header_offset = struct.unpack_from("<I", data, 40)[0]
    room_count = struct.unpack_from("<H", data, header_offset + 2)[0]
    scene_dir = f"scene_{scene:03d}"
    room_frames = 0
    for slot in range(1, room_count + 1):
        directory = room_root / scene_dir / f"room_{slot:03d}"
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        reader = Reader(data)
        reader.seek(metadata["chunk_offset"])
        chunk = read_block(reader, metadata["uncompressed_size"], compressed, decode=True)
        room_frames += compare_resource(directory, decode_room_frames(chunk, palette),
                                        {"scene_id": scene, "room_index": slot})
    canims = 0
    canim_frames = 0
    for entry in entries:
        if not entry["data_size"]:
            continue
        index = entry["index"]
        directory = canim_root / scene_dir / f"canim_{index:03d}"
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("animation", {}).get("data_offset") != entry["data_offset"] or \
                metadata.get("animation", {}).get("data_size") != entry["data_size"]:
            raise ValueError(f"{directory}: cAnim stream location mismatch")
        start, length = entry["data_offset"], entry["data_size"]
        decoded = decode_canim_frames(data[start:start + length], compressed, palette, start)
        canim_frames += compare_resource(directory, decoded, {"scene_id": scene, "canim_index": index})
        canims += 1
    return {"scene": scene, "room_resources": room_count, "room_frames": room_frames,
            "canim_resources": canims, "canim_frames": canim_frames}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--room-source", type=Path, required=True)
    parser.add_argument("--canim-source", type=Path, required=True)
    parser.add_argument("--scenes", type=int, nargs="+", help="Limit audit to these room numbers")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    paths = ([args.data_dir / f"RES{scene:02d}.RRM" for scene in args.scenes] if args.scenes else
             sorted(path for path in args.data_dir.glob("RES*.RRM") if re.fullmatch(r"RES\d+\.RRM", path.name)))
    if not paths:
        parser.error("no matching room files")
    results = []
    errors = []
    for path in paths:
        try:
            result = audit_scene(path, args.room_source, args.canim_source)
            results.append(result)
            print(f"{path.name}: {result['room_frames']} room frames, {result['canim_frames']} cAnim frames verified", flush=True)
        except (OSError, ValueError, KeyError, TypeError, EOFError, struct.error) as error:
            errors.append({"scene": path.name, "error": str(error)})
            print(f"{path.name}: ERROR {error}", flush=True)
    report = {"scenes_checked": len(paths), "scenes_passed": len(results), "results": results, "errors": errors}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Extraction: {len(results)}/{len(paths)} scenes verified; {len(errors)} errors")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
