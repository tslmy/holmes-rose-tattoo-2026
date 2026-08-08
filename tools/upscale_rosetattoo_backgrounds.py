#!/usr/bin/env python3
"""Upscale or externally enhance extracted Rose Tattoo backgrounds."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat


RESAMPLERS = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


@dataclass
class SceneResult:
    scene_id: int
    scene_name: str
    input: str
    prompt: str
    output: str
    method: str
    scale: int
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    expected_size: tuple[int, int]
    size_ok: bool
    blank: bool
    rms_delta_from_scaled_source: float


def scene_dirs(root: Path, selected: list[int] | None) -> list[Path]:
    if selected:
        return [root / f"scene_{scene_id:02d}" for scene_id in selected]
    return sorted(p for p in root.glob("scene_*") if p.is_dir())


def load_metadata(scene_dir: Path) -> dict:
    metadata_path = scene_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def is_blank(img: Image.Image) -> bool:
    stat = ImageStat.Stat(img.convert("RGB"))
    return max(stat.stddev) < 0.5


def rms_delta(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return math.sqrt(sum(v * v for v in stat.rms) / len(stat.rms))


def upscale_with_pillow(input_path: Path, output_path: Path, scale: int, method: str) -> None:
    with Image.open(input_path).convert("RGB") as img:
        output = img.resize((img.width * scale, img.height * scale), RESAMPLERS[method])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output.save(output_path)


def run_external_command(
    command_template: str,
    input_path: Path,
    prompt_path: Path,
    output_path: Path,
    scale: int,
    scene_id: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "input": shlex.quote(str(input_path)),
        "prompt": shlex.quote(str(prompt_path)),
        "output": shlex.quote(str(output_path)),
        "scale": str(scale),
        "scene": f"{scene_id:02d}",
    }
    command = command_template.format(**values)
    subprocess.run(command, shell=True, check=True)


def validate_output(
    scene_id: int,
    scene_name: str,
    input_path: Path,
    prompt_path: Path,
    output_path: Path,
    method: str,
    scale: int,
    allow_size_mismatch: bool,
) -> SceneResult:
    with Image.open(input_path).convert("RGB") as source:
        input_size = source.size
        expected_size = (source.width * scale, source.height * scale)
        scaled_source = source.resize(expected_size, Image.Resampling.NEAREST)
        with Image.open(output_path).convert("RGB") as output:
            output_size = output.size
            size_ok = output_size == expected_size or allow_size_mismatch
            blank = is_blank(output)
            delta = rms_delta(output, scaled_source)

    if not size_ok:
        raise ValueError(
            f"{output_path} is {output_size}, expected {expected_size}. "
            "Pass --allow-size-mismatch for freeform external enhancers."
        )
    if blank:
        raise ValueError(f"{output_path} appears blank")

    return SceneResult(
        scene_id=scene_id,
        scene_name=scene_name,
        input=str(input_path),
        prompt=str(prompt_path),
        output=str(output_path),
        method=method,
        scale=scale,
        input_size=input_size,
        output_size=output_size,
        expected_size=expected_size,
        size_ok=size_ok,
        blank=blank,
        rms_delta_from_scaled_source=round(delta, 3),
    )


def make_contact_sheet(results: Iterable[SceneResult], output_path: Path) -> None:
    results = list(results)
    if not results:
        return

    thumb_w, thumb_h, label_h = 220, 140, 30
    cols = min(4, len(results))
    rows = math.ceil(len(results) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "white")

    for idx, result in enumerate(results):
        with Image.open(result.output).convert("RGB") as img:
            thumb = ImageOps.contain(img, (thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = (idx % cols) * thumb_w + (thumb_w - thumb.width) // 2
        y = (idx // cols) * (thumb_h + label_h)
        sheet.paste(thumb, (x, y))

        draw = ImageDraw.Draw(sheet)
        label = f"{result.scene_id:02d} {result.scene_name[:22]}"
        draw.text(((idx % cols) * thumb_w + 4, y + thumb_h + 4), label, fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)


def process_scene(
    scene_dir: Path,
    input_root: Path,
    output_root: Path,
    method: str,
    scale: int,
    external_command: str | None,
    allow_size_mismatch: bool,
) -> SceneResult:
    metadata = load_metadata(scene_dir)
    scene_id = int(scene_dir.name.split("_", 1)[1])
    scene_name = metadata.get("scene_name", f"Scene {scene_id:02d}")
    input_path = scene_dir / "background.png"
    prompt_path = scene_dir / "prompt.txt"

    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)

    relative_scene = scene_dir.relative_to(input_root)
    output_path = output_root / relative_scene / f"background_{scale}x_{method}.png"

    if method == "external":
        if not external_command:
            raise ValueError("--external-command is required when --method external")
        run_external_command(external_command, input_path, prompt_path, output_path, scale, scene_id)
    else:
        upscale_with_pillow(input_path, output_path, scale, method)

    return validate_output(
        scene_id,
        scene_name,
        input_path,
        prompt_path,
        output_path,
        method,
        scale,
        allow_size_mismatch,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upscale or externally enhance extracted Rose Tattoo backgrounds."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("extracted/rosetattoo"))
    parser.add_argument("--output-dir", type=Path, default=Path("enhanced/rosetattoo"))
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument(
        "--method",
        choices=sorted([*RESAMPLERS.keys(), "external"]),
        default="lanczos",
    )
    parser.add_argument(
        "--external-command",
        help=(
            "Shell command template for external enhancers. Placeholders are "
            "{input}, {prompt}, {output}, {scale}, and {scene}; file placeholders "
            "are shell-quoted automatically."
        ),
    )
    parser.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Do not fail if an external enhancer returns a non-scale-multiple size.",
    )
    args = parser.parse_args()

    if args.scale < 1:
        raise ValueError("--scale must be >= 1")

    results: list[SceneResult] = []
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        result = process_scene(
            scene_dir,
            args.input_dir,
            args.output_dir,
            args.method,
            args.scale,
            args.external_command,
            args.allow_size_mismatch,
        )
        results.append(result)
        print(
            f"scene {result.scene_id:02d}: {result.scene_name} "
            f"{result.input_size[0]}x{result.input_size[1]} -> "
            f"{result.output_size[0]}x{result.output_size[1]} "
            f"delta={result.rms_delta_from_scaled_source}"
        )

    report = {
        "input_dir": str(args.input_dir),
        "output_dir": str(args.output_dir),
        "method": args.method,
        "scale": args.scale,
        "scene_count": len(results),
        "results": [asdict(result) for result in results],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    make_contact_sheet(results, args.output_dir / "contact_sheet.jpg")


if __name__ == "__main__":
    main()
