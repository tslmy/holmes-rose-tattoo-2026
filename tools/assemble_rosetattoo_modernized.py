#!/usr/bin/env python3
"""Assemble a new 2x override pack from completed background and sprite runs.

Output must not already exist. This deliberately does not alter an existing
playable pack, game data, or save files. Use audit_rosetattoo_pack.py against
the original extraction manifests after assembly to verify coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from sprite_metadata import resource_metadata_paths


def assemble(backgrounds: Path, sprite_roots: list[Path], destination: Path, scale: int) -> dict:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing pack: {destination}")
    if not backgrounds.is_dir() or not sprite_roots or any(not root.is_dir() for root in sprite_roots):
        raise ValueError("A background directory and at least one valid sprite root are required")
    background_paths = sorted(backgrounds.glob(f"scene_*/background@{scale}x.png"))
    if not background_paths:
        raise ValueError("No scale-matching background overrides found")

    # Validate all inputs and collisions before creating the destination.
    copies: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    frame_count = 0
    resources = 0
    provenance: Counter[tuple] = Counter()
    for source in background_paths:
        if not re.fullmatch(r"scene_\d+", source.parent.name):
            raise ValueError(f"Invalid scene directory: {source.parent}")
        relative = Path(source.parent.name) / source.name
        if relative in seen:
            raise ValueError(f"Duplicate destination: {relative}")
        seen.add(relative)
        copies.append((source, relative))
    for root in sprite_roots:
        for manifest_path in resource_metadata_paths(root):
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("complete") is not True or metadata.get("scale") != scale:
                raise ValueError(f"Incomplete or incompatible sprite run: {manifest_path}")
            relative = manifest_path.parent.relative_to(root)
            dest_dir = Path("sprites") / relative
            manifest_dest = dest_dir / "metadata.json"
            if manifest_dest in seen:
                raise ValueError(f"Duplicate sprite resource: {dest_dir}")
            seen.add(manifest_dest)
            copies.append((manifest_path, manifest_dest))
            resources += 1
            alpha_refinement = metadata.get("alpha_refinement") or {}
            if not isinstance(alpha_refinement, dict):
                raise ValueError(f"Invalid alpha refinement metadata: {manifest_path}")
            provenance[(metadata.get("upscaler"), metadata.get("model_sha256"),
                        metadata.get("alpha_mode"), metadata.get("tile_size"), metadata.get("tile_pad"),
                        alpha_refinement.get("method"), alpha_refinement.get("radius"),
                        alpha_refinement.get("thin_guard"), alpha_refinement.get("preserve_support"))] += 1
            sidecars = metadata.get("sidecars", [])
            if not isinstance(sidecars, list):
                raise ValueError(f"Invalid sidecar list: {manifest_path}")
            for name in sidecars:
                if not isinstance(name, str) or Path(name).name != name or name == "metadata.json" or name.startswith("frame_"):
                    raise ValueError(f"Invalid sidecar filename in {manifest_path}: {name!r}")
                sidecar = manifest_path.parent / name
                sidecar_dest = dest_dir / name
                if not sidecar.is_file():
                    raise FileNotFoundError(f"Missing generated sidecar: {sidecar}")
                if sidecar_dest in seen:
                    raise ValueError(f"Duplicate destination: {sidecar_dest}")
                seen.add(sidecar_dest)
                copies.append((sidecar, sidecar_dest))
            for record in metadata["frames"]:
                filename = record["file"]
                if Path(filename).name != filename or not filename.endswith(f"@{scale}x.png"):
                    raise ValueError(f"Invalid frame filename in {manifest_path}: {filename}")
                source = manifest_path.parent / filename
                if not source.is_file():
                    raise FileNotFoundError(f"Missing generated frame: {source}")
                frame_dest = dest_dir / filename
                if frame_dest in seen:
                    raise ValueError(f"Duplicate destination: {frame_dest}")
                seen.add(frame_dest)
                copies.append((source, frame_dest))
                frame_count += 1
    if not resources:
        raise ValueError("No sprite resource manifests found")

    destination.mkdir(parents=True, exist_ok=False)
    for source, relative in copies:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "name": "Rose Tattoo modernized visual override pack",
        "scale": scale,
        "background_source": str(backgrounds.resolve()),
        "sprite_sources": [str(root.resolve()) for root in sprite_roots],
        "background_count": len(background_paths),
        "sprite_resource_count": resources,
        "sprite_frame_count": frame_count,
        "sprite_provenance": [
            {"upscaler": key[0], "model_sha256": key[1], "alpha_mode": key[2],
             "tile_size": key[3], "tile_pad": key[4], "alpha_refinement": key[5],
             "alpha_radius": key[6], "alpha_thin_guard": key[7], "alpha_preserve_support": key[8],
             "resource_count": count}
            for key, count in sorted(provenance.items(), key=lambda item: str(item[0]))
        ],
        "note": "Assembly alone does not establish extraction coverage, runtime support, or visual quality.",
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background-dir", type=Path, required=True)
    parser.add_argument("--sprite-dir", type=Path, action="append", required=True,
                        help="Completed generated sprite tree; repeat for room, cutscene and shared assets.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=2)
    args = parser.parse_args()
    if args.scale < 1:
        parser.error("--scale must be >= 1")
    manifest = assemble(args.background_dir, args.sprite_dir, args.output_dir, args.scale)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
