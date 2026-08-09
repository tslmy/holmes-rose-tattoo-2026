#!/usr/bin/env python3
"""Capture multiple Rose Tattoo scenes through the patched ScummVM renderer."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, ImageStat


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = [1, 2, 18, 20, 36, 42, 53, 91]


def screenshot_path(
    output_dir: Path,
    scene_id: int,
    scale: int | None,
    debug_mode: str | None,
) -> Path:
    suffix = f"-{scale}x" if scale else ""
    if debug_mode:
        suffix += f"-{debug_mode}"
    return output_dir / f"scene-{scene_id:03d}{suffix}.png"


def parse_scene_capture_after(values: list[str] | None) -> dict[int, float]:
    overrides: dict[int, float] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(
                f"Expected SCENE=SECONDS for --scene-capture-after, got {value!r}"
            )
        scene_text, seconds_text = value.split("=", 1)
        scene_id = int(scene_text)
        seconds = float(seconds_text)
        if scene_id <= 0:
            raise ValueError(f"Scene id must be positive: {scene_id}")
        if seconds < 0:
            raise ValueError(f"Capture delay must be non-negative: {seconds}")
        overrides[scene_id] = seconds
    return overrides


def run_scene_capture(
    scene_id: int,
    args: argparse.Namespace,
    script_path: Path,
    capture_after: float,
) -> Path:
    output = screenshot_path(args.output_dir, scene_id, args.hires_scale, args.hires_debug)
    cmd = [
        sys.executable,
        str(script_path),
        "--start-scene",
        str(scene_id),
        "--capture-after",
        str(capture_after),
        "--capture-output",
        str(output),
        "--capture-mode",
        args.capture_mode,
        "--window-size",
        args.window_size,
    ]
    if args.scummvm:
        cmd.extend(["--scummvm", args.scummvm])
    if args.data_dir:
        cmd.extend(["--data-dir", str(args.data_dir)])
    if args.save_dir:
        cmd.extend(["--save-dir", str(args.save_dir)])
    if args.asset_overrides:
        cmd.extend(["--asset-overrides", str(args.asset_overrides)])
    if args.hires_scale:
        cmd.extend(["--hires-scale", str(args.hires_scale)])
    if args.hires_debug:
        cmd.extend(["--hires-debug", args.hires_debug])
    if args.hires_format:
        cmd.extend(["--hires-format", args.hires_format])
    if args.fullscreen:
        cmd.append("--fullscreen")

    subprocess.run(cmd, check=True)
    return output


def is_blank_capture(path: Path) -> bool:
    with Image.open(path) as source:
        image = source.convert("RGB")
        stat = ImageStat.Stat(image)
    return max(stat.stddev) < 0.5


def make_contact_sheet(captures: list[tuple[int, Path]], output_path: Path) -> None:
    if not captures:
        return

    thumb_w, thumb_h, label_h = 360, 270, 28
    cols = min(3, len(captures))
    rows = math.ceil(len(captures) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)

    for idx, (scene_id, capture) in enumerate(captures):
        x0 = (idx % cols) * thumb_w
        y0 = (idx // cols) * (thumb_h + label_h)
        with Image.open(capture).convert("RGB") as img:
            thumb = ImageOps.contain(img, (thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x0 + (thumb_w - thumb.width) // 2, y0))
        draw.text((x0 + 4, y0 + thumb_h + 5), f"scene {scene_id:03d}", fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-capture Rose Tattoo scenes through tools/run_rosetattoo_validation.py."
    )
    parser.add_argument("--scummvm", help="Path to a patched ScummVM executable")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "scummvm")
    parser.add_argument("--save-dir", type=Path, default=ROOT / "validation" / "saves")
    parser.add_argument("--asset-overrides", type=Path)
    parser.add_argument("--hires-scale", type=int)
    parser.add_argument(
        "--hires-debug",
        choices=["composite", "background", "mask", "native"],
    )
    parser.add_argument(
        "--hires-format",
        choices=["clut8", "rgb565", "rgba32"],
    )
    parser.add_argument("--scenes", type=int, nargs="*", default=DEFAULT_SCENES)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "validation" / "screenshots" / "batch",
    )
    parser.add_argument("--capture-after", type=float, default=5)
    parser.add_argument(
        "--scene-capture-after",
        action="append",
        metavar="SCENE=SECONDS",
        help=(
            "Override the capture delay for a specific scene. May be repeated, "
            "for example --scene-capture-after 1=8."
        ),
    )
    parser.add_argument(
        "--capture-mode",
        choices=["window", "screen"],
        default="window",
    )
    parser.add_argument("--window-size", default="1280,960")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first scene that fails to capture.",
    )
    parser.add_argument(
        "--contact-sheet",
        type=Path,
        help="Defaults to <output-dir>/contact-sheet.jpg.",
    )
    args = parser.parse_args()

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    script_path = ROOT / "tools" / "run_rosetattoo_validation.py"
    scene_capture_after = parse_scene_capture_after(args.scene_capture_after)

    captures: list[tuple[int, Path]] = []
    failures: list[dict[str, str | int]] = []
    for scene_id in args.scenes:
        capture_after = scene_capture_after.get(scene_id, args.capture_after)
        try:
            capture = run_scene_capture(scene_id, args, script_path, capture_after)
        except subprocess.CalledProcessError as err:
            failure = {
                "scene_id": scene_id,
                "returncode": err.returncode,
                "command": " ".join(str(part) for part in err.cmd),
            }
            failures.append(failure)
            print(
                f"failed scene {scene_id:03d}: "
                f"capture_after={capture_after} returncode={err.returncode}"
            )
            if args.fail_fast:
                raise
        else:
            if is_blank_capture(capture):
                failure = {
                    "scene_id": scene_id,
                    "returncode": 0,
                    "command": " ".join(str(part) for part in [capture]),
                    "error": "blank capture",
                }
                failures.append(failure)
                print(
                    f"failed scene {scene_id:03d}: "
                    f"capture_after={capture_after} blank capture {capture}"
                )
                if args.fail_fast:
                    raise SystemExit(1)
            else:
                captures.append((scene_id, capture))
                print(f"captured scene {scene_id:03d}: capture_after={capture_after} {capture}")

    contact_sheet = args.contact_sheet or args.output_dir / "contact-sheet.jpg"
    make_contact_sheet(captures, contact_sheet)
    print(f"contact sheet: {contact_sheet}")

    report = {
        "scummvm": args.scummvm,
        "data_dir": str(args.data_dir),
        "asset_overrides": str(args.asset_overrides) if args.asset_overrides else None,
        "hires_scale": args.hires_scale,
        "hires_debug": args.hires_debug,
        "hires_format": args.hires_format,
        "capture_after": args.capture_after,
        "scene_capture_after": {
            str(scene_id): seconds for scene_id, seconds in sorted(scene_capture_after.items())
        },
        "captures": [
            {"scene_id": scene_id, "path": str(path)}
            for scene_id, path in captures
        ],
        "failures": failures,
        "contact_sheet": str(contact_sheet),
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
