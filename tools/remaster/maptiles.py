"""Tiling and seam blending for the map redraw.

Splits a large image into overlapping tiles for per-tile redraw, and blends the
redrawn tiles back into a single image using linear weight ramps around the
interior edges (see tests/test_remaster_maptiles.py for the full spec).

NumPy only; no other dependencies.
"""

import numpy as np


def tile_boxes(width: int, height: int, cols: int, rows: int, overlap: int):
    """Return (x0, y0, x1, y1) boxes for a cols x rows grid of overlapping tiles.

    Core edges sit at round(i * width / cols) (x) and round(j * height / rows) (y).
    Each tile spans its core extended by `overlap` on every side that is not an
    image border, clamped to [0, width] / [0, height]. Row-major order: the row
    index j is outer, the column index i is inner.
    """
    xs = [round(i * width / cols) for i in range(cols + 1)]
    ys = [round(j * height / rows) for j in range(rows + 1)]
    boxes = []
    for j in range(rows):
        for i in range(cols):
            x0 = max(0, xs[i] - overlap)
            x1 = min(width, xs[i + 1] + overlap)
            y0 = max(0, ys[j] - overlap)
            y1 = min(height, ys[j + 1] + overlap)
            boxes.append((x0, y0, x1, y1))
    return boxes


def blend_tiles(tiles, boxes, size):
    """Blend tiles (each the size of boxes[i]) into a (height, width, 3) uint8 image.

    Each pixel is the weighted average of the tiles covering it. A tile's weight
    is the outer product of a horizontal and a vertical weight. Each is 1 except
    inside the overlap band next to an interior edge, where it ramps linearly
    from 1 (at the neighbouring tile's edge) to 0 (at the tile's own border).
    `overlap` is recovered from the boxes; image-border edges are not ramped.
    """
    width, height = size
    sum_w = np.zeros((height, width, 3), dtype=np.float64)
    sum_a = np.zeros((height, width), dtype=np.float64)
    for k, (tile, (x0, y0, x1, y1)) in enumerate(zip(tiles, boxes)):
        tile = np.asarray(tile)
        if tile.ndim == 2:
            tile = np.stack([tile] * 3, axis=-1)
        h, w = y1 - y0, x1 - x0
        # --- horizontal ramps (left/right) ---
        xs = np.arange(x0, x1, dtype=np.float64)
        if x0 > 0:
            # tile immediately to the left: same y0, largest x1 that is > x0
            left_x1 = max(boxes[m][2] for m in range(len(boxes))
                          if m != k and boxes[m][1] == y0 and boxes[m][3] == y1
                          and boxes[m][0] < x0 < boxes[m][2])
            o = (left_x1 - x0) / 2.0
            wl = np.clip((xs + 0.5 - x0) / (2.0 * o), 0.0, 1.0)
        else:
            wl = np.ones(w, dtype=np.float64)
        if x1 < width:
            # tile immediately to the right: same y0, smallest x0 that is < x1
            right_x0 = min(boxes[m][0] for m in range(len(boxes))
                           if m != k and boxes[m][1] == y0 and boxes[m][3] == y1
                           and boxes[m][0] < x1 < boxes[m][2])
            o = (x1 - right_x0) / 2.0
            wr = np.clip((x1 - xs - 0.5) / (2.0 * o), 0.0, 1.0)
        else:
            wr = np.ones(w, dtype=np.float64)
        # --- vertical ramps (top/bottom) ---
        ys = np.arange(y0, y1, dtype=np.float64)
        if y0 > 0:
            top_y1 = max(boxes[m][3] for m in range(len(boxes))
                         if m != k and boxes[m][0] == x0 and boxes[m][2] == x1
                         and boxes[m][1] < y0 < boxes[m][3])
            o = (top_y1 - y0) / 2.0
            wt = np.clip((ys + 0.5 - y0) / (2.0 * o), 0.0, 1.0)
        else:
            wt = np.ones(h, dtype=np.float64)
        if y1 < height:
            bottom_y0 = min(boxes[m][1] for m in range(len(boxes))
                            if m != k and boxes[m][0] == x0 and boxes[m][2] == x1
                            and boxes[m][1] < y1 < boxes[m][3])
            o = (y1 - bottom_y0) / 2.0
            wb = np.clip((y1 - ys - 0.5) / (2.0 * o), 0.0, 1.0)
        else:
            wb = np.ones(h, dtype=np.float64)
        weight = wt[:, None] * wb[:, None] * wl[None, :] * wr[None, :]
        sum_w[y0:y1, x0:x1] += weight[..., None] * tile.astype(np.float64)
        sum_a[y0:y1, x0:x1] += weight
    out = np.clip(np.round(sum_w / sum_a[..., None]), 0, 255).astype(np.uint8)
    return out
