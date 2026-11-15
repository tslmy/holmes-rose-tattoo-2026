"""`sprite-test`: a small identity-conditioned redraw test on Holmes and Watson.

One job per (character, backend) -> work/sprite-test/<character>/<backend>/:
  cast.png           photoreal cast sheet made from the character's front frame
  frames/NNN.png     the walk frames redrawn, each an edit conditioned on the original
                     frame (pose, silhouette) and the cast sheet (identity), fixed seed
  frames/NNN_rgba.png  the redraw cut out with re-segmented alpha (GrabCut seeded by the
                     original silhouette)
  record.json        time and peak memory per image, and the scores below

Scores, each also computed for the de-dithered ESRGAN baseline of the same frames:
  flicker            mean absolute change between consecutive frames inside the figure,
                     divided by the baseline's (the baseline changes only with the motion)
  identity drift     DINOv2 cosine distance of each frame to the first frame and to the cast
                     sheet; spread across the sequence
  pose agreement     IoU of the redraw's silhouette with the original's

This is the Phase 1 probe only; the LoRA and pose-model research is Phase 3.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .. import imagegen
from ..config import Config
from ..jobs import JobSpec
from . import baseline, common, geometry, prepare

NAME = "sprite-test"
SUMMARY = "Identity-conditioned cast sheet + walk redraw on Holmes and Watson, flicker and drift scores"
CODE = [Path(__file__), Path(imagegen.__file__)]
GREY = (128, 128, 128)

CAST_PROMPT = ("A photoreal casting reference sheet for a period drama: the same actor shown three times side "
               "by side, full body, front view, three-quarter view and side view, standing on a plain mid-grey "
               "studio background, even soft light. {description} Same costume, colours and build as the "
               "character in the image; a real person, real fabric.")
FRAME_PROMPT = ("Photograph of the actor from the second image, in exactly the pose, size, position and outline "
                "of the figure in the first image, on the same plain mid-grey background. {description} Keep "
                "the face, costume and colours from the second image. A real person, real fabric, soft light.")


def settings(cfg: Config) -> dict:
    return cfg.section("bakeoff").get("sprite_test", {})


def job_dir(cfg: Config, character: str, backend: str) -> Path:
    return cfg.work / "sprite-test" / character / backend


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    test = settings(cfg)
    specs = []
    seed = int(cfg.section("bakeoff").get("seed", 7))
    for character, spec in test.get("characters", {}).items():
        res = spec["resource"].lower()
        base = baseline.out_root(cfg) / "sprites" / res.replace(".", "_")
        for backend in test.get("backends", []):
            reason = imagegen.availability(cfg, backend)
            fp = common.fingerprint(spec, common.read_stamp(base).get("fingerprint"),
                                    imagegen.backend(cfg, backend), seed, CAST_PROMPT, FRAME_PROMPT,
                                    f"unavailable:{reason}" if reason else "available", *CODE)
            specs.append(JobSpec(NAME, f"{character}/{backend}",
                                 {"character": character, "backend": backend, "seed": seed}, fp,
                                 after=[f"baseline:sprites/{res}"], tags=["image-backend-provisional"]))
    return specs


def fit_canvas(frame: Image.Image, size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Centre an RGBA frame on a grey canvas, scaled to fill 85% of the height; returns placement."""
    w, h = size
    scale = min(0.85 * h / frame.height, 0.9 * w / frame.width)
    fw, fh = round(frame.width * scale), round(frame.height * scale)
    fr = frame.resize((fw, fh), Image.LANCZOS)
    canvas = Image.new("RGBA", size, GREY + (255,))
    x, y = (w - fw) // 2, h - fh - int(0.05 * h)
    canvas.alpha_composite(fr, (x, y))
    return canvas.convert("RGB"), (x, y, fw, fh)


def cutout(redraw: Image.Image, seed_alpha: np.ndarray) -> tuple[Image.Image, np.ndarray]:
    alpha = geometry.resegment(np.asarray(redraw.convert("RGB")), seed_alpha, 2.0)
    rgba = np.dstack([np.asarray(redraw.convert("RGB")), (alpha * 255 + 0.5).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA"), alpha


def sequence_scores(frames: list[np.ndarray], alphas: list[np.ndarray]) -> dict:
    """Mean |frame t+1 - frame t| inside the union of both silhouettes (RGB, 0-255)."""
    deltas = []
    for (a, ma), (b, mb) in zip(zip(frames, alphas), zip(frames[1:], alphas[1:])):
        m = np.maximum(ma, mb) > 0.5
        if m.any():
            deltas.append(float(np.abs(a.astype(np.float32) - b.astype(np.float32))[m].mean()))
    return {"mean_delta": round(float(np.mean(deltas)), 2) if deltas else None,
            "deltas": [round(d, 2) for d in deltas]}


def identity_scores(images: list[Image.Image], cast: Image.Image | None) -> dict:
    from .. import detect

    emb = detect.embed(images + ([cast] if cast is not None else []))
    frames = emb[:len(images)]
    to_first = frames @ frames[0]
    out = {"cos_to_first_min": round(float(to_first.min()), 4),
           "cos_to_first_mean": round(float(to_first[1:].mean()), 4) if len(frames) > 1 else 1.0,
           "drift": round(float(1 - to_first.min()), 4)}
    if cast is not None:
        to_cast = frames @ emb[-1]
        out.update({"cos_to_cast_mean": round(float(to_cast.mean()), 4),
                    "cos_to_cast_spread": round(float(to_cast.max() - to_cast.min()), 4)})
    return out


def on_grey(rgba: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", rgba.size, GREY + (255,))
    canvas.alpha_composite(rgba.convert("RGBA"))
    return canvas.convert("RGB")


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    character, backend = params["character"], params["backend"]
    target = job_dir(cfg, character, backend)
    if common.up_to_date(target, fingerprint):
        return json.loads((target / "record.json").read_text(encoding="utf-8"))["summary"]
    spec = settings(cfg)["characters"][character]
    reason = imagegen.availability(cfg, backend)
    tmp = common.scratch_dir(cfg, f"sprite-{character}-{backend}")
    try:
        if reason:
            summary = {"status": "unavailable", "reason": reason}
            record = {"summary": summary}
        else:
            record = run_test(cfg, spec, backend, int(params.get("seed", 7)), tmp)
            summary = record["summary"]
        common.write_json(tmp / "record.json", record)
        common.write_stamp(tmp, fingerprint, character=character, backend=backend)
        common.install_dir(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return summary


def run_test(cfg: Config, spec: dict, backend: str, seed: int, out: Path) -> dict:
    res = spec["resource"].lower().replace(".", "_")
    base_dir = baseline.out_root(cfg) / "sprites" / res
    description = spec.get("description", "")
    frame_size = tuple(spec.get("frame_size", [384, 768]))
    images: list[dict] = []

    # 1. cast sheet from the front-facing frame (baseline 2x, on grey)
    cast_in, _ = fit_canvas(Image.open(base_dir / f"frame_{spec['cast_frame']:03d}.png").convert("RGBA"),
                            (1536, 1024))
    cast_in.save(out / "cast_input.png")
    rec = imagegen.generate(cfg, backend, prompt=CAST_PROMPT.format(description=description),
                            images=[out / "cast_input.png"], size=(1536, 1024), seed=seed, out=out / "cast.png",
                            log_dir=out / "logs")
    images.append({"what": "cast", **rec})
    cast = Image.open(out / "cast.png").convert("RGB")

    # 2. walk frames: edit conditioned on the frame and the cast sheet
    (out / "frames").mkdir()
    red_cut, red_alpha, base_cut, base_alpha, ious = [], [], [], [], []
    for index in spec["walk"]:
        base_frame = Image.open(base_dir / f"frame_{index:03d}.png").convert("RGBA")
        canvas, (x, y, fw, fh) = fit_canvas(base_frame, frame_size)
        inp = out / "frames" / f"{index:03d}_input.png"
        canvas.save(inp)
        seed_alpha = np.zeros(frame_size[::-1], np.uint8)
        seed_alpha[y:y + fh, x:x + fw] = np.asarray(base_frame.getchannel("A").resize((fw, fh), Image.NEAREST))
        rec = imagegen.generate(cfg, backend, prompt=FRAME_PROMPT.format(description=description),
                                images=[inp, out / "cast.png"], size=frame_size, seed=seed,
                                out=out / "frames" / f"{index:03d}.png", log_dir=out / "logs")
        images.append({"what": f"frame {index}", **rec})
        redraw = Image.open(out / "frames" / f"{index:03d}.png").convert("RGB")
        if redraw.size != frame_size:
            redraw = redraw.resize(frame_size, Image.LANCZOS)
        rgba, alpha = cutout(redraw, seed_alpha)
        rgba.save(out / "frames" / f"{index:03d}_rgba.png")
        red_cut.append(np.asarray(redraw))
        red_alpha.append(alpha)
        base_cut.append(np.asarray(canvas))
        base_alpha.append(seed_alpha.astype(np.float32) / 255)
        seed_b = seed_alpha > 127
        inter = np.logical_and(alpha > 0.5, seed_b).sum()
        union = np.logical_or(alpha > 0.5, seed_b).sum()
        ious.append(round(float(inter / max(union, 1)), 3))

    # 3. scores against the baseline
    red_seq, base_seq = sequence_scores(red_cut, red_alpha), sequence_scores(base_cut, base_alpha)
    crops = lambda arrs: [Image.fromarray(a) for a in arrs]  # noqa: E731
    red_id = identity_scores(crops(red_cut), cast)
    base_id = identity_scores(crops(base_cut), None)
    flicker = (red_seq["mean_delta"] / base_seq["mean_delta"]
               if red_seq["mean_delta"] and base_seq["mean_delta"] else None)
    seconds = [i["seconds"] for i in images]
    peaks = [i.get("peak_memory_gb") for i in images if i.get("peak_memory_gb")]
    summary = {"status": "ok", "frames": len(spec["walk"]),
               "flicker_ratio": round(flicker, 3) if flicker else None,
               "drift": red_id["drift"], "baseline_drift": base_id["drift"],
               "drift_excess": round(red_id["drift"] - base_id["drift"], 4),
               "cos_to_cast_mean": red_id.get("cos_to_cast_mean"),
               "pose_iou_mean": round(float(np.mean(ious)), 3), "pose_iou_min": min(ious),
               "seconds_per_image": round(float(np.mean(seconds)), 1),
               "peak_memory_gb": max(peaks) if peaks else None}
    limits = cfg.section("thresholds")
    summary["pass"] = bool(summary["flicker_ratio"] is not None
                           and summary["flicker_ratio"] <= float(limits.get("sprite_max_flicker_ratio", 1.5))
                           and summary["drift_excess"] <= float(limits.get("sprite_max_drift_excess", 0.1))
                           and summary["pose_iou_min"] >= float(limits.get("sprite_min_pose_iou", 0.7)))
    # contact strip: baseline row over redraw row
    strip_w = frame_size[0] // 2
    strip = Image.new("RGB", (strip_w * len(red_cut), frame_size[1]), GREY)
    for k, (b, r) in enumerate(zip(base_cut, red_cut)):
        strip.paste(Image.fromarray(b).resize((strip_w, frame_size[1] // 2)), (k * strip_w, 0))
        strip.paste(Image.fromarray(r).resize((strip_w, frame_size[1] // 2)), (k * strip_w, frame_size[1] // 2))
    strip.save(out / "strip.jpg", quality=88)
    return {"summary": summary, "images": images, "redraw": {"sequence": red_seq, "identity": red_id},
            "baseline": {"sequence": base_seq, "identity": base_id}, "pose_iou": ious,
            "walk": spec["walk"], "cast_frame": spec["cast_frame"]}
