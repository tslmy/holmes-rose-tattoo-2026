"""`redraw-bg`: photoreal re-render of each plate, per backend and freedom level.

One job per (scene, backend, level), written to
work/redraw-bg/scene_NNN/<backend>/<level>/:
  redraw.png    the backend's output at the generation size (about 1 MP)
  record.json   prompt, settings, seconds, peak memory, or the reason it could not run

The input is the de-dithered plate resized to the generation size. The prompt
is the scene's visual brief plus the level instruction (imagegen.LEVEL_PROMPTS).
Backends that cannot run here (weights deleted, no API key, server down) finish
with `status: unavailable` and the reason; their fingerprint includes that
reason, so they re-queue on the next `remaster redraw-bg` once it changes.

Variants with `remove_people = true` get a people-cleanup pass: the redraw is checked for people that
are not in the input (remaster.detect, PERSON_QUERIES), and when found the redraw is edited once more
with imagegen.REMOVE_PEOPLE_PROMPT, the cleaned pixels pasted back only around those people
(cleanup_alpha), and the untouched first pass kept as redraw_raw.png.

The 4x tiled detail pass is Phase 2 work and is not run here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from PIL import Image

from .. import imagegen
from ..config import Config
from ..jobs import JobSpec
from . import brief, common, prepare

NAME = "redraw-bg"
SUMMARY = "Photoreal re-render of each plate per backend and freedom level (L0/L1/L2)"
CODE = [Path(__file__), Path(imagegen.__file__)]


def bakeoff(cfg: Config) -> dict:
    return cfg.section("bakeoff")


def backends(cfg: Config) -> list[str]:
    return list(bakeoff(cfg).get("backends") or [cfg.section("image").get("backgrounds")])


def levels(cfg: Config) -> list[str]:
    return list(bakeoff(cfg).get("levels", ["L0", "L1", "L2"]))


def variant(cfg: Config, name: str) -> dict:
    """A named level variant: `name` must be in [bakeoff].levels; anything that is not a plain
    level (L0/L1/L2) must have a [bakeoff.variants.<name>] table with its base level and optional
    seed, strength, extras (keys of imagegen.PROMPT_EXTRAS), mask_people, backends (which backends
    it may run on) and controls (a list of [kind, strength] ControlNet pairs). Plain levels L0/L1/L2
    have no backends and no controls."""
    table = cfg.section("bakeoff").get("variants", {})
    if name in ("L0", "L1", "L2"):
        base, table_name = name, {}
    else:
        if name not in table:
            raise KeyError(f"[bakeoff.variants.{name}] is missing from {cfg.path.name} (levels: {levels(cfg)})")
        base = table[name].get("level")
        if base not in ("L0", "L1", "L2"):
            raise KeyError(f"[bakeoff.variants.{name}].level must be L0/L1/L2, got {base!r}")
        table_name = table[name]
    extras = list(table_name.get("extras") or [])
    for key in extras:
        if key not in imagegen.PROMPT_EXTRAS:
            raise KeyError(f"[bakeoff.variants.{name}].extras key {key!r} is not in imagegen.PROMPT_EXTRAS")
    named = name not in ("L0", "L1", "L2")
    return {"name": name, "level": base, "seed": int(table_name.get("seed", bakeoff(cfg).get("seed", 7))),
            "strength": table_name.get("strength"), "extras": extras,
            "mask_people": bool(table_name.get("mask_people", False)),
            "remove_people": bool(table_name.get("remove_people", False)),
            "backends": list(table_name.get("backends") or []) if named else None,
            "controls": [list(c) for c in table_name.get("controls") or []] if named else None}


def base_level(cfg: Config, name: str) -> str:
    return variant(cfg, name)["level"]


def request_settings(cfg: Config, bk: dict, name: str) -> dict:
    """The backend's settings for the variant's base level, with `strength` replaced when the
    variant sets its own and `controls` added when the variant carries ControlNet pairs."""
    settings = imagegen.level_settings(bk, base_level(cfg, name))
    var = variant(cfg, name)
    if var["strength"] is not None:
        settings["strength"] = var["strength"]
    if var["controls"]:
        settings["controls"] = var["controls"]
    return settings


def job_dir(cfg: Config, scene: int, backend: str, level: str) -> Path:
    return cfg.work / "redraw-bg" / f"scene_{scene:03d}" / backend / level


def input_path(cfg: Config, scene: int) -> Path:
    return cfg.work / "redraw-bg" / f"scene_{scene:03d}" / "input.png"


def size_for(cfg: Config, scene: int) -> tuple[int, int]:
    plate = json.loads((prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate.json").read_text(encoding="utf-8"))
    width, height = plate.get("size", [640, 480])
    return imagegen.gen_size(width, height, float(bakeoff(cfg).get("megapixels", 1.0)))


def key(scene: int, backend: str, level: str) -> str:
    return f"scene/{scene:03d}/{backend}/{level}"


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    seed = int(bakeoff(cfg).get("seed", 7))
    for scene in common.resolve_scenes(cfg, args.scenes, args.all):
        brief_fp = common.read_stamp(brief.out_dir(cfg, scene)).get("fingerprint")
        prep_fp = common.read_stamp(prepare.out_root(cfg) / f"scene_{scene:03d}").get("fingerprint")
        for backend in backends(cfg):
            reason = imagegen.availability(cfg, backend)
            bk = imagegen.backend(cfg, backend) if reason is None or "no [image" not in reason else {}
            for level in levels(cfg):
                var = variant(cfg, level)
                if var["backends"] is not None and backend not in var["backends"]:
                    continue  # the variant names the backends it runs on; this one is not one of them
                fp = common.fingerprint(brief_fp, prep_fp, bk, imagegen.level_prompt(var["level"]),
                                        var, var["extras"], var["seed"],
                                        f"unavailable:{reason}" if reason else "available", *CODE)
                tags = ["image-backend-provisional", "bakeoff-backend"]
                specs.append(JobSpec(NAME, key(scene, backend, level),
                                     {"scene": scene, "backend": backend, "level": level, "seed": var["seed"]},
                                     fp,
                                     after=[f"brief:scene/{scene:03d}", f"prepare:scene/{scene:03d}"],
                                     tags=tags))
    return specs


def people_mask(cfg: Config, scene: int, size: tuple[int, int] | None = None) -> "np.ndarray":
    """A 0/255 mask (uint8) of every person in the scene: the silhouette pixels
    (nonzero plate layers) inside each person's box, or the whole box when the
    silhouette has no pixels there; dilated 3 px. In original px, or nearest
    resized to `size` = (width, height)."""
    import cv2
    import numpy as np

    objects = json.loads((brief.out_dir(cfg, scene) / "objects.json").read_text(encoding="utf-8"))["objects"]
    layers = np.asarray(Image.open(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate_layers.png"))
    mask = np.zeros_like(layers, np.uint8)
    for obj in objects:
        if obj.get("kind") != "person":
            continue
        x, y, w, h = obj["box"]
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(mask.shape[1], int(x) + int(w)), min(mask.shape[0], int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            continue
        inside = (layers[y0:y1, x0:x1] != 0).astype(np.uint8) * 255
        mask[y0:y1, x0:x1] = inside if inside.any() else 255
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    if size is not None:
        mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return (mask > 0).astype(np.uint8) * 255


def strip_people(text: str) -> str:
    """Keep the text up to and including the first blank line unchanged; in the
    rest, drop every sentence that names a person (remaster.stages.qa.PEOPLE)."""
    from . import qa

    parts = text.split("\n\n", 1)
    if len(parts) == 1:
        return text
    head, tail = parts
    kept = []
    for sentence in re.split(r"([.!?])", tail):
        if sentence.strip() and qa.PEOPLE.search(sentence):
            continue
        kept.append(sentence)
    return head + "\n\n" + "".join(kept)


PERSON_QUERIES = ["a person", "a man", "a woman"]


def _iou(box_a: list[float], box_b: list[float]) -> float:
    ix = max(0.0, min(box_a[0] + box_a[2], box_b[0] + box_b[2]) - max(box_a[0], box_b[0]))
    iy = max(0.0, min(box_a[1] + box_a[3], box_b[1] + box_b[3]) - max(box_a[1], box_b[1]))
    inter = ix * iy
    union = box_a[2] * box_a[3] + box_b[2] * box_b[3] - inter
    return inter / union if union > 0 else 0.0


def added_people(input_img: Image.Image, redraw_img: Image.Image,
                 threshold: float = 0.3, input_threshold: float = 0.2) -> list[list[float]]:
    """Boxes [x, y, w, h] of people the redraw added: confident detections in the redraw that no
    confident detection in the input matches (the input box contains the redraw box's centre, or the
    two boxes' IoU is > 0.3). Both images are the same size."""
    from .. import detect

    red = [d for d in detect.detect(redraw_img, PERSON_QUERIES) if d["score"] >= threshold]
    inp = [d for d in detect.detect(input_img, PERSON_QUERIES) if d["score"] >= input_threshold]
    added: list[list[float]] = []
    for d in red:
        x, y, w, h = d["box"]
        cx, cy = x + w / 2, y + h / 2
        if any(ix <= cx <= ix + iw and iy <= cy <= iy + ih or _iou(d["box"], ib) > 0.3
               for ix, iy, iw, ih in (e["box"] for e in inp)):
            continue
        added.append([x, y, w, h])
    return added


def cleanup_alpha(boxes: list[list[float]], size: tuple[int, int], grow: float = 0.15,
                  feather: int = 8) -> "np.ndarray":
    """Alpha (float32, shape (height, width), [0, 1]) for compositing the cleaned redraw back: 1
    inside each box grown by `grow` times its width/height on each side, falling off linearly to 0
    over `feather` pixels outside it (alpha = clip(1 - d / feather, 0, 1), the maximum over boxes)."""
    import numpy as np

    width, height = size
    ys, xs = np.mgrid[0:height, 0:width]
    alpha = np.zeros((height, width), dtype=np.float32)
    for x, y, w, h in boxes:
        x0 = max(0.0, x - grow * w)
        y0 = max(0.0, y - grow * h)
        x1 = min(width, x + w + grow * w)
        y1 = min(height, y + h + grow * h)
        # distance from each pixel to the grown box: 0 inside, else the distance to its border
        dx = np.maximum(x0 - xs, np.maximum(0.0, xs - (x1 - 1)))
        dy = np.maximum(y0 - ys, np.maximum(0.0, ys - (y1 - 1)))
        d = np.hypot(dx, dy)
        alpha = np.maximum(alpha, np.clip(1.0 - d / feather, 0.0, 1.0))
    return alpha


def prompt_for(cfg: Config, scene: int, name: str) -> str:
    """The scene's visual brief, the base level's instruction, then each of the variant's extra
    sentences, in order."""
    text = (brief.out_dir(cfg, scene) / "visual_brief.txt").read_text(encoding="utf-8").strip()
    var = variant(cfg, name)
    prompt = f"{text}\n\n{imagegen.level_prompt(var['level'])}"
    for key in var["extras"]:
        prompt += " " + imagegen.PROMPT_EXTRAS[key]
    return prompt


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene, backend, level = int(params["scene"]), params["backend"], params["level"]
    target = job_dir(cfg, scene, backend, level)
    if common.up_to_date(target, fingerprint):
        return json.loads((target / "record.json").read_text(encoding="utf-8"))
    size = size_for(cfg, scene)
    source = input_path(cfg, scene)
    # Always rebuild: a cached input outlives a re-prepared plate (iteration 2 redrew stale plates with people).
    imagegen.resize_to(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate_dedither.png", source, size)
    var = variant(cfg, level)
    prompt = prompt_for(cfg, scene, level)
    masked_pixels = 0
    if var["mask_people"]:
        import cv2

        import numpy as np

        rgb = np.asarray(Image.open(source).convert("RGB"))
        mask = people_mask(cfg, scene, size)
        prompt = strip_people(prompt) + " The set is empty: there are no people in it."
    tmp = common.scratch_dir(cfg, f"redraw-{scene}-{backend}-{level}")
    if var["mask_people"]:
        masked_pixels = int((mask > 0).sum())
        cv2.inpaint(rgb, mask, 5, cv2.INPAINT_TELEA)
        masked = tmp / "input_masked.png"
        Image.fromarray(rgb).save(masked)
        source = masked
    try:
        record = imagegen.generate(cfg, backend, prompt=prompt, images=[source], size=size,
                                   seed=int(params.get("seed", 7)), out=tmp / "redraw.png", level=level,
                                   settings=request_settings(cfg, imagegen.backend(cfg, backend), level),
                                   log_dir=tmp)
        record["mask_people"] = var["mask_people"]
        if var["mask_people"]:
            record["masked_pixels"] = masked_pixels
        if var["remove_people"]:
            # People the redraw added (not in the input) get painted out with one more edit of the redraw.
            boxes = added_people(Image.open(source).convert("RGB"), Image.open(tmp / "redraw.png").convert("RGB"))
            if boxes:
                import numpy as np

                raw_path = tmp / "redraw_raw.png"
                os.replace(tmp / "redraw.png", raw_path)
                cleanup = imagegen.generate(cfg, backend, prompt=imagegen.REMOVE_PEOPLE_PROMPT,
                                            images=[raw_path], size=size, seed=int(params.get("seed", 7)),
                                            out=tmp / "cleaned.png", level=level,
                                            settings=request_settings(cfg, imagegen.backend(cfg, backend), level),
                                            log_dir=tmp)
                raw_img = Image.open(raw_path).convert("RGB")
                alpha = cleanup_alpha(boxes, raw_img.size)[:, :, None]  # boxes are in raw-redraw pixels
                raw = np.asarray(raw_img, dtype=np.float32)
                cleaned_img = Image.open(tmp / "cleaned.png").convert("RGB")
                if cleaned_img.size != raw_img.size:  # the server may round the size differently
                    cleaned_img = cleaned_img.resize(raw_img.size, Image.LANCZOS)
                cleaned = np.asarray(cleaned_img, dtype=np.float32)
                redraw = (raw * (1 - alpha) + cleaned * alpha).round().astype(np.uint8)
                Image.fromarray(redraw).save(tmp / "redraw.png")
                record["people_removed"] = len(boxes)
                record["people_boxes"] = boxes
                record["cleanup"] = cleanup
            else:
                record["people_removed"] = 0
        record["status"] = "ok"
    except imagegen.Unavailable as err:
        record = {"backend": backend, "level": level, "status": "unavailable", "reason": str(err)}
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    record.update({"scene": scene, "prompt": prompt})
    common.write_json(tmp / "record.json", record)
    common.write_stamp(tmp, fingerprint, scene=scene, backend=backend, level=level)
    common.install_dir(tmp, target)
    return {k: record.get(k) for k in ("status", "reason", "seconds", "peak_memory_gb", "where", "output_size")}
