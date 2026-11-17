"""`assemble`: one 2x room background per room for the playable pack.

Per scene, work/assemble/backgrounds/scene_NNN/:
  background@2x.png  the room's background at 2x. Either a redraw (the most ambitious QA-passed
                     backend x level, best QA score among equals, warped back to
                     the original layout with cv2.remap and filled from the ESRGAN
                     baseline where the picture does not reach) or the ESRGAN
                     baseline copied through unchanged (no candidate remaster).
  pick.json          the per-room choice (source, backend, level, score, why) plus
                     the job fingerprint and, for a redraw, the valid fraction
  room_NNN/          static room objects (single-frame room images), NNN = the
                    layer's image_index: frame_000@2x.png cut out of the room's own
                    2x background at the place the object is drawn, so a redrawn
                    room does not show 1996 object art under a 1996 object

A room keeps the baseline unless some backend x level passes every QA gate AND
actually changes the picture (bakeoff_min_change): a redraw that barely moves the
picture is not a remaster. The choice follows the QA results, so a failed redraw
never blocks a room (this job depends only on the baseline).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .. import bakeoff
from ..config import Config
from ..jobs import JobSpec
from ..warp import GridWarp
from . import baseline, common, geometry, prepare, qa, redraw_bg

NAME = "assemble"
SUMMARY = "One 2x background per room for the playable pack (the best redraw, else the ESRGAN baseline)"
CODE = [Path(__file__)]
SCALE = 2


def out_root(cfg: Config) -> Path:
    return cfg.work / "assemble"


def pick_room(cfg: Config, scene: int) -> dict:
    """Choose the room's source: the most ambitious QA-passing redraw that changes the
    picture, or the ESRGAN baseline when none qualifies."""
    min_change = cfg.threshold("bakeoff_min_change", 20.0)
    candidates = []
    for backend in redraw_bg.backends(cfg):
        for level in redraw_bg.levels(cfg):
            summary = _read(qa.job_dir(cfg, scene, backend, level) / "qa.json").get("summary", {})
            if not (summary.get("status") == "ok" and summary.get("pass")):
                continue
            final = geometry.job_dir(cfg, scene, backend, level) / "final.png"
            if not final.is_file():
                continue
            change = bakeoff.change_from_original(cfg, scene, final)
            if change is None or change < min_change:
                continue
            candidates.append({"backend": backend, "level": level, "score": summary.get("score") or 0.0,
                               "change": change})
    if not candidates:
        return {"scene": scene, "source": "baseline", "backend": None, "level": None, "score": None,
                "why": "no QA-passing redraw changes the picture enough; keep the ESRGAN baseline"}
    best = max(candidates,
               key=lambda c: (bakeoff.LEVEL_RANK[redraw_bg.base_level(cfg, c["level"])], c["score"]))
    return {"scene": scene, "source": "redraw", "backend": best["backend"], "level": best["level"],
            "score": best["score"],
            "why": f"most ambitious level that passed (change {best['change']}, score {best['score']})"}


def room_sprites(cfg: Config, scene: int, background: Image.Image, out_dir: Path) -> list[dict]:
    """Cut each static room object out of the room's own 2x background.

    The engine draws every room object over the background; for a static object
    (its room image has one frame) the engine loads a replacement frame from
    <pack>/scene_NNN/room_NNN/frame_000@2x.png, NNN the layer's image_index.
    Cutting that frame out of the 2x background at the place the object is drawn
    makes it pixel-identical to what is under it. Written into
    out_dir/room_NNN/frame_000@2x.png; returns one record per file written.
    """
    plate = _read(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate.json")
    layers = plate.get("layers") or []
    counts: dict[int, int] = {}
    for layer in layers:
        try:
            key = int(layer["image_index"])
        except (TypeError, ValueError):
            continue
        counts[key] = counts.get(key, 0) + 1
    written: list[dict] = []
    for layer in layers:
        try:
            index = int(layer["image_index"])
        except (TypeError, ValueError):
            continue
        if counts[index] > 1:
            continue  # two layers share this image_index: ambiguous which one is which
        frame = Path(layer["source"]) if Path(layer["source"]).is_absolute() else cfg.root / layer["source"]
        metadata = _read(frame.parent / "metadata.json")
        frame_count = metadata.get("frame_count")
        if frame_count is None:
            frame_count = len(metadata.get("frames") or [])
        if frame_count != 1 or not frame.is_file():
            continue  # animated, or the frame file is gone: never cut
        frame_img = Image.open(frame)
        size = (2 * frame_img.width, 2 * frame_img.height)
        x, y, w, h = (int(layer[k]) for k in ("x", "y", "width", "height"))
        cut = background.crop((2 * x, 2 * y, 2 * (x + w), 2 * (y + h)))
        if cut.size != size:
            cut = cut.resize(size, Image.LANCZOS)
        if layer.get("flipped"):
            cut = cut.transpose(Image.FLIP_LEFT_RIGHT)  # store it un-flipped; the engine flips on draw
        alpha = frame_img.getchannel("A").resize(size, Image.NEAREST)
        out = Image.merge("RGBA", (*cut.convert("RGBA").split()[:3], alpha))
        out_path = out_dir / f"room_{index:03d}" / "frame_000@2x.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(out_path)
        written.append({"name": layer["name"], "image_index": index, "path": str(out_path)})
    return written


def warp_back(picture: Image, warp: GridWarp, out_size: tuple[int, int], fill: Image) -> tuple[Image, np.ndarray]:
    """Pull the redraw back to the original layout at `out_size`.

    For each output pixel, the original point (x, y) is warped forward to its
    position in the redraw's picture and the picture is sampled there; pixels
    whose position falls outside the picture are invalid and take the fill image.
    Returns (image, valid), valid a bool array of shape (height, width).
    """
    import cv2

    out_w, out_h = out_size
    pw, ph = picture.size
    ys, xs = np.mgrid[0:out_h, 0:out_w].astype(float)
    x = (xs + 0.5) * warp.width / out_w
    y = (ys + 0.5) * warp.height / out_h
    pts = np.stack([x.ravel(), y.ravel()], axis=1)
    picture_pos = warp.forward(pts)  # (out_h * out_w, 2), picture px
    map_x = (picture_pos[:, 0] - 0.5).reshape(out_h, out_w).astype(np.float32)
    map_y = (picture_pos[:, 1] - 0.5).reshape(out_h, out_w).astype(np.float32)
    valid = (picture_pos[:, 0] >= 0) & (picture_pos[:, 0] <= pw) \
        & (picture_pos[:, 1] >= 0) & (picture_pos[:, 1] <= ph)
    valid = valid.reshape(out_h, out_w)
    arr = np.asarray(picture.convert("RGB"))
    result = cv2.remap(arr, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if not valid.all():
        fill_arr = np.asarray(fill.convert("RGB").resize((out_w, out_h)))
        result[~valid] = fill_arr[~valid]
    return Image.fromarray(result), valid


def _qa_texts(cfg: Config, scene: int) -> list[str]:
    """The text of every existing qa.json for the scene, all backends x levels."""
    texts = []
    for backend in redraw_bg.backends(cfg):
        for level in redraw_bg.levels(cfg):
            path = qa.job_dir(cfg, scene, backend, level) / "qa.json"
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8"))
    return texts


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    for scene in common.resolve_scenes(cfg, args.scenes, args.all):
        base = baseline.out_root(cfg) / f"scene_{scene:03d}"
        fp = common.fingerprint(*_qa_texts(cfg, scene),
                                common.read_stamp(base).get("fingerprint"), *CODE)
        specs.append(JobSpec(NAME, f"room/{scene:03d}", {"scene": scene}, fp,
                             after=[f"baseline:scene/{scene:03d}"]))
    return specs


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene = int(params["scene"])
    pick = pick_room(cfg, scene)
    base_img = Image.open(baseline.out_root(cfg) / f"scene_{scene:03d}" / "plate_esrgan.png").convert("RGB")
    target = out_root(cfg) / "backgrounds" / f"scene_{scene:03d}"
    if common.up_to_date(target, fingerprint):
        return {**(json.loads((target / "pick.json").read_text(encoding="utf-8"))) | {"skipped": True}}
    valid_fraction = None
    if pick["source"] == "redraw":
        out_size = (base_img.width, base_img.height)
        sidecar = _read(geometry.job_dir(cfg, scene, pick["backend"], pick["level"]) / "geometry.json")
        if sidecar.get("original_size"):
            out_size = (int(sidecar["original_size"][0]) * SCALE, int(sidecar["original_size"][1]) * SCALE)
        warp = GridWarp.from_json(sidecar["warp"])
        picture = Image.open(geometry.job_dir(cfg, scene, pick["backend"], pick["level"]) / "final.png").convert("RGB")
        result, valid = warp_back(picture, warp, out_size, base_img)
        valid_fraction = round(float(valid.mean()), 4)
    else:
        result = base_img
    tmp = common.scratch_dir(cfg, f"assemble-{scene}")
    result.save(tmp / "background@2x.png")
    sprites = room_sprites(cfg, scene, result, tmp)
    record = {**pick, "fingerprint": fingerprint, **({"valid_fraction": valid_fraction} if valid_fraction is not None else {})}
    if sprites:
        record["room_sprites"] = [w["name"] for w in sprites]
    common.write_json(tmp / "pick.json", record)
    common.install_dir(tmp, target)
    return record
