#!/usr/bin/env python3
"""Extract or audit both archive variants of five ambiguous shared VGS files.

This is a separate command from the first-match sprite extractor. Its output
directory must be new; audit mode is read-only. See docs/vgs-variants.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from extract_rosetattoo_sprites import (
    decompress_if_necessary,
    load_library_index,
    load_scene_palette,
    load_vgs_image,
)


RESOURCES = ("HTUPRIGH.VGS", "JTDOWNRG.VGS", "JTRIGHT.VGS", "JTUPRIGH.VGS", "NOHAT.VGS")
ARCHIVES = ("VGS.LIB", "WALK.LIB")
MODE = {"skipPal": False, "animImages": False}
PALETTE_NOTE = (
    "The fallback room palette is an approximation; runtime scene and dynamic "
    "palette changes are not represented in these RGBA frames."
)


def md5(data: bytes) -> str:
    # This is an engine-compatible content identity, not a security check.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def collect_variants(data_dir: Path) -> dict[str, dict[str, dict]]:
    """Index both archives, grouping identical *decompressed* resource bytes."""
    grouped = {resource: {} for resource in RESOURCES}
    for archive in ARCHIVES:
        path = data_dir / archive
        data = path.read_bytes()
        index = load_library_index(data)
        for resource in RESOURCES:
            if resource not in index:
                raise ValueError(f"{archive}: missing {resource}")
            entry = index[resource]
            if entry.offset < 0 or entry.size <= 0 or entry.offset + entry.size > len(data):
                raise ValueError(f"{archive}: invalid bounds for {resource}")
            stored = data[entry.offset:entry.offset + entry.size]
            decoded = decompress_if_necessary(stored)
            digest = md5(decoded)
            variants = grouped[resource]
            if digest in variants and variants[digest]["data"] != decoded:
                # The engine path has only the MD5 to distinguish content.
                raise ValueError(f"{resource}: MD5 collision for distinct resource bytes: {digest}")
            variant = variants.setdefault(digest, {"data": decoded, "sources": []})
            variant["sources"].append({
                "archive": archive,
                "resource": resource,
                "decompressed_md5": digest,
                "decompressed_size": len(decoded),
                "stored_md5": md5(stored),
                "stored_size": len(stored),
            })
    return grouped


def resource_path(resource: str, digest: str) -> Path:
    return Path(resource.lower().replace(".", "_")) / "variants" / f"{digest}-s0a0"


def decoded_variant(resource: str, digest: str, variant: dict, palette: list[int], scene: int):
    _, frames = load_vgs_image(variant["data"], default_palette=palette, opaque=False)
    if not frames:
        raise ValueError(f"{resource} {digest}: no frames decoded")
    records = [{"index": i, "file": f"frame_{i:03d}.png", "width": frame.width,
                "height": frame.height, "offset_x": frame.offset_x, "offset_y": frame.offset_y}
               for i, frame in enumerate(frames)]
    metadata = {
        "resource_name": resource,
        "source": f"{variant['sources'][0]['archive']}:{resource}",
        "source_archives": variant["sources"],
        "resource_md5": digest,
        "decode_mode": MODE,
        "alpha_policy": "index-255-transparent",
        "fallback_palette": f"scene:{scene}",
        "palette_note": PALETTE_NOTE,
        "frame_count": len(records),
        "frames": records,
    }
    return metadata, frames


def validate_data_dir(data_dir: Path, scene: int) -> tuple[dict, list[int]]:
    if not data_dir.is_dir():
        raise ValueError(f"game data directory does not exist: {data_dir}")
    if scene < 1:
        raise ValueError("palette scene must be positive")
    return collect_variants(data_dir), load_scene_palette(data_dir, scene)


def write_json_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, indent=2, ensure_ascii=False)
        file.write("\n")


def extract(data_dir: Path, output_dir: Path, scene: int) -> dict:
    data_path, output_path = data_dir.resolve(), output_dir.resolve()
    if (output_path == data_path or output_path.is_relative_to(data_path)
            or data_path.is_relative_to(output_path)):
        raise ValueError("game data and output directories must not overlap")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    grouped, palette = validate_data_dir(data_dir, scene)
    # The parent must already exist. mkdir and all writes are exclusive, so an
    # existing output or a file created concurrently is never overwritten.
    output_dir.mkdir()
    summaries = []
    for resource, variants in grouped.items():
        for digest, variant in variants.items():
            metadata, frames = decoded_variant(resource, digest, variant, palette, scene)
            relative = resource_path(resource, digest)
            directory = output_dir / relative
            directory.mkdir(parents=True, exist_ok=False)
            for record, frame in zip(metadata["frames"], frames):
                with (directory / record["file"]).open("xb") as file:
                    frame.image.save(file, format="PNG")
            write_json_new(directory / "metadata.json", metadata)
            summaries.append({"resource": resource, "md5": digest, "path": relative.as_posix(),
                              "archives": [source["archive"] for source in variant["sources"]],
                              "frames": len(frames)})
            print(f"{relative}: {len(frames)} frames ({', '.join(summaries[-1]['archives'])})")
    manifest = {"palette_scene": scene, "decode_mode": MODE, "resource_count": len(grouped),
                "variant_count": len(summaries), "frame_count": sum(v["frames"] for v in summaries),
                "variants": summaries, "palette_note": PALETTE_NOTE}
    write_json_new(output_dir / "manifest.json", manifest)
    return manifest


def audit(data_dir: Path, source_dir: Path, scene: int) -> dict:
    """Re-read both archives, and check paths, provenance, frames and pixels."""
    if not source_dir.is_dir():
        raise ValueError(f"extraction directory does not exist: {source_dir}")
    grouped, palette = validate_data_dir(data_dir, scene)
    summaries = []
    for resource, variants in grouped.items():
        variant_root = source_dir / resource.lower().replace(".", "_") / "variants"
        expected_names = {f"{digest}-s0a0" for digest in variants}
        if not variant_root.is_dir() or {p.name for p in variant_root.iterdir()} != expected_names:
            raise ValueError(f"{variant_root}: variant directories do not match both archives")
        for digest, variant in variants.items():
            expected, frames = decoded_variant(resource, digest, variant, palette, scene)
            relative = resource_path(resource, digest)
            directory = source_dir / relative
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            if metadata != expected:
                raise ValueError(f"{directory}: metadata or archive provenance mismatch")
            expected_files = {"metadata.json", *(record["file"] for record in expected["frames"])}
            if {p.name for p in directory.iterdir()} != expected_files:
                raise ValueError(f"{directory}: frame files do not match metadata")
            for record, frame in zip(expected["frames"], frames):
                path = directory / record["file"]
                with Image.open(path) as image:
                    if (image.format != "PNG" or image.mode != "RGBA" or image.size != frame.image.size
                            or image.tobytes() != frame.image.tobytes()):
                        raise ValueError(f"{path}: decoded frame pixels differ")
            summaries.append({"resource": resource, "md5": digest, "path": relative.as_posix(),
                              "archives": [source["archive"] for source in variant["sources"]],
                              "frames": len(frames)})
            print(f"{relative}: {len(frames)} frames verified")
    expected_manifest = {"palette_scene": scene, "decode_mode": MODE, "resource_count": len(grouped),
                         "variant_count": len(summaries), "frame_count": sum(v["frames"] for v in summaries),
                         "variants": summaries, "palette_note": PALETTE_NOTE}
    actual_manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    if actual_manifest != expected_manifest:
        raise ValueError(f"{source_dir}: manifest mismatch")
    return expected_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--palette-scene", type=int, required=True,
                        help="Fallback room palette, e.g. 1 for RES01.RRM.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output-dir", type=Path, help="New extraction directory; parent must exist.")
    action.add_argument("--audit-dir", type=Path, help="Read-only verification of an extraction.")
    args = parser.parse_args()
    result = (extract(args.data_dir, args.output_dir, args.palette_scene) if args.output_dir
              else audit(args.data_dir, args.audit_dir, args.palette_scene))
    print(f"{result['resource_count']} resources, {result['variant_count']} variants, "
          f"{result['frame_count']} frames")


if __name__ == "__main__":
    main()
