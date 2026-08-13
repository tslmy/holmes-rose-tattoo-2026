#!/usr/bin/env python3
"""Assemble a complete Rose Tattoo runtime override tree.

The playable tree is deliberately built from validated, scale-qualified
backgrounds and the temporal sprite set. Identity-consistent character redraws
can be copied in for offline inspection, but are not enabled by the runtime
unless its experimental scene-sprite overlay is explicitly requested. Keeping
this as an explicit assembly step prevents a partial experiment from being
mistaken for a complete playable pack.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_files(source: Path, destination: Path, pattern: str) -> int:
    count = 0
    for item in sorted(source.rglob(pattern)):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--background-dir",
        type=Path,
        default=ROOT / "generated" / "neural-redraws-cinematic-full",
    )
    parser.add_argument(
        "--sprite-dir",
        type=Path,
        default=ROOT / "enhanced" / "sprites-temporal",
    )
    parser.add_argument(
        "--lifelike-sprite-dir",
        type=Path,
        default=ROOT / "enhanced" / "sprites-lifelike-v2",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "generated" / "playable-overrides-2x",
    )
    parser.add_argument(
        "--include-experimental-lifelike-sprites",
        action="store_true",
        help=(
            "Overlay generated character sequences for offline review only. "
            "They require SCUMMVM_SHERLOCK_TATTOO_EXPERIMENTAL_SCENE_SPRITES=1 "
            "and are not part of the safe playable default."
        ),
    )
    args = parser.parse_args()

    required_inputs = [args.background_dir, args.sprite_dir]
    if args.include_experimental_lifelike_sprites:
        required_inputs.append(args.lifelike_sprite_dir)
    for required in required_inputs:
        if not required.is_dir():
            raise SystemExit(f"missing input directory: {required}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    background_count = copy_files(args.background_dir, args.output_dir, "background@2x.png")
    sprite_root = args.output_dir / "sprites"
    sprite_count = copy_files(args.sprite_dir, sprite_root, "frame_*@2x.png")
    copy_files(args.sprite_dir, sprite_root, "metadata.json")
    lifelike_count = 0
    if args.include_experimental_lifelike_sprites:
        lifelike_count = copy_files(
            args.lifelike_sprite_dir, sprite_root, "frame_*@2x.png"
        )
        copy_files(args.lifelike_sprite_dir, sprite_root, "metadata.json")

    scene_count = len(list(args.output_dir.glob("scene_*/background@2x.png")))
    resource_names = sorted(
        path.parent.name for path in sprite_root.glob("*/metadata.json")
    )
    manifest = {
        "name": "Rose Tattoo playable 2x visual override pack",
        "scale": 2,
        "background_source": str(args.background_dir.resolve()),
        "temporal_sprite_source": str(args.sprite_dir.resolve()),
        "lifelike_sprite_source": str(args.lifelike_sprite_dir.resolve()),
        "scene_count": scene_count,
        "background_files_copied": background_count,
        "temporal_sprite_frames_copied": sprite_count,
        "lifelike_sprite_frames_overlaid": lifelike_count,
        "experimental_lifelike_sprites_included": args.include_experimental_lifelike_sprites,
        "sprite_resource_count": len(resource_names),
        "sprite_resources": resource_names,
        "runtime_contract": {
            "asset_override_env": "SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES",
            "background_pattern": "scene_NNN/background@2x.png",
            "sprite_pattern": "sprites/<resource>/frame_NNN@2x.png",
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
