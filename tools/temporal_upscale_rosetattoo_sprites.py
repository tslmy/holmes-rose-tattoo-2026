#!/usr/bin/env python3
"""Make ESRGAN sprite sequences temporally consistent.

The regular sprite upscaler is deliberately frame-local.  That is a good
property for cursors and icons, but a GAN can choose slightly different
high-frequency texture for two frames of the same walk cycle.  This pass
keeps each frame's original pixels and alpha mask as the authority, while
stabilising only the ESRGAN detail residual using neighbouring frames.

This is an animation-native, offline approximation of the propagation /
alignment / aggregation pattern used by video super-resolution models.  It
does not invent or insert frames, change VGS frame indices, or alter timing.
For each target frame it:

1. builds a deterministic Lanczos base and an ESRGAN detail residual;
2. aligns neighbouring frames with a small source-resolution translation
   search (the VGS offsets provide the common sprite coordinate system);
3. aggregates residuals only where the neighbour agrees in source colour and
   opacity, so moving limbs and changing silhouettes are never smeared; and
4. restores the target frame's exact binary alpha mask.

The result is intentionally conservative: a slightly less "creative" detail
pass is preferable to a flickering hand, face, or coat edge in a game sprite.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def rgba_array(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.float32)


def resize_rgba(array: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGBA")
    return np.asarray(image.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)


def shift_array(array: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate an HxWxC array, filling uncovered pixels with zero."""
    height, width = array.shape[:2]
    result = np.zeros_like(array)
    src_x0, src_x1 = max(0, -dx), min(width, width - dx)
    src_y0, src_y1 = max(0, -dy), min(height, height - dy)
    dst_x0, dst_x1 = max(0, dx), min(width, width + dx)
    dst_y0, dst_y1 = max(0, dy), min(height, height + dy)
    if src_x1 > src_x0 and src_y1 > src_y0:
        result[dst_y0:dst_y1, dst_x0:dst_x1] = array[src_y0:src_y1, src_x0:src_x1]
    return result


def alignment_shift(target: np.ndarray, neighbour: np.ndarray, radius: int) -> tuple[int, int]:
    """Find a conservative integer source-pixel translation for a neighbour."""
    target_alpha = target[..., 3] > 127
    target_luma = np.dot(target[..., :3], [0.299, 0.587, 0.114])
    best = (0, 0)
    best_score = float("inf")
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            shifted = shift_array(neighbour, dx, dy)
            valid = target_alpha & (shifted[..., 3] > 127)
            if valid.sum() < 4:
                continue
            neighbour_luma = np.dot(shifted[..., :3], [0.299, 0.587, 0.114])
            # Robust trimmed error: silhouette changes and transparent borders
            # should not dominate the translation estimate.
            error = np.abs(target_luma[valid] - neighbour_luma[valid])
            score = float(np.percentile(error, 60)) + (1.0 - min(valid.mean() * 2, 1.0)) * 20
            if score < best_score:
                best_score, best = score, (dx, dy)
    return best


def stabilize_sequence(
    source_dir: Path,
    input_dir: Path,
    output_dir: Path,
    scale: int,
    radius: int = 2,
    blend: float = 0.35,
) -> dict:
    metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
    records = metadata["frames"]
    source = [rgba_array(source_dir / record["file"]) for record in records]
    enhanced = [rgba_array(input_dir / f"{Path(record['file']).stem}@{scale}x.png") for record in records]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_records = []
    for index, (record, lowres, detail_frame) in enumerate(zip(records, source, enhanced)):
        height, width = lowres.shape[:2]
        target_size = (width * scale, height * scale)
        base = resize_rgba(lowres, target_size)
        # Residuals are where ESRGAN is allowed to contribute.  Luma/chroma
        # and alpha are treated separately so this cannot change geometry.
        target_rgb = detail_frame[..., :3]
        target_residual = target_rgb - base[..., :3]
        candidates = [target_residual]

        for neighbour_index in range(max(0, index - radius), min(len(records), index + radius + 1)):
            if neighbour_index == index:
                continue
            neighbour_lowres = source[neighbour_index]
            # Compare in the target's local frame space.  This handles the
            # per-frame VGS bounding boxes without resizing a pose into another.
            if neighbour_lowres.shape[:2] != lowres.shape[:2]:
                continue
            dx, dy = alignment_shift(lowres, neighbour_lowres, radius=2)
            shifted_lowres = shift_array(neighbour_lowres, dx, dy)
            shifted_detail = shift_array(enhanced[neighbour_index], dx * scale, dy * scale)
            shifted_base = resize_rgba(shifted_lowres, target_size)
            shifted_residual = shifted_detail[..., :3] - shifted_base[..., :3]
            source_distance = np.abs(lowres[..., :3] - shifted_lowres[..., :3]).max(axis=2)
            source_distance = Image.fromarray(np.clip(source_distance, 0, 255).astype(np.uint8), "L").resize(target_size, Image.Resampling.NEAREST)
            source_distance = np.asarray(source_distance, dtype=np.float32)
            valid = (
                (detail_frame[..., 3] > 127)
                & (shifted_detail[..., 3] > 127)
                & (source_distance < 28)
            )
            # Fade rather than hard-switch at the agreement boundary.
            if valid.any():
                candidate = target_residual.copy()
                candidate[valid] = shifted_residual[valid]
                candidates.append(candidate)

        if len(candidates) > 1:
            aggregate = np.median(np.stack(candidates, axis=0), axis=0)
            stable = np.median(np.stack([candidates[0], aggregate], axis=0), axis=0)
            output_rgb = target_rgb + (stable - target_residual) * blend
        else:
            output_rgb = target_rgb
        output = np.dstack([np.clip(output_rgb, 0, 255), detail_frame[..., 3]])
        output_path = output_dir / f"{Path(record['file']).stem}@{scale}x.png"
        Image.fromarray(np.rint(output).astype(np.uint8), "RGBA").save(output_path)
        output_records.append({
            **record,
            "file": output_path.name,
            "width": width * scale,
            "height": height * scale,
            "offset_x": record["offset_x"] * scale,
            "offset_y": record["offset_y"] * scale,
        })

    out_metadata = {
        **metadata,
        "scale": scale,
        "upscaler": "temporal-esrgan",
        "temporal_consistency": {
            "method": "aligned-neighbour-residual-median",
            "neighbour_radius": radius,
            "blend": blend,
            "alpha_policy": "target-binary-mask",
        },
        "frames": output_records,
    }
    (output_dir / "metadata.json").write_text(json.dumps(out_metadata, indent=2) + "\n", encoding="utf-8")
    return out_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "enhanced" / "sprites")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "extracted" / "sprites")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "enhanced" / "sprites-temporal")
    parser.add_argument("--resources", nargs="+", default=None)
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--radius", type=int, default=2, help="Neighbour frames on each side.")
    parser.add_argument("--blend", type=float, default=0.35, help="How much stable detail to restore.")
    args = parser.parse_args()

    resource_names = args.resources or sorted(
        d.name for d in args.source_dir.iterdir() if d.is_dir() and (d / "metadata.json").exists()
    )
    for name in resource_names:
        source_dir = args.source_dir / name
        input_dir = args.input_dir / name
        if not (source_dir / "metadata.json").exists() or not input_dir.exists():
            print(f"{name}: missing source or ESRGAN output, skipping")
            continue
        metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
        missing = [
            record["file"]
            for record in metadata["frames"]
            if not (input_dir / f"{Path(record['file']).stem}@{args.scale}x.png").exists()
        ]
        if missing:
            print(f"{name}: no complete @{args.scale}x input sequence, skipping ({len(missing)} missing)")
            continue
        if len(metadata["frames"]) == 1:
            # Single-frame assets have no temporal problem; preserve the
            # existing ESRGAN result and metadata in the new tree.
            out = args.output_dir / name
            out.mkdir(parents=True, exist_ok=True)
            for path in input_dir.glob("*@" + str(args.scale) + "x.png"):
                Image.open(path).save(out / path.name)
            (out / "metadata.json").write_text((input_dir / "metadata.json").read_text(), encoding="utf-8")
            print(f"{name}: copied single frame")
            continue
        result = stabilize_sequence(source_dir, input_dir, args.output_dir / name, args.scale, args.radius, args.blend)
        print(f"{name}: temporally stabilised {len(result['frames'])} frames")


if __name__ == "__main__":
    main()
