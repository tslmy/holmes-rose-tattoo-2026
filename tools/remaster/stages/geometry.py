"""`geometry`: room warp, invertibility checks, protected regions, re-segmented layers.

One job per redraw (scene, backend, level), written to
work/geometry/scene_NNN/<backend>/<level>/:
  geometry.json   the sidecar: level, warp grid (original -> picture), checks, anchors,
                  static-layer alpha/offset records, protected regions
  detections.json detector results for the room's named objects, original and redraw
  final.png       the redraw with writing/clue regions warped in from the ESRGAN baseline
  overlay.png     final.png with warped hotspot boxes (yellow) and walk zones (cyan)
  layers/NN.png   static foreground layers cut from the redraw with re-segmented alpha

Warp estimation (all levels): SIFT + MAGSAC homography, DIS optical flow on
the pulled-back redraw (forward-backward checked), and anchors from the
room's named objects found by OWLv2 in both images; fitted as a smooth grid
(warp.fit_grid_warp). What ships depends on the level (design decision 1):

  L2  the fitted warp; must be invertible with stretch <= thresholds.max_warp_stretch
  L1  identity warp plus per-layer offsets (alpha/offset sidecar); measured drift must
      stay within thresholds.l1_max_drift_px
  L0  identity warp; measured drift must stay within thresholds.l0_max_drift_px

Redraws that could not be produced (backend unavailable or failed) finish with
the same status and are skipped by later stages.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .. import textcheck, warp as warpmod
from ..config import Config
from ..llm import ChatClient
from ..jobs import JobSpec
from . import baseline, brief, common, extract, prepare, redraw_bg

NAME = "geometry"
SUMMARY = "Room warp (dense matching + object anchors), checks, protected regions, layer alpha"
CODE = [Path(__file__), Path(warpmod.__file__), Path(__file__).parents[1] / "detect.py"]
PROTECT_MAX_AREA = 0.08  # a "writing" object larger than this share of the room is a facade, not a sign


def job_dir(cfg: Config, scene: int, backend: str, level: str) -> Path:
    return cfg.work / "geometry" / f"scene_{scene:03d}" / backend / level


def thresholds(cfg: Config) -> dict:
    return {"max_stretch": cfg.threshold("max_warp_stretch", 1.6),
            "l0_drift": cfg.threshold("l0_max_drift_px", 2.0),
            "l1_drift": cfg.threshold("l1_max_drift_px", 4.0),
            "max_offset": cfg.threshold("max_sprite_offset_px", 3),
            "spacing": cfg.threshold("warp_grid_spacing_px", 40),
            "smooth": cfg.threshold("warp_smoothness", 2.0)}


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    for spec in redraw_bg.plan(cfg, args):
        p = spec.params
        src = redraw_bg.job_dir(cfg, p["scene"], p["backend"], p["level"])
        fp = common.fingerprint(spec.fingerprint, common.read_stamp(src).get("fingerprint"),
                                common.read_stamp(baseline.out_root(cfg) / f"scene_{p['scene']:03d}").get("fingerprint"),
                                thresholds(cfg), *CODE)
        specs.append(JobSpec(NAME, spec.key, dict(p), fp,
                             after=[spec.id, f"baseline:scene/{p['scene']:03d}"], tags=spec.tags))
    return specs


# -- helpers -------------------------------------------------------------
def anchor_objects(objects: list[dict], orig_dets: list[dict], queries: list[str]) -> list[dict]:
    """Objects the detector also finds in the original near their hotspot box."""
    out = []
    for obj in objects:
        qi = queries.index(obj["query"])
        cands = [d for d in orig_dets if d["query"] == qi]
        best = max(cands, key=lambda d: brief.iou(d["box"], obj["box"]) * (0.5 + d["score"]), default=None)
        overlap = brief.iou(best["box"], obj["box"]) if best else 0.0
        out.append({"id": obj["id"], "label": obj["label"], "query": obj["query"], "box": obj["box"],
                    "kind": obj.get("kind"), "standing": bool(obj.get("kind") == "person" or obj.get("layer")),
                    "detectable": bool(best and overlap >= 0.25 and best["score"] >= 0.08),
                    "orig_score": round(best["score"], 3) if best else 0.0, "orig_iou": round(overlap, 3),
                    "orig_det": [round(v, 1) for v in best["box"]] if best else None})
    return out


def _project(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ np.asarray(h).T
    return p[:, :2] / p[:, 2:3]


def pick_redraw_detections(anchors: list[dict], red_dets: list[dict], queries: list[str],
                           homography: np.ndarray, size: tuple[int, int]) -> None:
    """For each detectable object choose the redraw detection that fits the global motion."""
    sigma = 0.12 * size[0]
    for a in anchors:
        a["redraw_det"], a["redraw_score"] = None, 0.0
        qi = queries.index(a["query"])
        x, y, w, h = a["box"]
        pred = _project(homography, np.array([[x, y], [x + w, y + h]], float))
        pw, ph = max(1.0, pred[1, 0] - pred[0, 0]), max(1.0, pred[1, 1] - pred[0, 1])
        pc = pred.mean(axis=0)
        best, best_value = None, 0.0
        for det in (d for d in red_dets if d["query"] == qi):
            bx, by, bw, bh = det["box"]
            ratio = (bw * bh) / (pw * ph)
            if not 0.4 <= ratio <= 2.5:
                continue
            dist = np.hypot(bx + bw / 2 - pc[0], by + bh / 2 - pc[1])
            value = det["score"] * np.exp(-(dist / sigma) ** 2 / 2)
            if value > best_value:
                best, best_value = det, value
        min_score = max(0.06, 0.4 * a["orig_score"])
        if best is not None and best["score"] >= min_score:
            a["redraw_det"] = [round(v, 1) for v in best["box"]]
            a["redraw_score"] = round(best["score"], 3)


def anchor_points(anchors: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Centre plus four inset corners of each matched object box: original -> redraw."""
    src, dst = [], []
    for a in anchors:
        if not (a["detectable"] and a.get("redraw_det")):
            continue
        sx, sy, sw, sh = a["orig_det"] or a["box"]
        dx, dy, dw, dh = a["redraw_det"]
        for u, v in ((0.5, 0.5), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)):
            src.append([sx + u * sw, sy + v * sh])
            dst.append([dx + u * dw, dy + v * dh])
    return np.asarray(src, float).reshape(-1, 2), np.asarray(dst, float).reshape(-1, 2)


def _bbox_px(poly: list[list[float]], size: tuple[int, int]) -> list[float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
    x1, y1 = min(float(size[0]), max(xs)), min(float(size[1]), max(ys))
    return [x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0)]


def warp_rect_polygon(w: warpmod.GridWarp, box, samples: int = 8) -> list[list[float]]:
    x, y, bw, bh = box
    t = np.linspace(0, 1, samples)
    edge = np.concatenate([np.stack([x + t * bw, np.full_like(t, y)], 1),
                           np.stack([np.full_like(t, x + bw), y + t * bh], 1),
                           np.stack([x + (1 - t) * bw, np.full_like(t, y + bh)], 1),
                           np.stack([np.full_like(t, x), y + (1 - t) * bh], 1)])
    return np.round(w.forward(edge), 1).tolist()


def remap(image: np.ndarray, map_x: np.ndarray, map_y: np.ndarray, interp=None) -> np.ndarray:
    import cv2

    return cv2.remap(image, map_x, map_y, interp if interp is not None else cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def resegment(redraw: np.ndarray, seed: np.ndarray, scale: float) -> np.ndarray:
    """GrabCut on the redraw seeded by the warped original mask; returns a float alpha."""
    import cv2

    if seed.sum() < 200 * 255:
        return seed.astype(np.float32) / 255
    ys, xs = np.where(seed > 127)
    pad = int(12 * scale)
    y0, y1 = max(0, ys.min() - pad), min(seed.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(seed.shape[1], xs.max() + pad + 1)
    s = (seed[y0:y1, x0:x1] > 127).astype(np.uint8)
    k_in = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, int(6 * scale) | 1),) * 2)
    k_out = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, int(10 * scale) | 1),) * 2)
    sure_fg, maybe_bg = cv2.erode(s, k_in), cv2.dilate(s, k_out)
    mask = np.full(s.shape, cv2.GC_BGD, np.uint8)
    mask[maybe_bg > 0] = cv2.GC_PR_BGD
    mask[s > 0] = cv2.GC_PR_FGD
    mask[sure_fg > 0] = cv2.GC_FGD
    if not (mask == cv2.GC_BGD).any():
        mask[0, :] = cv2.GC_BGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    crop = np.ascontiguousarray(redraw[y0:y1, x0:x1, :3])
    try:
        cv2.grabCut(crop, mask, None, bgd, fgd, 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return seed.astype(np.float32) / 255
    out = np.zeros(seed.shape, np.float32)
    fg = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.float32)
    out[y0:y1, x0:x1] = cv2.GaussianBlur(fg, (0, 0), 0.7)
    return out


def relight_patch(patch: np.ndarray, target: np.ndarray, mask: np.ndarray,
                  inner: int = 8, outer: int = 24) -> np.ndarray:
    """Match a pasted patch to the redraw's lighting.

    `patch` and `target` are HxWx3 (any numeric dtype); `mask` is HxW in 0..1
    marking the pasted region. The relight (per-channel gain * patch + offset)
    is fitted (mean and spread) over the ring of pixels that sit between
    `inner` and `outer` px from the region (dilated with an elliptical kernel).
    Gain is clipped to [0.5, 2.0]; the offset is refit as mean(target - gain * patch).
    The result is applied to the whole patch, unclipped (the caller clips).
    """
    import cv2

    p = np.asarray(patch, dtype=np.float32)
    t = np.asarray(target, dtype=np.float32)
    region = (np.asarray(mask) > 0.5).astype(np.uint8)
    if not region.any():
        return p
    k_in = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(2, int(2 * inner)) | 1, max(2, int(2 * inner)) | 1))
    k_out = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(2, int(2 * outer)) | 1, max(2, int(2 * outer)) | 1))
    ring = (cv2.dilate(region, k_out) > 0) & (cv2.dilate(region, k_in) == 0)
    if int(ring.sum()) < 50:
        return p
    x, y = p[ring], t[ring]  # x: patch (predictor), y: target (response); both NxC
    xm, ym = x.mean(axis=0), y.mean(axis=0)
    # Match spread, not a regression slope: at L2 the redraw's texture in the ring does not line up
    # pixel for pixel with the baseline, and a least-squares slope would shrink towards 0 (flat patches).
    gain = np.clip(y.std(axis=0) / np.maximum(x.std(axis=0), 1e-6), 0.5, 2.0)
    offset = ym - gain * xm
    return (p * gain + offset).astype(np.float32)


# -- job -------------------------------------------------------------------
def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene, backend, level = int(params["scene"]), params["backend"], params["level"]
    target = job_dir(cfg, scene, backend, level)
    if common.up_to_date(target, fingerprint):
        return json.loads((target / "geometry.json").read_text(encoding="utf-8"))["summary"]
    src = redraw_bg.job_dir(cfg, scene, backend, level)
    redraw_rec = json.loads((src / "record.json").read_text(encoding="utf-8"))
    tmp = common.scratch_dir(cfg, f"geometry-{scene}-{backend}-{level}")
    try:
        if redraw_rec.get("status") != "ok":
            summary = {"status": redraw_rec.get("status", "missing"), "reason": redraw_rec.get("reason")}
            common.write_json(tmp / "geometry.json", {"summary": summary})
        else:
            summary = estimate(cfg, scene, level, src / "redraw.png", tmp)
        common.write_stamp(tmp, fingerprint, scene=scene, backend=backend, level=level)
        common.install_dir(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return summary


def estimate(cfg: Config, scene: int, level: str, redraw_path: Path, out: Path) -> dict:
    import cv2

    from .. import detect

    limits = thresholds(cfg)
    prep = prepare.out_root(cfg) / f"scene_{scene:03d}"
    meta = json.loads((extract.scene_dir(cfg, "rooms", scene) / "metadata.json").read_text(encoding="utf-8"))
    plate_info = json.loads((prep / "plate.json").read_text(encoding="utf-8"))
    objects = json.loads((brief.out_dir(cfg, scene) / "objects.json").read_text(encoding="utf-8"))["objects"]
    original = Image.open(prep / "plate_dedither.png").convert("RGB")
    redraw = Image.open(redraw_path).convert("RGB")
    W, H = original.size
    scale = (redraw.width / W, redraw.height / H)
    redraw_small = redraw.resize((W, H), Image.LANCZOS)

    # 1. dense + sparse matching in original units
    matches = warpmod.match_points(np.asarray(original.convert("L")), np.asarray(redraw_small.convert("L")))
    homography = np.asarray(matches["homography"])

    # 2. named-object anchors (detector on both images, boxes in original units)
    queries = sorted({o["query"] for o in objects}) or ["object"]
    orig_dets = detect.detect(original, queries)
    red_dets = detect.detect(redraw, queries)
    for d in red_dets:
        d["box"] = [d["box"][0] / scale[0], d["box"][1] / scale[1], d["box"][2] / scale[0], d["box"][3] / scale[1]]
    anchors = anchor_objects(objects, orig_dets, queries)
    pick_redraw_detections(anchors, red_dets, queries, homography, (W, H))
    a_src, a_dst = anchor_points(anchors)
    src = np.vstack([matches["src"], a_src])
    dst = np.vstack([matches["dst"], a_dst])
    weights = np.concatenate([matches["weights"], np.full(len(a_src), 25.0)])
    common.write_json(out / "detections.json", {"queries": queries, "original": orig_dets, "redraw": red_dets,
                                                 "redraw_units": "original px"})

    # 3. fit, check
    if len(src) >= 12:
        fitted = warpmod.fit_grid_warp(W, H, scale, src, dst, weights, spacing=limits["spacing"],
                                       smooth=limits["smooth"])
    else:
        fitted = warpmod.GridWarp.identity(W, H, scale, limits["spacing"])
        fitted.meta["note"] = "too few matches; identity"
    measured = fitted.jacobian_stats()
    measured["roundtrip_px"] = fitted.roundtrip_error()
    base = redraw_bg.base_level(cfg, level)
    if base == "L2":
        shipped = fitted
    else:
        shipped = warpmod.GridWarp.identity(W, H, scale, limits["spacing"])
    checks = {
        "invertible": measured["min_det"] > 0.05,
        "stretch_ok": measured["max_stretch"] <= limits["max_stretch"],
        "roundtrip_ok": measured["roundtrip_px"] <= 0.5,
        "matches": len(matches["src"]),
        "anchors_used": int(len(a_src) // 5),
        "homography_scale": float(np.sqrt(abs(np.linalg.det(homography[:2, :2])))),
    }
    if base == "L0":
        checks["drift_ok"] = measured["disp_p95"] <= limits["l0_drift"]
    elif base == "L1":
        checks["drift_ok"] = measured["disp_p95"] <= limits["l1_drift"]
    else:
        checks["drift_ok"] = True
    checks["warp_ok"] = bool(checks["invertible"] and checks["stretch_ok"] and checks["roundtrip_ok"]
                             and checks["drift_ok"])

    # 4. protected regions: warp writing/clue regions in from the ESRGAN baseline
    map_x, map_y = shipped.inverse_maps(redraw.size)
    final = np.asarray(redraw).astype(np.float32)
    base_img = Image.open(baseline.out_root(cfg) / f"scene_{scene:03d}" / "plate_esrgan.png").convert("RGB")
    bscale = base_img.width / W
    base_warped = remap(np.asarray(base_img), (map_x + 0.5) * bscale - 0.5, (map_y + 0.5) * bscale - 0.5)
    protect = np.zeros((H, W), np.uint8)
    regions = []
    vision_client = None

    def vision() -> ChatClient:
        nonlocal vision_client
        if vision_client is None:
            vision_client = ChatClient.from_config(cfg.section("llm"), "vision")
        return vision_client

    def read_region_text(box) -> tuple[str, str]:
        """Read the sign's words in the original and in the redraw ("" if unreadable/failed)."""
        orig_text = textcheck.read_text(vision(), original, box)
        redraw_text = textcheck.read_text(vision(), redraw,
                                          _bbox_px(warp_rect_polygon(shipped, box), redraw.size))
        return orig_text, redraw_text

    for obj in objects:
        x, y, w, h = obj["box"]
        if not (obj.get("writing") or obj.get("clue")):
            continue
        if w * h > PROTECT_MAX_AREA * W * H:
            regions.append({"id": obj["id"], "label": obj["label"], "box": obj["box"], "pasted": False,
                            "reason": "too large to paste; left to the redraw and the VLM check"})
            continue
        rec = {"id": obj["id"], "label": obj["label"], "box": obj["box"],
               "writing": obj.get("writing"), "clue": obj.get("clue"),
               "polygon": warp_rect_polygon(shipped, obj["box"])}
        if obj.get("writing"):
            text_ok = False
            try:
                orig_text, redraw_text = read_region_text(obj["box"])
                rec["text_original"], rec["text_redraw"] = orig_text, redraw_text
                text_ok = textcheck.text_matches(orig_text, redraw_text) is True
            except Exception as err:  # noqa: BLE001
                rec["text_error"] = f"{type(err).__name__}: {str(err)[:300]}"
            if text_ok:
                rec["pasted"], rec["reason"] = False, "redraw text legible"
                regions.append(rec)
                continue
        m = 2
        protect[max(0, y - m):y + h + m, max(0, x - m):x + w + m] = 255
        rec["pasted"] = True
        regions.append(rec)
    if redraw_bg.variant(cfg, level)["mask_people"]:
        # the people were stripped from the input before the redraw; paste them
        # back from the ESRGAN baseline like any other protected region
        protect |= redraw_bg.people_mask(cfg, scene)
        for obj in objects:
            if obj.get("kind") != "person":
                continue
            regions.append({"id": obj["id"], "label": obj["label"], "box": obj["box"],
                            "pasted": True, "person": True,
                            "polygon": warp_rect_polygon(shipped, obj["box"])})
    if protect.any():
        mask = remap(protect, map_x, map_y)
        mask = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 1.0 * max(scale))[..., None]
        base_warped = relight_patch(base_warped, final, mask[..., 0])
        final = final * (1 - mask) + base_warped.astype(np.float32) * mask
    final_img = Image.fromarray(np.clip(final + 0.5, 0, 255).astype(np.uint8))
    final_img.save(out / "final.png")

    # 5. static foreground layers: warp the original layer masks, re-segment on the redraw
    layer_index = np.asarray(Image.open(prep / "plate_layers.png"))
    (out / "layers").mkdir()
    layers = []
    final_np = np.asarray(final_img)
    for layer in plate_info.get("layers", []):
        idx = layer["layer"]
        seed_orig = (layer_index == idx).astype(np.uint8) * 255
        if not seed_orig.any():
            continue
        if base == "L2":
            seed = remap(seed_orig, map_x, map_y, cv2.INTER_LINEAR)
        else:
            seed = cv2.resize(seed_orig, redraw.size, interpolation=cv2.INTER_LINEAR)
        alpha = resegment(final_np, seed, max(scale))
        seed_f = seed.astype(np.float32) / 255
        area_seed, area_new = float(seed_f.sum()), float(alpha.sum())
        inter = float(np.minimum(alpha, seed_f).sum())
        iou = inter / max(area_seed + area_new - inter, 1e-6)

        def centroid(a):
            ys, xs = np.nonzero(a > 0.5)
            return np.array([xs.mean(), ys.mean()]) if len(xs) else np.zeros(2)
        offset = (centroid(alpha) - centroid(seed_f)) / np.asarray(scale)
        ys, xs = np.nonzero(alpha > 0.02)
        rec = {"layer": idx, "name": layer["name"], "z": layer.get("z"), "seed_area": round(area_seed, 1),
               "area_ratio": round(area_new / max(area_seed, 1e-6), 3), "iou_with_seed": round(iou, 3),
               "offset_orig_px": [round(float(v), 2) for v in offset]}
        if len(xs):
            x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
            rgba = np.dstack([final_np[y0:y1, x0:x1], (alpha[y0:y1, x0:x1] * 255 + 0.5).astype(np.uint8)])
            Image.fromarray(rgba, "RGBA").save(out / "layers" / f"{idx:02d}.png")
            rec["bbox_picture"] = [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]
        if base == "L1":
            rec["offset_ok"] = bool(np.hypot(*offset) <= limits["max_offset"])
        layers.append(rec)

    # 6. overlay: warped hotspot boxes and walk zones on the final picture
    overlay = final_img.convert("RGBA")
    layer_draw = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer_draw)
    walk_polys, hot_polys = [], []
    for zone in meta.get("walk_zones", []):
        poly = warp_rect_polygon(shipped, (zone["x"], zone["y"], zone["w"], zone["h"]))
        walk_polys.append(poly)
        draw.polygon([tuple(p) for p in poly], fill=(0, 200, 255, 70), outline=(0, 220, 255, 230))
    for spot in meta.get("hotspots", []):
        if spot["w"] > 2 and spot["h"] > 2:
            poly = warp_rect_polygon(shipped, (spot["x"], spot["y"], spot["w"], spot["h"]))
            hot_polys.append(poly)
            draw.line([tuple(p) for p in poly] + [tuple(poly[0])], fill=(255, 220, 0, 230), width=2)
    for a in anchors:
        if a.get("redraw_det"):
            x, y, w, h = a["redraw_det"]
            draw.rectangle([x * scale[0], y * scale[1], (x + w) * scale[0], (y + h) * scale[1]],
                           outline=(80, 255, 80, 200), width=1)
    overlay.alpha_composite(layer_draw)
    overlay.convert("RGB").save(out / "overlay.png")

    summary = {"status": "ok", "level": level, "warp_ok": checks["warp_ok"], **checks,
               "disp_p95": round(measured["disp_p95"], 2), "max_stretch": round(measured["max_stretch"], 3),
               "min_det": round(measured["min_det"], 3), "protected": sum(r["pasted"] for r in regions),
               "layers": len(layers),
               "layers_offset_ok": all(l.get("offset_ok", True) for l in layers)}
    sidecar = {
        "summary": summary, "scene": scene, "level": level, "picture_size": list(redraw.size),
        "original_size": [W, H], "warp": shipped.to_json(), "measured_warp": fitted.to_json(),
        "measured": measured, "matching": {k: v for k, v in matches.items() if k not in ("src", "dst", "weights")},
        "anchors": anchors, "protected_regions": regions, "layers": layers,
        "walk_zones_picture": walk_polys, "hotspots_picture": hot_polys,
    }
    common.write_json(out / "geometry.json", sidecar)
    return summary
