#!/usr/bin/env python3
"""Build a playable high-resolution Rose Tattoo background override pack."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_command(cmd: list[str]) -> None:
    print("+ " + " ".join(str(part) for part in cmd))
    subprocess.run(cmd, check=True)


def maybe_extract_assets(args: argparse.Namespace) -> None:
    if args.skip_extract:
        return

    background_count = len(list(args.input_dir.glob("scene_*/background.png")))
    if background_count and not args.force_extract:
        print(f"using existing extracted backgrounds: {background_count} scenes")
        return

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "extract_rosetattoo_assets.py"),
        "--data-dir",
        str(args.data_dir),
        "--output-dir",
        str(args.input_dir),
    ]
    if args.scenes:
        cmd.extend(["--scenes", *(str(scene_id) for scene_id in args.scenes)])
    run_command(cmd)


def build_overrides(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "upscale_rosetattoo_backgrounds.py"),
        "--input-dir",
        str(args.input_dir),
        "--output-dir",
        str(args.enhanced_dir),
        "--scale",
        str(args.scale),
        "--method",
        args.method,
        "--scummvm-overrides",
        str(args.mod_dir),
    ]
    if args.scenes:
        cmd.extend(["--scenes", *(str(scene_id) for scene_id in args.scenes)])
    if args.external_command:
        cmd.extend(["--external-command", args.external_command])
    if args.allow_size_mismatch:
        cmd.append("--allow-size-mismatch")
    run_command(cmd)


def validate_overrides(args: argparse.Namespace) -> None:
    if not args.validate:
        return

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "batch_validate_rosetattoo.py"),
        "--scummvm",
        str(args.scummvm),
        "--data-dir",
        str(args.data_dir),
        "--asset-overrides",
        str(args.mod_dir),
        "--hires-scale",
        str(args.scale),
        "--hires-format",
        args.hires_format,
        "--capture-after",
        str(args.capture_after),
        "--output-dir",
        str(args.validation_dir),
        "--window-size",
        args.window_size,
    ]
    if args.validation_scenes:
        cmd.extend(
            ["--scenes", *(str(scene_id) for scene_id in args.validation_scenes)]
        )
    for override in args.scene_capture_after or []:
        cmd.extend(["--scene-capture-after", override])
    run_command(cmd)


def write_manifest(args: argparse.Namespace) -> None:
    report_path = args.enhanced_dir / "report.json"
    upscale_report = {}
    if report_path.exists():
        upscale_report = json.loads(report_path.read_text(encoding="utf-8"))

    scene_count = len(list(args.mod_dir.glob("scene_*/background@*x.png")))
    play_parts = [
        f"SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES={args.mod_dir}",
        f"SCUMMVM_SHERLOCK_TATTOO_HIRES_SCALE={args.scale}",
        f"SCUMMVM_SHERLOCK_TATTOO_HIRES_FORMAT={args.hires_format}",
        str(args.scummvm),
        f"--path={args.data_dir}",
        "--aspect-ratio",
        "--stretch-mode=pixel-perfect",
        "--no-fullscreen",
        f"--window-size={args.window_size}",
        "sherlock:rosetattoo",
    ]
    manifest = {
        "name": "Rose Tattoo high-resolution background override pack",
        "scale": args.scale,
        "hires_format": args.hires_format,
        "method": args.method,
        "data_dir": str(args.data_dir),
        "input_dir": str(args.input_dir),
        "enhanced_dir": str(args.enhanced_dir),
        "mod_dir": str(args.mod_dir),
        "scene_count": scene_count,
        "upscale_report": str(report_path) if report_path.exists() else None,
        "validation_dir": str(args.validation_dir) if args.validate else None,
        "play_command": " ".join(shlex.quote(part) for part in play_parts),
        "upscale_summary": {
            "scene_count": upscale_report.get("scene_count"),
            "method": upscale_report.get("method"),
            "scale": upscale_report.get("scale"),
        },
    }
    args.mod_dir.mkdir(parents=True, exist_ok=True)
    (args.mod_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"playable override pack: {args.mod_dir}")
    print(f"manifest: {args.mod_dir / 'manifest.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a full ScummVM-ready high-resolution background override pack "
            "for The Case of the Rose Tattoo."
        )
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "scummvm")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "extracted" / "rosetattoo",
    )
    parser.add_argument(
        "--enhanced-dir",
        type=Path,
        default=ROOT / "enhanced" / "playable-hires-backgrounds",
    )
    parser.add_argument(
        "--mod-dir",
        type=Path,
        default=ROOT / "mods" / "hires-backgrounds",
    )
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument(
        "--hires-format",
        choices=["clut8", "rgb565", "rgba32"],
        default="rgba32",
        help="Runtime pixel format for the patched high-resolution ScummVM compositor.",
    )
    parser.add_argument(
        "--method",
        choices=["nearest", "bilinear", "bicubic", "lanczos", "external"],
        default="lanczos",
    )
    parser.add_argument("--external-command")
    parser.add_argument("--allow-size-mismatch", action="store_true")
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Require existing extracted backgrounds instead of extracting missing assets.",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Re-run extraction even if extracted backgrounds already exist.",
    )
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--scummvm", default=str(ROOT / "scummvm-src" / "scummvm"))
    parser.add_argument(
        "--validation-scenes",
        type=int,
        nargs="*",
        default=[1, 18, 36, 53],
    )
    parser.add_argument(
        "--validation-dir",
        type=Path,
        default=ROOT / "validation" / "screenshots" / "playable-hires",
    )
    parser.add_argument("--capture-after", type=float, default=4)
    parser.add_argument("--scene-capture-after", action="append")
    parser.add_argument("--window-size", default="1280,960")
    args = parser.parse_args()

    if args.scale < 1:
        raise ValueError("--scale must be >= 1")
    if args.method == "external" and not args.external_command:
        raise ValueError("--external-command is required with --method external")

    maybe_extract_assets(args)
    build_overrides(args)
    validate_overrides(args)
    write_manifest(args)


if __name__ == "__main__":
    main()
