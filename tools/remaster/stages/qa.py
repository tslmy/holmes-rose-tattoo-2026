"""`qa`: background gates and VLM review for each redraw (docs/remaster-plan.md, QA gates).

One job per (scene, backend, level) -> work/qa/scene_NNN/<backend>/<level>/qa.json.

Deterministic gates, from the geometry sidecar and detections:
  objects    every named object the detector finds confidently in the original
             (score >= thresholds.qa_confident_score) is found again in the redraw where
             the shipped warp puts it (IoU >= 0.3); weaker ones count towards the score only
  order      found objects keep their left-right and front-back (bottom edge) order
  warp       invertible, stretch bounded, inverse round-trips; L0/L1 drift within limits
  offsets    L1 only: re-segmented static layers move <= max_sprite_offset_px
  protected  every writing/clue region small enough was warped in from the SR baseline
VLM review (vision model from [llm]):
  vlm        "what was added or removed?" on original and result side by side; fails if
             the model names a removed or added object
  floor      the warped walk zones still lie on walkable ground (asked with the zones tinted)
Ranking only (not gates): palette closeness (chroma ratio and hue histogram overlap)
and the VLM's 1-10 photoreal rating.

A failed VLM call is recorded and leaves its gate `null` (unknown), not failed.
Re-queueing failures with changed settings is the Phase 2 QA loop and is not done here.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..config import Config
from ..jobs import JobSpec
from ..llm import ChatClient
from ..warp import GridWarp
from . import brief, common, geometry, prepare

NAME = "qa"
SUMMARY = "Background gates (objects, order, warp, offsets, protected text), VLM review, palette score"
CODE = [Path(__file__)]

PEOPLE = re.compile(r"\b(person|people|man|men|woman|women|figure|figures|child|children|boy|girl|crowd|"
                    r"pedestrian|passer|customer|patron|officer|constable|gentleman|lady)s?\b", re.IGNORECASE)
VLM_SYSTEM = "You are a meticulous art director checking a remastered video game background. Answer with JSON only."
VLM_DIFF = """Image 1 (left) is the original 1996 game room; image 2 (right) is a photoreal remaster of it.
List the differences in content, not in style or rendering quality. Return JSON:
{"removed": [objects or people in the original that are missing from the remaster],
 "added": [objects or people in the remaster that were not in the original],
 "moved": [objects that clearly changed position or order],
 "writing_changed": true if any sign, poster or printed text was changed, garbled or lost,
 "photoreal": 1-10 how much the remaster looks like a real photograph of a built set,
 "palette_kept": 1-10 how well the remaster keeps the original's colours and their vibrancy}
Only list clear, specific objects (e.g. "fire hydrant", "man behind the bar"); use [] when none."""
VLM_FLOOR = """The cyan-tinted areas mark where characters walk in this game room. Return JSON:
{"floor": true if every tinted area lies on walkable ground (floor, street, pavement, rug, stairs) and not on
walls, furniture tops, windows or sky, "problems": [short descriptions of tinted areas that are not ground]}"""


def job_dir(cfg: Config, scene: int, backend: str, level: str) -> Path:
    return cfg.work / "qa" / f"scene_{scene:03d}" / backend / level


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    llm = cfg.section("llm")
    for spec in geometry.plan(cfg, args):
        p = spec.params
        src = geometry.job_dir(cfg, p["scene"], p["backend"], p["level"])
        fp = common.fingerprint(common.read_stamp(src).get("fingerprint"), llm.get("vision_model"),
                                cfg.section("thresholds"), VLM_DIFF, VLM_FLOOR, *CODE)
        specs.append(JobSpec(NAME, spec.key, dict(p), fp, after=[spec.id], tags=spec.tags + ["llm-models"]))
    return specs


def _bbox(poly: np.ndarray) -> list[float]:
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return [float(x0), float(y0), float(x1 - x0), float(y1 - y0)]


def object_gates(sidecar: dict, confident: float) -> dict:
    warp = GridWarp.from_json(sidecar["warp"])
    found, missing, weak_missing, rows = [], [], [], []
    for a in sidecar.get("anchors", []):
        if not a["detectable"]:
            continue
        x, y, w, h = a["orig_det"] or a["box"]
        corners = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]], float)
        expect = _bbox(warp.to_original_units(corners))
        det = a.get("redraw_det")
        ok = bool(det and brief.iou(det, expect) >= 0.3)
        rows.append({"id": a["id"], "label": a["label"], "orig_score": a["orig_score"],
                     "redraw_score": a.get("redraw_score"), "found": ok})
        if ok:
            found.append((a, det))
        elif a["orig_score"] >= confident:
            missing.append(a["label"])
        else:
            weak_missing.append(a["label"])
    detectable = len(rows)
    inversions = []
    for i in range(len(found)):
        for j in range(i + 1, len(found)):
            (a, da), (b, db) = found[i], found[j]
            ao, bo = a["orig_det"] or a["box"], b["orig_det"] or b["box"]
            # left-right: only objects side by side (no horizontal overlap in the original)
            apart = ao[0] + ao[2] <= bo[0] or bo[0] + bo[2] <= ao[0]
            dx_o = (ao[0] + ao[2] / 2) - (bo[0] + bo[2] / 2)
            dx_r = (da[0] + da[2] / 2) - (db[0] + db[2] / 2)
            if apart and abs(dx_o) > 8 and np.sign(dx_o) != np.sign(dx_r) and abs(dx_r) > 8:
                inversions.append(f"{a['label']} / {b['label']} left-right")
            # front-back: only things standing on the floor (people, sprite layers) have a depth order
            if a.get("standing", True) and b.get("standing", True):
                dy_o = (ao[1] + ao[3]) - (bo[1] + bo[3])
                dy_r = (da[1] + da[3]) - (db[1] + db[3])
                if abs(dy_o) > 8 and np.sign(dy_o) != np.sign(dy_r) and abs(dy_r) > 8:
                    inversions.append(f"{a['label']} / {b['label']} front-back")
    return {"detectable": detectable, "found": len(found),
            "found_ratio": round(len(found) / detectable, 3) if detectable else None,
            "missing_confident": missing, "missing_weak": weak_missing, "objects": rows,
            "objects_ok": not missing, "order_inversions": inversions, "order_ok": not inversions}


def palette_scores(original: Image.Image, result: Image.Image) -> dict:
    import cv2

    def lab(img):
        arr = np.asarray(img.convert("RGB").resize((320, int(320 * img.height / img.width))))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)

    lo, lr = lab(original), lab(result)
    chroma = lambda l: np.hypot(l[:, 1] - 128, l[:, 2] - 128)  # noqa: E731
    co, cr = chroma(lo), chroma(lr)
    ratio = float(cr.mean() / max(co.mean(), 1e-6))

    def hue_hist(l, c):
        hue = np.arctan2(l[:, 2] - 128, l[:, 1] - 128)
        hist, _ = np.histogram(hue[c > 8], bins=24, range=(-np.pi, np.pi))
        return hist / max(hist.sum(), 1)
    overlap = float(np.minimum(hue_hist(lo, co), hue_hist(lr, cr)).sum())
    score = float(np.clip(1 - abs(np.log(max(ratio, 1e-3))), 0, 1) * 0.5 + overlap * 0.5)
    return {"chroma_ratio": round(ratio, 3), "hue_overlap": round(overlap, 3), "palette_score": round(score, 3)}


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in reply: {text[:200]}")
    return json.loads(match.group(0))


def side_by_side(original: Image.Image, result: Image.Image, path: Path, height: int = 640) -> Path:
    a = original.convert("RGB").resize((int(original.width * height / original.height), height), Image.LANCZOS)
    b = result.convert("RGB").resize((int(result.width * height / result.height), height), Image.LANCZOS)
    canvas = Image.new("RGB", (a.width + b.width + 16, height), (255, 255, 255))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 16, 0))
    canvas.save(path, quality=90)
    return path


def walk_tint(result: Image.Image, polys: list, path: Path) -> Path:
    img = result.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for poly in polys:
        draw.polygon([tuple(p) for p in poly], fill=(0, 230, 255, 110))
    img.alpha_composite(layer)
    img.convert("RGB").save(path, quality=90)
    return path


def vlm_review(cfg: Config, original: Image.Image, result: Image.Image, sidecar: dict, out: Path) -> dict:
    review: dict = {"errors": []}
    try:
        client = ChatClient.from_config(cfg.section("llm"), "vision")
    except Exception as err:  # noqa: BLE001
        review["errors"].append(f"client: {err}")
        return review
    review["model"] = client.model
    pair = side_by_side(original, result, out / "pair.jpg")
    try:
        reply = client.chat(VLM_SYSTEM, VLM_DIFF, images=[pair], max_tokens=700)
        review["diff"] = parse_json(reply.text)
        review["diff_seconds"] = reply.seconds
    except Exception as err:  # noqa: BLE001
        review["errors"].append(f"diff: {type(err).__name__}: {str(err)[:300]}")
    polys = sidecar.get("walk_zones_picture") or []
    if polys:
        tinted = walk_tint(result, polys, out / "walk.jpg")
        try:
            reply = client.chat(VLM_SYSTEM, VLM_FLOOR, images=[tinted], max_tokens=400)
            review["floor"] = parse_json(reply.text)
            review["floor_seconds"] = reply.seconds
        except Exception as err:  # noqa: BLE001
            review["errors"].append(f"floor: {type(err).__name__}: {str(err)[:300]}")
    return review


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene, backend, level = int(params["scene"]), params["backend"], params["level"]
    target = job_dir(cfg, scene, backend, level)
    if common.up_to_date(target, fingerprint):
        return json.loads((target / "qa.json").read_text(encoding="utf-8"))["summary"]
    src = geometry.job_dir(cfg, scene, backend, level)
    sidecar = json.loads((src / "geometry.json").read_text(encoding="utf-8"))
    tmp = common.scratch_dir(cfg, f"qa-{scene}-{backend}-{level}")
    try:
        if sidecar["summary"].get("status") != "ok":
            summary = {"status": sidecar["summary"].get("status"), "reason": sidecar["summary"].get("reason"),
                       "pass": False}
            common.write_json(tmp / "qa.json", {"summary": summary})
        else:
            summary = evaluate(cfg, scene, level, sidecar, src, tmp)
        common.write_stamp(tmp, fingerprint, scene=scene, backend=backend, level=level)
        common.install_dir(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return summary


def evaluate(cfg: Config, scene: int, level: str, sidecar: dict, src: Path, out: Path) -> dict:
    confident = cfg.threshold("qa_confident_score", 0.2)
    original = Image.open(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate_dedither.png").convert("RGB")
    result = Image.open(src / "final.png").convert("RGB")
    geo = sidecar["summary"]
    gates: dict = {"warp": geo["warp_ok"]}
    objs = object_gates(sidecar, confident)
    gates["objects"], gates["order"] = objs["objects_ok"], objs["order_ok"]
    if level == "L1":
        gates["offsets"] = geo.get("layers_offset_ok", True)
    regions = sidecar.get("protected_regions", [])
    # every region is either warped in from the baseline or explicitly left (too large, with a reason)
    gates["protected"] = all(r.get("pasted") or r.get("reason") for r in regions)
    review = vlm_review(cfg, original, result, sidecar, out)
    diff = review.get("diff") or {}
    if "diff" in review:
        removed, added = list(diff.get("removed") or []), list(diff.get("added") or [])
        # Removing anything, or adding a figure (an extra character misleads the player), fails;
        # other additions (furniture glimpsed through a door) only lower the score.
        added_people = [a for a in added if PEOPLE.search(str(a))]
        gates["vlm"] = not removed and not added_people
    else:
        removed = added = []
        gates["vlm"] = None
    if "floor" in review:
        gates["floor"] = bool(review["floor"].get("floor"))
    elif sidecar.get("walk_zones_picture"):
        gates["floor"] = None
    palette = palette_scores(original, result)
    known = [v for v in gates.values() if v is not None]
    passed = all(known)
    photoreal = diff.get("photoreal")
    extra = min(len(added) + len(diff.get("moved") or []), 5) / 5 if "diff" in review else 0.0
    score = (0.35 * (objs["found_ratio"] if objs["found_ratio"] is not None else 0.5)
             + 0.15 * (1.0 if objs["order_ok"] else 0.0)
             + 0.15 * (1.0 if geo["warp_ok"] else 0.0)
             + 0.2 * ((float(photoreal) / 10) if isinstance(photoreal, (int, float)) else 0.5)
             + 0.15 * palette["palette_score"] - 0.1 * extra)
    summary = {"status": "ok", "level": level, "pass": passed, "gates": gates,
               "gates_passed": sum(1 for v in known if v), "gates_known": len(known),
               "found_ratio": objs["found_ratio"], "missing": objs["missing_confident"],
               "inversions": len(objs["order_inversions"]), "removed": removed, "added": added,
               "writing_changed": diff.get("writing_changed"), "photoreal": photoreal,
               "palette_kept": diff.get("palette_kept"), **palette, "score": round(score, 3),
               "vlm_errors": review.get("errors", [])}
    common.write_json(out / "qa.json", {"summary": summary, "objects": objs, "review": review,
                                         "geometry": geo})
    return summary
