#!/usr/bin/env python3
"""Generate Rose Tattoo background enhancement candidates for review."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from PIL import (
    Image,
    ImageChops,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageStat,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CandidateResult:
    scene_id: int
    scene_name: str
    candidate: str
    prompt: str
    output: str
    scummvm_override: str
    in_game_capture: str | None
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    expected_size: tuple[int, int]
    blank: bool
    rms_delta_from_nearest_source: float


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


def draw_label(img: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, img.width, 20), fill=(255, 255, 255))
    draw.text((5, 4), text, fill=(0, 0, 0))


def resize_source(source: Image.Image, scale: int, resampler: Image.Resampling) -> Image.Image:
    return source.resize((source.width * scale, source.height * scale), resampler)


def candidate_nearest(source: Image.Image, scale: int) -> Image.Image:
    return resize_source(source, scale, Image.Resampling.NEAREST)


def candidate_bicubic(source: Image.Image, scale: int) -> Image.Image:
    return resize_source(source, scale, Image.Resampling.BICUBIC)


def candidate_lanczos(source: Image.Image, scale: int) -> Image.Image:
    return resize_source(source, scale, Image.Resampling.LANCZOS)


def candidate_lanczos_sharp(source: Image.Image, scale: int) -> Image.Image:
    img = candidate_lanczos(source, scale)
    return img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=120, threshold=3))


def candidate_detail(source: Image.Image, scale: int) -> Image.Image:
    img = candidate_lanczos(source, scale)
    img = img.filter(ImageFilter.DETAIL)
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    return ImageEnhance.Contrast(img).enhance(1.04)


BUILTIN_CANDIDATES: dict[str, Callable[[Image.Image, int], Image.Image]] = {
    "nearest": candidate_nearest,
    "bicubic": candidate_bicubic,
    "lanczos": candidate_lanczos,
    "lanczos-sharp": candidate_lanczos_sharp,
    "detail": candidate_detail,
}


def parse_external_candidate(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(
            f"Expected NAME=COMMAND for --external-candidate, got {value!r}"
        )
    name, command = value.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError("--external-candidate name must not be empty")
    if "/" in name or "\\" in name:
        raise ValueError(f"Candidate name must be a folder-safe token: {name!r}")
    if not command.strip():
        raise ValueError(f"--external-candidate command is empty for {name!r}")
    return name, command


def parse_scene_capture_after(values: list[str] | None) -> dict[int, float]:
    overrides: dict[int, float] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"Expected SCENE=SECONDS, got {value!r}")
        scene_text, seconds_text = value.split("=", 1)
        scene_id = int(scene_text)
        seconds = float(seconds_text)
        if scene_id <= 0:
            raise ValueError(f"Scene id must be positive: {scene_id}")
        if seconds < 0:
            raise ValueError(f"Capture delay must be non-negative: {seconds}")
        overrides[scene_id] = seconds
    return overrides


def run_external_candidate(
    command_template: str,
    input_path: Path,
    prompt_path: Path,
    output_path: Path,
    scale: int,
    scene_id: int,
    candidate: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "input": shlex.quote(str(input_path)),
        "prompt": shlex.quote(str(prompt_path)),
        "output": shlex.quote(str(output_path)),
        "scale": str(scale),
        "scene": f"{scene_id:02d}",
        "scene3": f"{scene_id:03d}",
        "candidate": shlex.quote(candidate),
    }
    subprocess.run(command_template.format(**values), shell=True, check=True)


def copy_sidecars(scene_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("prompt.txt", "metadata.json"):
        source = scene_dir / name
        if source.exists():
            shutil.copy2(source, destination / name)


def save_builtin_candidate(
    source: Image.Image,
    scale: int,
    candidate: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    BUILTIN_CANDIDATES[candidate](source, scale).save(output_path)


def validate_candidate(
    scene_id: int,
    scene_name: str,
    candidate: str,
    prompt_path: Path,
    source: Image.Image,
    output_path: Path,
    override_path: Path,
    scale: int,
) -> CandidateResult:
    expected_size = (source.width * scale, source.height * scale)
    nearest = source.resize(expected_size, Image.Resampling.NEAREST)
    with Image.open(output_path).convert("RGB") as output:
        output_size = output.size
        blank = is_blank(output)
        delta = rms_delta(output, nearest)

    if output_size != expected_size:
        raise ValueError(f"{output_path} is {output_size}, expected {expected_size}")
    if blank:
        raise ValueError(f"{output_path} appears blank")

    return CandidateResult(
        scene_id=scene_id,
        scene_name=scene_name,
        candidate=candidate,
        prompt=str(prompt_path),
        output=str(output_path),
        scummvm_override=str(override_path),
        in_game_capture=None,
        input_size=source.size,
        output_size=output_size,
        expected_size=expected_size,
        blank=blank,
        rms_delta_from_nearest_source=round(delta, 3),
    )


def capture_in_game_candidate(
    scene_id: int,
    candidate: str,
    override_root: Path,
    output_path: Path,
    args: argparse.Namespace,
    capture_after: float,
) -> None:
    script_path = ROOT / "tools" / "run_rosetattoo_validation.py"
    cmd = [
        sys.executable,
        str(script_path),
        "--scummvm",
        str(args.scummvm),
        "--data-dir",
        str(args.data_dir),
        "--start-scene",
        str(scene_id),
        "--asset-overrides",
        str(override_root),
        "--hires-scale",
        str(args.scale),
        "--capture-after",
        str(capture_after),
        "--capture-output",
        str(output_path),
        "--capture-mode",
        args.capture_mode,
        "--window-size",
        args.window_size,
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)


def make_scene_review_sheet(
    scene_dir: Path,
    scene_results: list[CandidateResult],
    output_path: Path,
) -> None:
    if not scene_results:
        return

    thumb_w, thumb_h = 320, 240
    cells = [("original", scene_dir / "background.png")]
    for result in scene_results:
        cells.append((result.candidate, Path(result.output)))
        if result.in_game_capture:
            cells.append(
                (f"in-game {result.candidate}", Path(result.in_game_capture))
            )
    cols = min(3, len(cells))
    rows = math.ceil(len(cells) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "white")

    for idx, (label, path) in enumerate(cells):
        with Image.open(path).convert("RGB") as img:
            thumb = ImageOps.contain(img, (thumb_w, thumb_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (thumb_w, thumb_h), "white")
        canvas.paste(thumb, ((thumb_w - thumb.width) // 2, (thumb_h - thumb.height) // 2))
        draw_label(canvas, label)

        x0 = (idx % cols) * thumb_w
        y0 = (idx // cols) * thumb_h
        sheet.paste(canvas, (x0, y0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)


def generate_scene_candidates(
    scene_dir: Path,
    output_root: Path,
    candidates: list[str],
    external_candidates: dict[str, str],
    scale: int,
    capture_args: argparse.Namespace | None,
    scene_capture_after: dict[int, float],
) -> list[CandidateResult]:
    metadata = load_metadata(scene_dir)
    scene_id = int(scene_dir.name.split("_", 1)[1])
    scene_name = metadata.get("scene_name", f"Scene {scene_id:02d}")
    input_path = scene_dir / "background.png"
    prompt_path = scene_dir / "prompt.txt"
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)

    scene_output_dir = output_root / f"scene_{scene_id:03d}"
    copy_sidecars(scene_dir, scene_output_dir)

    results: list[CandidateResult] = []
    with Image.open(input_path).convert("RGB") as source:
        for candidate in candidates:
            candidate_dir = scene_output_dir / "candidates" / candidate
            output_path = candidate_dir / f"background@{scale}x.png"
            override_dir = (
                output_root / "overrides" / candidate / f"scene_{scene_id:03d}"
            )
            override_path = override_dir / f"background@{scale}x.png"

            if candidate in BUILTIN_CANDIDATES:
                save_builtin_candidate(source, scale, candidate, output_path)
            elif candidate in external_candidates:
                run_external_candidate(
                    external_candidates[candidate],
                    input_path,
                    prompt_path,
                    output_path,
                    scale,
                    scene_id,
                    candidate,
                )
            else:
                raise ValueError(f"Unknown candidate: {candidate}")

            result = validate_candidate(
                scene_id,
                scene_name,
                candidate,
                prompt_path,
                source,
                output_path,
                override_path,
                scale,
            )
            override_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, override_path)
            copy_sidecars(scene_dir, override_dir)
            if capture_args is not None:
                capture_after = scene_capture_after.get(
                    scene_id,
                    capture_args.capture_after,
                )
                capture_path = scene_output_dir / "in_game" / f"{candidate}.png"
                capture_in_game_candidate(
                    scene_id,
                    candidate,
                    output_root / "overrides" / candidate,
                    capture_path,
                    capture_args,
                    capture_after,
                )
                result.in_game_capture = str(capture_path)
            results.append(result)

    make_scene_review_sheet(scene_dir, results, scene_output_dir / "review_sheet.jpg")
    (scene_output_dir / "review.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "scene_name": scene_name,
                "selected_candidate": None,
                "notes": "",
                "candidates": [asdict(result) for result in results],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate reviewable Rose Tattoo background enhancement candidates."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "extracted" / "rosetattoo",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "generated" / "candidates",
    )
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument(
        "--candidates",
        nargs="*",
        default=["nearest", "bicubic", "lanczos", "lanczos-sharp", "detail"],
        help=f"Built-in candidates: {', '.join(BUILTIN_CANDIDATES)}.",
    )
    parser.add_argument(
        "--external-candidate",
        action="append",
        default=[],
        metavar="NAME=COMMAND",
        help=(
            "Add an external candidate command. Placeholders: {input}, {prompt}, "
            "{output}, {scale}, {scene}, {scene3}, and {candidate}."
        ),
    )
    parser.add_argument(
        "--capture-scummvm",
        action="store_true",
        help="Capture each candidate in-game through the patched ScummVM validator.",
    )
    parser.add_argument("--scummvm", help="Path to a patched ScummVM executable")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "scummvm")
    parser.add_argument("--capture-after", type=float, default=5)
    parser.add_argument(
        "--scene-capture-after",
        action="append",
        metavar="SCENE=SECONDS",
        help="Override candidate in-game capture delay for a specific scene.",
    )
    parser.add_argument(
        "--capture-mode",
        choices=["window", "screen"],
        default="window",
    )
    parser.add_argument("--window-size", default="1280,960")
    args = parser.parse_args()

    if args.scale < 1:
        raise ValueError("--scale must be >= 1")
    if args.capture_scummvm and not args.scummvm:
        raise ValueError("--scummvm is required with --capture-scummvm")

    external_candidates = dict(
        parse_external_candidate(value) for value in args.external_candidate
    )
    candidates = list(dict.fromkeys(args.candidates + list(external_candidates)))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scene_capture_after = parse_scene_capture_after(args.scene_capture_after)

    all_results: list[CandidateResult] = []
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        results = generate_scene_candidates(
            scene_dir,
            args.output_dir,
            candidates,
            external_candidates,
            args.scale,
            args if args.capture_scummvm else None,
            scene_capture_after,
        )
        all_results.extend(results)
        scene_id = int(scene_dir.name.split("_", 1)[1])
        print(
            f"scene {scene_id:03d}: generated {len(results)} candidates "
            f"-> {args.output_dir / f'scene_{scene_id:03d}' / 'review_sheet.jpg'}"
        )

    manifest = {
        "input_dir": str(args.input_dir),
        "output_dir": str(args.output_dir),
        "scale": args.scale,
        "candidates": candidates,
        "scene_count": len({result.scene_id for result in all_results}),
        "candidate_count": len(all_results),
        "results": [asdict(result) for result in all_results],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
