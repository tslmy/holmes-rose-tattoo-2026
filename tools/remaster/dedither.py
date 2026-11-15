"""Deterministic de-dither for 256-colour art.

Ordered and error-diffusion dither alternate neighbouring pixels between
nearby palette entries. Super-resolution and diffusion models both read that
pattern as texture and amplify it. This pass averages each pixel with the
neighbours whose colour is within `threshold` (Euclidean RGB distance), with
a Gaussian spatial weight and a tent range weight: a range-limited bilateral
filter. Colour steps larger than the threshold (real edges, outlines, text)
are left alone; transparent pixels neither contribute nor change.

It is pure NumPy, uses no randomness and no platform-dependent reductions
beyond float32 sums in a fixed order, so the same input gives the same bytes.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def _shift(array: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """array shifted so out[y, x] = array[y+dy, x+dx], edges replicated."""
    h, w = array.shape[:2]
    ys = np.clip(np.arange(h) + dy, 0, h - 1)
    xs = np.clip(np.arange(w) + dx, 0, w - 1)
    return array[ys][:, xs]


def dedither_array(rgb: np.ndarray, alpha: np.ndarray | None = None, *, radius: int = 1,
                   threshold: float = 48.0, iterations: int = 2) -> np.ndarray:
    """Return a de-dithered uint8 RGB array of the same shape."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("expected HxWx3 uint8 RGB")
    valid = np.ones(rgb.shape[:2], dtype=bool) if alpha is None else alpha > 0
    image = rgb.astype(np.float32)
    t2 = np.float32(threshold) ** 2
    sigma2 = np.float32(max(radius, 1)) ** 2
    offsets = [(dy, dx) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)]
    for _ in range(iterations):
        acc = np.zeros_like(image)
        wsum = np.zeros(rgb.shape[:2], dtype=np.float32)
        for dy, dx in offsets:
            neighbour = _shift(image, dy, dx)
            neighbour_valid = _shift(valid, dy, dx)
            d2 = np.sum((neighbour - image) ** 2, axis=2)
            spatial = np.float32(np.exp(-(dy * dy + dx * dx) / (2 * sigma2)))
            weight = spatial * np.clip(1 - np.sqrt(d2 / t2), 0, 1) * neighbour_valid
            acc += weight[..., None] * neighbour
            wsum += weight
        smoothed = acc / np.maximum(wsum, 1e-6)[..., None]
        image = np.where(valid[..., None], smoothed, image)
    out = np.clip(np.rint(image), 0, 255).astype(np.uint8)
    out[~valid] = rgb[~valid]
    return out


def dedither_image(image: Image.Image, **kwargs) -> Image.Image:
    """De-dither a PIL image, preserving its alpha channel exactly."""
    if image.mode == "RGBA":
        array = np.asarray(image)
        rgb = dedither_array(np.ascontiguousarray(array[..., :3]), array[..., 3], **kwargs)
        return Image.fromarray(np.dstack([rgb, array[..., 3]]), "RGBA")
    rgb = np.asarray(image.convert("RGB"))
    return Image.fromarray(dedither_array(np.ascontiguousarray(rgb), **kwargs), "RGB")


def dither_energy(image: Image.Image) -> float:
    """Mean |2x2 checkerboard response| of luma on opaque pixels: a dither gauge.

    High for pixel-alternating patterns, near zero for smooth gradients and
    single straight edges. Used for reporting, not as a gate.
    """
    array = np.asarray(image.convert("RGBA")).astype(np.float32)
    luma = array[..., 0] * 0.299 + array[..., 1] * 0.587 + array[..., 2] * 0.114
    opaque = array[..., 3] > 0
    response = np.abs(luma[:-1, :-1] - luma[:-1, 1:] - luma[1:, :-1] + luma[1:, 1:]) / 4
    mask = opaque[:-1, :-1] & opaque[:-1, 1:] & opaque[1:, :-1] & opaque[1:, 1:]
    return float(response[mask].mean()) if mask.any() else 0.0


def edge_map(image: Image.Image, low: float = 40, high: float = 120) -> Image.Image:
    """Structure/edge control image: Sobel magnitude with hysteresis.

    Deterministic stand-in for Canny that needs no OpenCV; white edges on black.
    """
    luma = np.asarray(image.convert("L")).astype(np.float32)
    gx = (_shift(luma, -1, 1) + 2 * _shift(luma, 0, 1) + _shift(luma, 1, 1)
          - _shift(luma, -1, -1) - 2 * _shift(luma, 0, -1) - _shift(luma, 1, -1))
    gy = (_shift(luma, 1, -1) + 2 * _shift(luma, 1, 0) + _shift(luma, 1, 1)
          - _shift(luma, -1, -1) - 2 * _shift(luma, -1, 0) - _shift(luma, -1, 1))
    magnitude = np.hypot(gx, gy)
    strong = magnitude >= high
    weak = magnitude >= low
    edges = strong.copy()
    for _ in range(8):  # grow strong edges along weak ones (bounded hysteresis)
        grown = edges.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= _shift(edges, dy, dx)
        grown &= weak
        if np.array_equal(grown, edges):
            break
        edges = grown
    return Image.fromarray((edges * 255).astype(np.uint8), "L")
