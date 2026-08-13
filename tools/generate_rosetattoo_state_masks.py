#!/usr/bin/env python3
"""Generate story-flag-specific hotspot and geometry masks.

The room resource stores two optional required flags per object. ScummVM's
Rose Tattoo engine restores an object only when every non-zero required flag
is set; otherwise it hides the object. This tool mirrors that rule without
needing to run the game and emits masks for representative flag states.

The source-derived masks are intentionally conservative. They describe where
the game may put an interactable object, not a claim that every pixel inside
the rectangle is its visible silhouette.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_ACTION_TYPES = {"nowalk_zone", "blank_zone", "script_zone"}


def _valid_bounds(bounds: dict | None, size: tuple[int, int]) -> bool:
    if not bounds:
        return False
    x, y, w, h = (bounds.get(key, 0) for key in ("x", "y", "w", "h"))
    width, height = size
    return 0 <= x < width and 0 <= y < height and 1 <= w <= width and 1 <= h <= height


def _active(obj: dict, flags_on: set[int]) -> bool:
    """Mirror SherlockEngine::readFlags, including negative flag literals."""
    for literal in obj.get("required_flags", []):
        literal = int(literal)
        if literal == 0:
            continue
        flag_on = abs(literal) in flags_on
        if (literal > 0) != flag_on:
            return False
    return True


def _object_mask(objects: list[dict], flags_on: set[int], size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for obj in objects:
        if not _active(obj, flags_on) or obj.get("action_type") in EXCLUDED_ACTION_TYPES:
            continue
        if not _valid_bounds(obj.get("bounds"), size):
            continue
        bounds = obj["bounds"]
        x, y, w, h = (bounds[key] for key in ("x", "y", "w", "h"))
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=255)
    return mask


def _rect_mask(rects: list[dict], size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for bounds in rects:
        if not _valid_bounds(bounds, size):
            continue
        x, y, w, h = (bounds[key] for key in ("x", "y", "w", "h"))
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=255)
    return mask


def _scenario_flags(flag_ids: list[int], max_combinations: int) -> list[set[int]]:
    if len(flag_ids) <= 10 and (1 << len(flag_ids)) <= max_combinations:
        return [
            {flag for flag, bit in zip(flag_ids, bits) if bit}
            for bits in itertools.product((0, 1), repeat=len(flag_ids))
        ]

    # Large flag sets are unusual, but exhaustive expansion would create a
    # misleadingly expensive build. These states cover every individual flag,
    # the empty state, and the fully-enabled state deterministically.
    states = [set(), set(flag_ids)]
    states.extend({flag} for flag in flag_ids)
    return states


def _state_name(flags_on: set[int]) -> str:
    if not flags_on:
        return "flags_none"
    return "flags_" + "_".join(str(flag) for flag in sorted(flags_on)) + "_on"


def generate_scene(scene_dir: Path, max_combinations: int) -> dict:
    metadata_path = scene_dir / "metadata.json"
    background_path = scene_dir / "background.png"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    with Image.open(background_path) as image:
        size = image.size

    objects = metadata.get("objects", [])
    flag_ids = sorted(
        {
            abs(int(flag))
            for obj in objects
            for flag in obj.get("required_flags", [])
            if int(flag) != 0
        }
    )
    walk_zones = metadata.get("walk_zones", [])
    scenarios = _scenario_flags(flag_ids, max_combinations)
    state_root = scene_dir / "states"
    state_root.mkdir(parents=True, exist_ok=True)
    records = []

    for flags_on in scenarios:
        name = _state_name(flags_on)
        output_dir = state_root / name
        output_dir.mkdir(parents=True, exist_ok=True)
        hotspots = _object_mask(objects, flags_on, size)
        walk = _rect_mask(walk_zones, size)
        protect = ImageChops.lighter(walk, hotspots)
        structure = Image.new("RGB", size, (0, 0, 0))
        structure.paste((0, 160, 255), mask=walk)
        structure.paste((255, 200, 0), mask=hotspots)
        hotspots.save(output_dir / "hotspots_mask.png")
        protect.save(output_dir / "protect_mask.png")
        structure.save(output_dir / "structure_control.png")
        active_objects = [
            int(obj["index"])
            for obj in objects
            if _active(obj, flags_on)
            and obj.get("action_type") not in EXCLUDED_ACTION_TYPES
            and _valid_bounds(obj.get("bounds"), size)
        ]
        records.append(
            {
                "name": name,
                "flags_on": sorted(flags_on),
                "active_object_indices": active_objects,
                "hotspot_mask": str((output_dir / "hotspots_mask.png").relative_to(scene_dir)),
                "protect_mask": str((output_dir / "protect_mask.png").relative_to(scene_dir)),
            }
        )

    manifest = {
        "scene_id": metadata.get("scene_id"),
        "scene_name": metadata.get("scene_name"),
        "background_size": {"width": size[0], "height": size[1]},
        "required_flag_variables": flag_ids,
        "scenario_count": len(records),
        "selection": (
            "exhaustive" if len(flag_ids) <= 10 and (1 << len(flag_ids)) <= max_combinations
            else "bounded"
        ),
        "engine_rule": "object active iff every required flag literal passes readFlags; negative literals require false",
        "states": records,
    }
    (scene_dir / "state_masks.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--scenes", type=int, nargs="*", default=None)
    parser.add_argument("--max-combinations", type=int, default=1024)
    args = parser.parse_args()
    scene_dirs = sorted(args.input_dir.glob("scene_*/"))
    if args.scenes is not None:
        wanted = set(args.scenes)
        scene_dirs = [path for path in scene_dirs if int(path.name.split("_")[1]) in wanted]
    total = 0
    for scene_dir in scene_dirs:
        if not (scene_dir / "metadata.json").exists() or not (scene_dir / "background.png").exists():
            continue
        manifest = generate_scene(scene_dir, args.max_combinations)
        total += manifest["scenario_count"]
        print(
            f"scene {manifest['scene_id']:02d}: {manifest['scenario_count']} states "
            f"({manifest['selection']}, flag_variables={manifest['required_flag_variables']})"
        )
    print(f"generated {total} state masks")


if __name__ == "__main__":
    main()
