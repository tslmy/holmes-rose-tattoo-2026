#!/usr/bin/env python3
"""Audit a high-resolution pack against extracted scene and frame manifests.

This checks asset coverage and PNG dimensions, not visual quality or correct
runtime drawing. Source metadata must come from a completed extraction; an
asset pack can pass these checks and still require in-game playtesting.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

from sprite_metadata import resource_metadata_paths


def png_problem(path: Path, size: tuple[int, int], require_rgba: bool = False) -> str | None:
    if not path.is_file():
        return "missing"
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return f"format {image.format}, expected PNG"
            if image.size != size:
                return f"size {image.size}, expected {size}"
            if require_rgba and image.mode != "RGBA":
                return f"mode {image.mode}, expected RGBA"
            image.verify()
    except (OSError, ValueError, SyntaxError) as error:
        return f"invalid PNG: {error}"
    return None


def audit_backgrounds(source_root: Path, pack: Path, scale: int, scenes: set[int] | None = None) -> dict:
    problems = []
    checked = 0
    valid = 0
    seen = set()
    for source in sorted(source_root.glob("scene_*/background.png")):
        match = re.fullmatch(r"scene_0*(\d+)", source.parent.name)
        if not match:
            continue
        scene = int(match.group(1))
        if scenes is not None and scene not in scenes:
            continue
        seen.add(scene)
        with Image.open(source) as image:
            expected_size = (image.width * scale, image.height * scale)
        output = pack / f"scene_{scene:03d}" / f"background@{scale}x.png"
        problem = png_problem(output, expected_size)
        checked += 1
        if problem:
            problems.append({"path": str(output), "problem": problem})
        else:
            valid += 1
    if scenes is not None:
        for scene in sorted(scenes - seen):
            problems.append({"path": str(source_root / f"scene_{scene:02d}" / "background.png"),
                             "problem": "selected source scene is missing"})
    return {"expected": checked, "valid": valid, "problems": problems}


def audit_sprites(source_root: Path, pack: Path, scale: int, scenes: set[int] | None = None) -> dict:
    problems = []
    per_scene: dict[str, Counter] = {}
    resources = 0
    frames = 0
    for metadata_path in resource_metadata_paths(source_root):
        relative = metadata_path.parent.relative_to(source_root)
        scene_match = re.fullmatch(r"scene_0*(\d+)", relative.parts[0]) if relative.parts else None
        if scenes is not None and scene_match and int(scene_match.group(1)) not in scenes:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            records = metadata["frames"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems.append({"path": str(metadata_path), "problem": f"invalid source manifest: {error}"})
            continue
        resources += 1
        scene = relative.parts[0] if relative.parts and relative.parts[0].startswith("scene_") else "shared"
        counts = per_scene.setdefault(scene, Counter())
        counts["resources"] += 1
        for record in records:
            try:
                name = Path(record["file"]).stem
                expected_size = (int(record["width"]) * scale, int(record["height"]) * scale)
            except (KeyError, TypeError, ValueError) as error:
                problems.append({"path": str(metadata_path), "problem": f"invalid frame record: {error}"})
                continue
            output = pack / "sprites" / relative / f"{name}@{scale}x.png"
            problem = png_problem(output, expected_size, require_rgba=True)
            frames += 1
            counts["expected"] += 1
            if problem:
                counts["invalid"] += 1
                problems.append({"path": str(output), "problem": problem})
            else:
                counts["valid"] += 1
    return {"resources": resources, "expected": frames, "valid": frames - sum(c["invalid"] for c in per_scene.values()),
            "scenes": {key: dict(value) for key, value in sorted(per_scene.items())}, "problems": problems}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--background-source", type=Path, default=Path("extracted/rosetattoo"))
    parser.add_argument("--sprite-source", type=Path, action="append", default=[],
                        help="Extracted sprite tree; repeat for room, cutscene and shared assets.")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--scenes", type=int, nargs="+", help="Limit room assets and backgrounds to selected scenes; shared sprites remain included.")
    parser.add_argument("--report", type=Path, help="Write the full JSON report to this path.")
    args = parser.parse_args()
    if args.scale < 1:
        parser.error("--scale must be >= 1")
    for path in [args.pack, args.background_source, *args.sprite_source]:
        if not path.is_dir():
            parser.error(f"directory does not exist: {path}")
    scenes = set(args.scenes) if args.scenes else None
    backgrounds = audit_backgrounds(args.background_source, args.pack, args.scale, scenes)
    sprites = {str(source): audit_sprites(source, args.pack, args.scale, scenes) for source in args.sprite_source}
    complete = bool(sprites) and backgrounds["expected"] > 0 and not backgrounds["problems"]
    complete = complete and all(result["resources"] > 0 and not result["problems"] for result in sprites.values())
    report = {"pack": str(args.pack), "scale": args.scale, "complete": complete,
              "backgrounds": backgrounds, "sprite_sources": sprites,
              "scenes": sorted(scenes) if scenes is not None else None,
              "note": "Coverage relative to supplied manifests and scene selection only; extraction completeness, runtime rendering and visual quality require separate validation."}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Backgrounds: {backgrounds['valid']}/{backgrounds['expected']} valid")
    for source, result in sprites.items():
        print(f"{source}: {result['valid']}/{result['expected']} frames valid across {result['resources']} resources")
    print("Coverage complete" if complete else "Coverage incomplete; inspect the JSON report for details")
    if args.report:
        print(f"Report: {args.report}")
    sys.exit(0 if complete else 1)


if __name__ == "__main__":
    main()
