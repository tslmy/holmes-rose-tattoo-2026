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
  room_NNN/          animated room objects (multi-frame room images) whose art mostly
                    copies the background: each frame re-based on the 2x redraw, so a
                    redrawn room does not show 1996 object art; objects that are not
                    mostly background (a walker, a person) are left to the engine

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

from .. import bakeoff, plates
from ..config import Config
from ..jobs import JobSpec
from ..warp import GridWarp
from . import baseline, common, extract, geometry, prepare, qa, redraw_bg

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
    boxes: dict[int, set] = {}
    for layer in layers:
        try:
            key = int(layer["image_index"])
        except (TypeError, ValueError):
            continue
        box = tuple(layer.get(k) for k in ("x", "y", "width", "height")) + (bool(layer.get("flipped")),)
        boxes.setdefault(key, set()).add(box)
    seen: set[int] = set()
    written: list[dict] = []
    for layer in layers:
        try:
            index = int(layer["image_index"])
        except (TypeError, ValueError):
            continue
        if len(boxes[index]) > 1:
            continue  # layers share this image_index at different places: one frame cannot fit both
        if index in seen:
            continue  # the same image at the same place (e.g. a sound trigger twice): cut once
        seen.add(index)
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


def scaled_offset(offset: int, scale: int) -> int:
    """The engine's ImageFrame::sDrawXOffset for a frame offset at a given scale (256 = unscaled).

    scale 0 counts as 1; at 256 the offset passes through unchanged.
    """
    if scale == 256:
        return offset
    scale = scale or 1
    value = offset - 1 if scale >= 256 else offset
    result = value * 256 // scale
    return result + 1 if scale > 256 else result


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity of the per-pixel RGB vectors a and b (both (h, w, 3) int/float).

    +1 is added to both to avoid zero vectors; the result is (h, w).
    """
    a = a.astype(np.float64) + 1.0
    b = b.astype(np.float64) + 1.0
    dot = np.sum(a * b, axis=-1)
    return dot / (np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1) + 1e-12)


def _frame_masks(fr: np.ndarray, ref: np.ndarray, alpha: np.ndarray, tolerance: int,
                 shift_tolerance: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Classify a 1x frame's pixels as background-copy (`same`), a brightness-only
    change (`tint`) or a small-delta change (`shift`) relative to the plate under it;
    everything else keeps old art. `shift` pixels differ by at most `shift_tolerance`
    per channel but are neither a copy nor a brightness change; they do not count
    towards the background fraction."""
    a = alpha > 0
    fr = fr.astype(int)
    ref = ref.astype(int)
    maxdiff = np.max(np.abs(fr - ref), axis=-1)
    same = a & (maxdiff <= tolerance)
    lum = (fr.sum(axis=-1) + 3) / (ref.sum(axis=-1) + 3)
    tint = a & ~same & (_cosine(fr, ref) >= 0.98) & (lum >= 0.2) & (lum <= 5.0)
    shift = a & ~same & ~tint & (maxdiff <= shift_tolerance)
    return same, tint, shift


def ambience_sprites(cfg: Config, scene: int, background: Image.Image, out_dir: Path, *,
                     tolerance: int = 8, min_bg_fraction: float = 0.2,
                     shift_tolerance: int = 24) -> list[dict]:
    """Re-base each animated room object's frames on the room's own 2x background.

    The engine draws animated room objects (lights, steam, dials, shadows, fans)
    from 1996 frames that mostly copy the background under them; on a redrawn room
    they show as old-art rectangles. For each frame the pixels that match the
    original plate take the redraw, the pixels that only change brightness (a
    glow, a shadow) take the redraw scaled by the same per-channel ratio, the
    pixels that differ only a little (a water shimmer, a hue nudge within
    `shift_tolerance`) take the redraw plus their per-channel delta, and the rest
    keep the old art, upscaled. Objects drawn scaled (scale_val != 256, e.g. a
    doorman) are handled too: the engine draws a frame of size (sw, sh) at
    position + (scaled_offset, scaled_offset) with size (sw * 256 // s,
    sh * 256 // s), so the redraw region is cut out of that box and resampled to
    the frame's 2x size. Objects that are not mostly background (a walker, a
    person; small-delta pixels do not count as background) are left to the
    engine. Written into out_dir/room_NNN/frame_iii@2x.png; returns one record
    per object written, including its scale.
    """
    plate_path = prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate.png"
    meta_path = extract.scene_dir(cfg, "rooms", scene) / "metadata.json"
    if not meta_path.is_file():
        return []
    metadata = _read(meta_path)
    objects = metadata.get("objects") or []
    sprite_root = extract.scene_dir(cfg, "room-sprites", scene)
    plate = np.asarray(Image.open(plate_path).convert("RGB"), dtype=np.int32)
    plate_h, plate_w = plate.shape[:2]

    def object_scale(obj: dict) -> int:
        """The object's draw scale (256 = unscaled); missing or 0 means 256."""
        return int(obj.get("scale_val", plates.SCALE_THRESHOLD) or plates.SCALE_THRESHOLD)

    def frame_boxes(obj: dict, frames: list[dict]) -> list[tuple[int, int, int, int] | None] | None:
        """(x0, y0, w, h) per frame at the frame's own resolution, aligned with `frames`
        (None for an invalid placeholder); None overall if a valid frame's drawn box
        leaves the plate or no frame is valid. For a scaled object (s != 256) the box
        is the drawn size (sw * 256 // s, sh * 256 // s) at position + scaled_offset,
        as the engine draws it."""
        pos = obj.get("position") or {}
        scale = object_scale(obj)
        boxes = []
        for f in frames:
            if f.get("invalid_placeholder"):
                boxes.append(None)
                continue
            sw, sh = int(f.get("width", 0)), int(f.get("height", 0))
            if sw <= 0 or sh <= 0:
                return None
            w, h = sw * 256 // scale, sh * 256 // scale
            x0 = int(pos.get("x", 0)) + scaled_offset(int(f.get("offset_x", 0)), scale)
            y0 = int(pos.get("y", 0)) + scaled_offset(int(f.get("offset_y", 0)), scale)
            if x0 < 0 or y0 < 0 or x0 + w > plate_w or y0 + h > plate_h:
                return None
            boxes.append((x0, y0, w, h))
        return boxes if any(b is not None for b in boxes) else None

    def eligible(obj: dict) -> int | None:
        """The object's image_index if it is a candidate animated room object, else None."""
        if obj.get("sprite_type") != "active_bg_shape" or not plates.flags_pass(obj):
            return None
        if object_scale(obj) <= 0:
            return None
        try:
            return int(obj.get("image_index", 0))
        except (TypeError, ValueError):
            return None

    written: list[dict] = []
    used: dict[int, int] = {}
    for obj in objects:
        index = eligible(obj)
        if index is not None:
            used[index] = used.get(index, 0) + 1
    for obj in objects:
        index = eligible(obj)
        if index is None:
            continue
        if used.get(index, 0) > 1:
            continue  # two objects share this image_index: ambiguous which one is which
        room_dir = sprite_root / f"room_{index:03d}"
        frames = _read(room_dir / "metadata.json").get("frames") or []
        if len(frames) <= 1:
            continue  # static (room_sprites' job) or missing: not an animated room object
        boxes = frame_boxes(obj, frames)
        if not boxes:
            continue
        scale = object_scale(obj)

        # Pass 1, at the frame's own resolution: classify each valid frame against the
        # plate under its drawn box (plate sampled nearest at the frame's resolution)
        # and count the background fraction (copies plus brightness-only changes).
        analysis = []
        total_same = total_opaque = 0
        for i, (f, box) in enumerate(zip(frames, boxes)):
            if box is None:
                analysis.append(None)
                continue
            x0, y0, w, h = box
            sw, sh = int(f.get("width", 0)), int(f.get("height", 0))
            frame_path = room_dir / (f.get("file") or f"frame_{i:03d}.png")
            if not frame_path.is_file():
                analysis.append(None)
                continue
            img = Image.open(frame_path).convert("RGBA")
            arr = np.asarray(img)
            fr = arr[..., :3].astype(np.int32)
            alpha = arr[..., 3]
            ref = plate[np.ix_(y0 + np.arange(sh) * h // sh, x0 + np.arange(sw) * w // sw)]
            same, tint, shift = _frame_masks(fr, ref, alpha, tolerance, shift_tolerance)
            total_same += int(same.sum())
            total_opaque += int((alpha > 0).sum())
            analysis.append((fr, alpha, ref, same, tint, shift))
        if total_opaque == 0 or total_same / total_opaque < min_bg_fraction:
            continue  # mostly its own art (a walker or person): leave it to the engine

        # Pass 2, at 2x: rebuild each frame on the redraw and save it.
        out_dir_room = out_dir / f"room_{index:03d}"
        out_dir_room.mkdir(parents=True, exist_ok=True)
        count = 0
        for i, (f, item) in enumerate(zip(frames, analysis)):
            if item is None:
                continue
            fr, alpha, ref, same, tint, shift = item
            x0, y0, w, h = boxes[i]
            sw, sh = int(f.get("width", 0)), int(f.get("height", 0))
            R = np.asarray(background.convert("RGB").crop((2 * x0, 2 * y0, 2 * x0 + 2 * w, 2 * y0 + 2 * h)))
            if (w, h) != (sw, sh):  # scaled: resample the drawn box to the frame's 2x size
                R = np.asarray(Image.fromarray(R).resize((2 * sw, 2 * sh), Image.LANCZOS))
            R = R.astype(float)
            # old art is the default; each pixel class overrides its own region.
            rgb = np.asarray(Image.fromarray(fr.astype(np.uint8)).resize((2 * sw, 2 * sh), Image.LANCZOS)).astype(float)
            if same.any():
                same2 = np.asarray(Image.fromarray(same.astype(np.uint8) * 255).resize((2 * sw, 2 * sh),
                                   Image.NEAREST), dtype=bool)
                rgb = np.where(same2[..., None], R, rgb)
            if tint.any():
                tint2 = np.asarray(Image.fromarray(tint.astype(np.uint8) * 255).resize((2 * sw, 2 * sh),
                                    Image.NEAREST), dtype=bool)
                ratio = (fr.astype(np.float64) + 1.0) / (ref.astype(np.float64) + 1.0)
                ratio2 = ratio.repeat(2, axis=0).repeat(2, axis=1)
                rgb = np.where(tint2[..., None], np.clip(R * ratio2, 0, 255), rgb)
            if shift.any():
                shift2 = np.asarray(Image.fromarray(shift.astype(np.uint8) * 255).resize((2 * sw, 2 * sh),
                                     Image.NEAREST), dtype=bool)
                # the frame's delta as an int, upscaled NEAREST per channel (offset to stay unsigned)
                delta2 = np.asarray(Image.fromarray((128 + (fr - ref).astype(np.int32)).astype(np.uint8))
                                    .resize((2 * sw, 2 * sh), Image.NEAREST)).astype(np.int32) - 128
                rgb = np.where(shift2[..., None], np.clip(R + delta2, 0, 255), rgb)
            alpha2 = np.asarray(Image.fromarray(alpha).resize((2 * sw, 2 * sh), Image.NEAREST))
            out = np.dstack([rgb.clip(0, 255).astype(np.uint8), alpha2])
            Image.fromarray(out, "RGBA").save(out_dir_room / f"frame_{i:03d}@2x.png")
            count += 1
        if count:
            written.append({"name": obj.get("name", ""), "image_index": index, "scale": scale, "frames": count,
                            "bg_fraction": round(total_same / total_opaque, 3), "dir": str(out_dir_room)})
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
    ambience = ambience_sprites(cfg, scene, result, tmp)
    record = {**pick, "fingerprint": fingerprint, **({"valid_fraction": valid_fraction} if valid_fraction is not None else {})}
    if sprites:
        record["room_sprites"] = [w["name"] for w in sprites]
    if ambience:
        record["ambience_sprites"] = [w["name"] for w in ambience]
    common.write_json(tmp / "pick.json", record)
    common.install_dir(tmp, target)
    return record
