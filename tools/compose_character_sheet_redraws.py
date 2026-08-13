#!/usr/bin/env python3
"""Seed playable sprite sequences from identity-consistent character sheets.

The generative pass operates on a 5x2 pose sheet so one character identity is
resolved jointly across front, profile, back, and walking views. This tool
fits each generated pose into the original frame's exact opaque bounding box,
then uses the generated subject's chroma-keyed alpha as the visible
silhouette. The result is safe to feed into the temporal residual stabilizer:
generated key poses provide life-like detail, while the original frame
dimensions, offsets, timing, and gameplay geometry remain authoritative.

Reference sheets are intentionally external/generated artifacts. A sheet is
never used as the runtime image itself; only its ten pose cells are extracted
and composited into the original VGS frame sequence.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def sheet_cell(sheet: np.ndarray, index: int) -> np.ndarray:
    rows, columns = 2, 5
    height, width = sheet.shape[:2]
    x0 = index % columns * width // columns
    x1 = (index % columns + 1) * width // columns
    y0 = index // columns * height // rows
    y1 = (index // columns + 1) * height // rows
    return sheet[y0:y1, x0:x1]


def remove_green(cell: np.ndarray) -> np.ndarray:
    rgb = cell[..., :3].astype(np.int16)
    # The source sheets use a flat chroma key. Requiring green to dominate
    # both red and blue avoids removing warm skin highlights or brass.
    green = (rgb[..., 1] > 120) & (rgb[..., 1] > rgb[..., 0] + 10) & (rgb[..., 1] > rgb[..., 2] + 10)
    alpha = np.where(green, 0, 255).astype(np.uint8)
    return np.dstack([cell[..., :3], alpha])


def opaque_bbox(rgba: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(rgba[..., 3] > 127)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def fit_generated_to_target(generated: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Fit generated subject RGB and alpha into the native frame envelope."""
    target_box = opaque_bbox(target)
    source_box = opaque_bbox(generated)
    result = target.copy()
    if not target_box or not source_box:
        return result
    sx0, sy0, sx1, sy1 = source_box
    tx0, ty0, tx1, ty1 = target_box
    crop = Image.fromarray(generated[sy0:sy1, sx0:sx1], "RGBA")
    crop = crop.resize((tx1 - tx0, ty1 - ty0), Image.Resampling.LANCZOS)
    fitted = np.asarray(crop, dtype=np.uint8)
    fitted_alpha = fitted[..., 3] > 127
    target_region = result[ty0:ty1, tx0:tx1]
    target_region[..., :3] = fitted[..., :3]
    target_region[..., 3] = np.where(fitted_alpha, 255, 0).astype(np.uint8)
    result[ty0:ty1, tx0:tx1] = target_region
    result[:ty0, ..., 3] = 0
    result[ty1:, ..., 3] = 0
    result[:, :tx0, 3] = 0
    result[:, tx1:, 3] = 0
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "extracted" / "sprites")
    parser.add_argument("--base-dir", type=Path, default=ROOT / "enhanced" / "sprites-temporal")
    parser.add_argument("--references-dir", type=Path, default=ROOT / "generated" / "character-redraws" / "references")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "enhanced" / "sprites-lifelike-seeded")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--resources", nargs="+", default=None)
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only replace the ten sheet keyframes; by default the nearest sheet pose is fitted into every frame.",
    )
    args = parser.parse_args()

    names = args.resources or sorted(p.stem for p in args.references_dir.glob("*.png"))
    for name in names:
        reference_path = args.references_dir / f"{name}.png"
        source_dir = args.source_dir / name
        base_dir = args.base_dir / name
        if not reference_path.exists() or not (source_dir / "metadata.json").exists() or not base_dir.exists():
            print(f"{name}: missing reference/source/base, skipping")
            continue
        metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
        base_metadata = json.loads((base_dir / "metadata.json").read_text(encoding="utf-8"))
        sheet = load_rgba(reference_path)
        output_dir = args.output_dir / name
        output_dir.mkdir(parents=True, exist_ok=True)
        key_indices = [min(round(i * (len(metadata["frames"]) - 1) / 9), len(metadata["frames"]) - 1) for i in range(10)]
        key_to_cell = dict(zip(key_indices, range(10)))
        key_images: dict[int, np.ndarray] = {}
        for frame_index, cell_index in key_to_cell.items():
            key_images[frame_index] = remove_green(sheet_cell(sheet, cell_index))

        for frame_index, (record, base_record) in enumerate(zip(metadata["frames"], base_metadata["frames"])):
            base_path = base_dir / base_record["file"]
            target_path = source_dir / record["file"]
            target = load_rgba(target_path)
            if not args.seed_only or frame_index in key_images:
                # The target alpha is low-resolution; the base image is the
                # scale-qualified frame that will receive generated RGB.
                base = load_rgba(base_path)
                target_hi = np.asarray(Image.fromarray(target, "RGBA").resize(base.shape[1::-1], Image.Resampling.NEAREST))
                nearest_key = min(key_indices, key=lambda key: abs(key - frame_index))
                fitted = fit_generated_to_target(key_images[nearest_key], target_hi)
                Image.fromarray(fitted, "RGBA").save(output_dir / base_record["file"])
            else:
                shutil.copy2(base_path, output_dir / base_record["file"])

        out_metadata = {
            **base_metadata,
            "upscaler": "identity-consistent-character-sheet-seed",
            "character_sheet_reference": reference_path.name,
            "character_sheet_keyframes": key_indices,
            "character_sheet_frame_policy": "nearest-pose-every-frame" if not args.seed_only else "keyframes-only",
            "alpha_policy": "generated-chroma-keyed-silhouette-with-native-frame-envelope",
        }
        (output_dir / "metadata.json").write_text(json.dumps(out_metadata, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: seeded {len(key_indices)} identity-consistent keyframes")


if __name__ == "__main__":
    main()
