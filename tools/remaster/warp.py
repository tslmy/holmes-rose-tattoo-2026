"""Room warp: a smooth map from original (logic) coordinates to the redrawn picture.

The map is m(p) = s * (p + d(p)): p is a point in the original 640x480 (or
1280x480) room, s the nominal picture scale (picture size / original size per
axis) and d a displacement in original pixels, stored on a coarse control grid
and interpolated bilinearly. Fitting solves a sparse least-squares problem:
matched points (dense flow, SIFT, object anchors) pull the grid, a
second-difference penalty keeps it smooth, and a small pull towards zero
keeps unconstrained areas at identity. Robust re-weighting drops bad matches.

Checks (docs/remaster-plan.md, QA gates): the map must be invertible (no fold:
the Jacobian determinant of p + d(p) stays positive) with bounded stretch (both
singular values within [1/max_stretch, max_stretch]); the numerical inverse
must round-trip.

Pure numpy/scipy; matching helpers that need OpenCV are in `match_points`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class GridWarp:
    width: int                 # original room size
    height: int
    scale: tuple[float, float]  # picture px per original px (x, y)
    dx: np.ndarray             # (ny, nx) displacement in original px at control nodes
    dy: np.ndarray
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def identity(cls, width: int, height: int, scale: tuple[float, float], spacing: float = 40.0) -> "GridWarp":
        nx, ny = grid_shape(width, height, spacing)
        return cls(width, height, scale, np.zeros((ny, nx)), np.zeros((ny, nx)))

    @property
    def shape(self) -> tuple[int, int]:
        return self.dx.shape

    def _cell(self, pts: np.ndarray):
        ny, nx = self.shape
        gx = np.clip(pts[:, 0] / self.width * (nx - 1), 0, nx - 1 - 1e-9)
        gy = np.clip(pts[:, 1] / self.height * (ny - 1), 0, ny - 1 - 1e-9)
        ix, iy = np.floor(gx).astype(int), np.floor(gy).astype(int)
        return ix, iy, gx - ix, gy - iy

    def displacement(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        ix, iy, fx, fy = self._cell(pts)
        out = np.empty_like(pts)
        for k, grid in enumerate((self.dx, self.dy)):
            out[:, k] = (grid[iy, ix] * (1 - fx) * (1 - fy) + grid[iy, ix + 1] * fx * (1 - fy)
                         + grid[iy + 1, ix] * (1 - fx) * fy + grid[iy + 1, ix + 1] * fx * fy)
        return out

    def to_original_units(self, pts: np.ndarray) -> np.ndarray:
        """p + d(p): where p lands, in original-pixel units (before the nominal scale)."""
        pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        return pts + self.displacement(pts)

    def forward(self, pts: np.ndarray) -> np.ndarray:
        """Original coordinates -> picture coordinates."""
        return self.to_original_units(pts) * np.asarray(self.scale)

    def inverse(self, picture_pts: np.ndarray, iterations: int = 60) -> np.ndarray:
        """Picture coordinates -> original coordinates (fixed-point iteration)."""
        u = np.asarray(picture_pts, dtype=float).reshape(-1, 2) / np.asarray(self.scale)
        x = u.copy()
        for _ in range(iterations):
            x_new = u - self.displacement(x)
            if np.max(np.abs(x_new - x), initial=0) < 1e-3:
                x = x_new
                break
            x = 0.5 * x + 0.5 * x_new if iterations > 40 else x_new
        return x

    def inverse_maps(self, picture_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Dense (map_x, map_y) in original px for every picture pixel (for cv2.remap)."""
        w, h = picture_size
        ys, xs = np.mgrid[0:h, 0:w].astype(float)
        pts = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], axis=1)
        inv = self.inverse(pts) - 0.5
        return inv[:, 0].reshape(h, w).astype(np.float32), inv[:, 1].reshape(h, w).astype(np.float32)

    def jacobian_stats(self, step: float = 2.0) -> dict[str, float]:
        """Determinant and stretch of p -> p + d(p), sampled every `step` original px."""
        xs = np.arange(0, self.width - step, step) + step / 2
        ys = np.arange(0, self.height - step, step) + step / 2
        gx, gy = np.meshgrid(xs, ys)
        p = np.stack([gx.ravel(), gy.ravel()], axis=1)
        e = 0.5
        mx = (self.to_original_units(p + [e, 0]) - self.to_original_units(p - [e, 0])) / (2 * e)
        my = (self.to_original_units(p + [0, e]) - self.to_original_units(p - [0, e])) / (2 * e)
        jac = np.stack([mx, my], axis=2)  # (n, 2, 2): columns are d/dx, d/dy
        det = np.linalg.det(jac)
        sv = np.linalg.svd(jac, compute_uv=False)
        stretch = np.maximum(sv[:, 0], 1.0 / np.maximum(sv[:, 1], 1e-6))
        disp = np.linalg.norm(self.displacement(p), axis=1)
        return {"min_det": float(det.min()), "max_stretch": float(stretch.max()),
                "p99_stretch": float(np.percentile(stretch, 99)),
                "disp_median": float(np.median(disp)), "disp_p95": float(np.percentile(disp, 95)),
                "disp_max": float(disp.max())}

    def roundtrip_error(self, samples: int = 400, seed: int = 0) -> float:
        rng = np.random.default_rng(seed)
        p = rng.uniform([0, 0], [self.width, self.height], size=(samples, 2))
        back = self.inverse(self.forward(p))
        return float(np.max(np.linalg.norm(back - p, axis=1)))

    def to_json(self) -> dict[str, Any]:
        ny, nx = self.shape
        return {"width": self.width, "height": self.height, "scale": list(self.scale), "grid": [nx, ny],
                "dx": np.round(self.dx, 3).tolist(), "dy": np.round(self.dy, 3).tolist(), "meta": self.meta}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "GridWarp":
        return cls(int(data["width"]), int(data["height"]), tuple(data["scale"]), np.asarray(data["dx"], float),
                   np.asarray(data["dy"], float), dict(data.get("meta", {})))


def grid_shape(width: int, height: int, spacing: float) -> tuple[int, int]:
    return max(2, int(round(width / spacing)) + 1), max(2, int(round(height / spacing)) + 1)


def fit_grid_warp(width: int, height: int, scale: tuple[float, float], src: np.ndarray, dst: np.ndarray,
                  weights: np.ndarray | None = None, *, spacing: float = 40.0, smooth: float = 2.0,
                  shrink: float = 0.01, robust_iters: int = 3, huber: float = 3.0) -> GridWarp:
    """Fit d so that src + d(src) ~= dst (both in original px units)."""
    from scipy.sparse import coo_matrix, vstack
    from scipy.sparse.linalg import lsqr

    src = np.asarray(src, float).reshape(-1, 2)
    dst = np.asarray(dst, float).reshape(-1, 2)
    base = np.ones(len(src)) if weights is None else np.asarray(weights, float)
    nx, ny = grid_shape(width, height, spacing)
    warp = GridWarp(width, height, scale, np.zeros((ny, nx)), np.zeros((ny, nx)))
    n_nodes = nx * ny
    ix, iy, fx, fy = warp._cell(src)
    rows = np.repeat(np.arange(len(src)), 4)
    cols = np.stack([iy * nx + ix, iy * nx + ix + 1, (iy + 1) * nx + ix, (iy + 1) * nx + ix + 1], 1).ravel()
    vals = np.stack([(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy], 1).ravel()
    data_a = coo_matrix((vals, (rows, cols)), shape=(len(src), n_nodes)).tocsr()

    reg_rows, reg_cols, reg_vals, r = [], [], [], 0
    for y in range(ny):  # second differences along x and y, and a weak pull to zero
        for x in range(nx):
            node = y * nx + x
            if 0 < x < nx - 1:
                reg_rows += [r] * 3
                reg_cols += [node - 1, node, node + 1]
                reg_vals += [smooth, -2 * smooth, smooth]
                r += 1
            if 0 < y < ny - 1:
                reg_rows += [r] * 3
                reg_cols += [node - nx, node, node + nx]
                reg_vals += [smooth, -2 * smooth, smooth]
                r += 1
            reg_rows.append(r)
            reg_cols.append(node)
            reg_vals.append(shrink)
            r += 1
    reg = coo_matrix((reg_vals, (reg_rows, reg_cols)), shape=(r, n_nodes)).tocsr()
    target = dst - src
    w = base.copy()
    # Normalise data weight so `smooth` means the same with 50 or 5000 matches.
    for _ in range(max(1, robust_iters)):
        norm = np.sqrt(w / max(w.sum(), 1e-9) * n_nodes)
        a = vstack([data_a.multiply(norm[:, None]), reg]).tocsr()
        sol = []
        for k in range(2):
            b = np.concatenate([target[:, k] * norm, np.zeros(r)])
            sol.append(lsqr(a, b, atol=1e-8, btol=1e-8, iter_lim=5000)[0])
        warp.dx, warp.dy = sol[0].reshape(ny, nx), sol[1].reshape(ny, nx)
        resid = np.linalg.norm(warp.to_original_units(src) - dst, axis=1)
        w = base * np.where(resid <= huber, 1.0, huber / np.maximum(resid, 1e-9))
    resid = np.linalg.norm(warp.to_original_units(src) - dst, axis=1)
    warp.meta.update({"matches": int(len(src)), "spacing": spacing, "smooth": smooth,
                      "residual_median": float(np.median(resid)) if len(resid) else 0.0,
                      "residual_p90": float(np.percentile(resid, 90)) if len(resid) else 0.0})
    return warp


def match_points(original: np.ndarray, redraw_small: np.ndarray, *, step: int = 8) -> dict[str, Any]:
    """Dense + sparse correspondences between two same-size grey images (uint8).

    Returns src/dst point arrays (original px), weights and diagnostics. SIFT
    with a ratio test and MAGSAC gives a global homography H; the redraw is
    pulled back through H and DIS optical flow (forward-backward checked)
    gives the dense residual, sampled every `step` px where the original is
    textured.
    """
    import cv2

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    a = cv2.GaussianBlur(clahe.apply(original), (0, 0), 1.0)
    b = cv2.GaussianBlur(clahe.apply(redraw_small), (0, 0), 1.0)
    h, w = a.shape
    sift = cv2.SIFT_create(nfeatures=4000)
    ka, da = sift.detectAndCompute(a, None)
    kb, db = sift.detectAndCompute(b, None)
    src_s = dst_s = np.zeros((0, 2))
    homography = np.eye(3)
    inliers = 0
    if da is not None and db is not None and len(ka) >= 8 and len(kb) >= 8:
        pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * n.distance]
        if len(good) >= 8:
            pa = np.float32([ka[m.queryIdx].pt for m in good])
            pb = np.float32([kb[m.trainIdx].pt for m in good])
            found, mask = cv2.findHomography(pa, pb, cv2.USAC_MAGSAC, 6.0)
            if found is not None and mask is not None and mask.sum() >= 12:
                homography = found
                keep = mask.ravel().astype(bool)
                src_s, dst_s = pa[keep].astype(float), pb[keep].astype(float)
                inliers = int(keep.sum())
    pulled = cv2.warpPerspective(b, homography, (w, h), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REFLECT)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    fwd = dis.calc(a, pulled, None)
    bwd = dis.calc(pulled, a, None)
    ys, xs = np.mgrid[step // 2:h:step, step // 2:w:step]
    xs, ys = xs.ravel().astype(float), ys.ravel().astype(float)
    f = fwd[ys.astype(int), xs.astype(int)]
    xb, yb = np.clip(xs + f[:, 0], 0, w - 1), np.clip(ys + f[:, 1], 0, h - 1)
    fb = bwd[yb.astype(int), xb.astype(int)]
    err = np.linalg.norm(f + fb, axis=1)
    grad = cv2.magnitude(cv2.Sobel(a, cv2.CV_32F, 1, 0), cv2.Sobel(a, cv2.CV_32F, 0, 1))
    texture = grad[ys.astype(int), xs.astype(int)]
    ok = (err < 1.5) & (texture > np.percentile(grad, 50))
    moved = np.stack([xs + f[:, 0], ys + f[:, 1]], 1)[ok]
    ones = np.ones((len(moved), 1))
    proj = np.hstack([moved, ones]) @ homography.T
    dst_d = proj[:, :2] / proj[:, 2:3]
    src_d = np.stack([xs, ys], 1)[ok]
    src = np.vstack([src_d, src_s])
    dst = np.vstack([dst_d, dst_s])
    weights = np.concatenate([np.full(len(src_d), 1.0), np.full(len(src_s), 2.0)])
    return {"src": src, "dst": dst, "weights": weights, "homography": homography.tolist(),
            "sift_inliers": inliers, "flow_points": int(ok.sum()), "flow_candidates": int(len(ok)),
            "flow_fb_error_median": float(np.median(err)) if len(err) else 0.0}
