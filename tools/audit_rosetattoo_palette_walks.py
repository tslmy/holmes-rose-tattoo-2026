#!/usr/bin/env python3
"""Read-only validation of a palette-qualified walk extraction and its inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

import extract_rosetattoo_palette_walks as walks


def audit(data_dir: Path, source_dir: Path) -> dict:
    if not data_dir.is_dir() or not source_dir.is_dir():
        raise ValueError("game data and extraction directories must exist")
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    scenes, resources = manifest["scenes"], manifest["resources"]
    if not isinstance(scenes, list) or not isinstance(resources, list) or not resources:
        raise ValueError("invalid manifest selections")
    if any(not isinstance(name, str) or not walks.NAME.fullmatch(name) for name in resources) or resources != sorted(set(resources)):
        raise ValueError("invalid manifest resource names")
    files = walks.case_files(data_dir)
    try:
        libraries = [walks.Library(files[name]) for name in walks.ARCHIVES]
    except KeyError as exc:
        raise ValueError(f"missing required library {exc.args[0]}") from exc
    references, sequences = walks.discover_sequences(libraries)
    if not set(resources) <= set(references) | set(walks.EXTRAS):
        raise ValueError("manifest selects an undiscovered walk resource")
    palettes = walks.room_palettes(files, scenes)
    variants = walks.collect_variants(libraries, resources)
    expected_files = {Path("manifest.json")}
    expected_dirs = set()
    projections = []
    for variant in variants:
        resource, digest, frames = variant["resource"], variant["digest"], variant["frames"]
        for projection in walks.project_palettes(variant["indices"], palettes):
            relative = walks.resource_path(resource, digest, projection["digest"])
            directory = source_dir / relative
            expected_dirs.update(parent for parent in (relative, *relative.parents) if parent != Path("."))
            expected_files.update(relative / name for name in ["palette.bin", "metadata.json", *[f.record["file"] for f in frames]])
            payload = (directory / "palette.bin").read_bytes()
            if payload != projection["payload"] or walks.md5(payload) != projection["digest"]:
                raise ValueError(f"{relative}: palette sidecar differs")
            metadata = {
                "resource_name": resource, "resource_md5": digest, "decode_mode": walks.MODE,
                "source_archives": variant["sources"], "sequence_references": references.get(resource, []),
                "conservative_extra": resource in walks.EXTRAS, "embedded_palette": variant["embedded_palette"],
                "palette_source": "initial RRM palette (embedded VGS palette not used for walk blits)",
                "palette_format": "RT-PAL1", "palette_md5": projection["digest"],
                "used_indices": variant["indices"], "source_scenes": projection["scenes"],
                "sidecars": ["palette.bin"], "alpha_policy": "index-255-transparent",
                "frame_count": len(frames), "frames": [frame.record for frame in frames],
                "limitations": walks.LIMITATIONS,
            }
            if json.loads((directory / "metadata.json").read_text(encoding="utf-8")) != metadata:
                raise ValueError(f"{relative}: metadata differs")
            for frame in frames:
                path = directory / frame.record["file"]
                expected = walks.render(frame, projection["palette"])
                with Image.open(path) as image:
                    if (image.format != "PNG" or image.mode != "RGBA" or image.size != expected.size
                            or image.tobytes() != expected.tobytes()):
                        raise ValueError(f"{path}: frame pixels differ")
            projections.append({"resource": resource, "resource_md5": digest, "palette_md5": projection["digest"],
                                "path": relative.as_posix(), "scenes": projection["scenes"], "frames": len(frames)})
        print(f"{resource} {digest}: {len(frames)} frames per projection verified", flush=True)
    expected_manifest = {
        "format": "RT-palette-walk-sources-v1", "decode_mode": walks.MODE,
        "scenes": sorted(palettes), "scene_count": len(palettes), "resources": resources,
        "resource_count": len(resources), "identity_count": len(variants),
        "projection_count": len(projections), "frame_count": sum(item["frames"] for item in projections),
        "sequences": sequences, "excluded_sequences": walks.EXCLUDED_SEQUENCES,
        "conservative_extras": [name for name in walks.EXTRAS if name in resources],
        "projections": projections, "limitations": walks.LIMITATIONS,
    }
    if manifest != expected_manifest:
        raise ValueError("manifest differs from game data")
    actual_files, actual_dirs = set(), set()
    for path in source_dir.rglob("*"):
        relative = path.relative_to(source_dir)
        if path.is_symlink():
            raise ValueError(f"unexpected symlink: {path}")
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_dirs.add(relative)
        else:
            raise ValueError(f"unexpected filesystem entry: {path}")
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise ValueError("extraction contains missing or unexpected paths")
    return expected_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.data_dir, args.source_dir)
    print(f"verified {result['scene_count']} scenes, {result['identity_count']} identities, "
          f"{result['projection_count']} projections, {result['frame_count']} frames")


if __name__ == "__main__":
    main()
