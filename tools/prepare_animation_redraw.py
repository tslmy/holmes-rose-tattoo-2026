#!/usr/bin/env python3
"""Prepare one pose-matched generated frame for the Rose Tattoo runtime pack.

The generator's output may be arbitrarily large. This tool removes its flat
chroma key, scales the actual subject into the matching source-frame envelope,
and writes the scale-qualified PNG consumed by the renderer. It never selects
or interpolates another animation pose.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def bbox(alpha: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(alpha > 127)
    if not len(xs):
        raise ValueError("image contains no opaque pixels")
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True,
                        help="Matching temporal-upscale frame; determines output canvas/envelope.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    generated = rgba(args.generated)
    target = rgba(args.target)
    rgb = generated[..., :3].astype(np.int16)
    keyed = (rgb[..., 1] > 120) & (rgb[..., 1] > rgb[..., 0] + 24) & (rgb[..., 1] > rgb[..., 2] + 24)
    generated[..., 3] = np.where(keyed, 0, generated[..., 3])

    sx0, sy0, sx1, sy1 = bbox(generated[..., 3])
    tx0, ty0, tx1, ty1 = bbox(target[..., 3])
    subject = Image.fromarray(generated[sy0:sy1, sx0:sx1], "RGBA")
    subject = subject.resize((tx1 - tx0, ty1 - ty0), Image.Resampling.LANCZOS)

    output = np.zeros_like(target)
    output[ty0:ty1, tx0:tx1] = np.asarray(subject, dtype=np.uint8)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(output, "RGBA").save(args.output)


if __name__ == "__main__":
    main()
