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

The 4x tiled detail pass is Phase 2 work and is not run here.
"""

from __future__ import annotations

import argparse
import json
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
                fp = common.fingerprint(brief_fp, prep_fp, bk, imagegen.level_prompt(level), seed,
                                        f"unavailable:{reason}" if reason else "available", *CODE)
                tags = ["image-backend-provisional", "bakeoff-backend"]
                specs.append(JobSpec(NAME, key(scene, backend, level),
                                     {"scene": scene, "backend": backend, "level": level, "seed": seed}, fp,
                                     after=[f"brief:scene/{scene:03d}", f"prepare:scene/{scene:03d}"], tags=tags))
    return specs


def prompt_for(cfg: Config, scene: int, level: str) -> str:
    text = (brief.out_dir(cfg, scene) / "visual_brief.txt").read_text(encoding="utf-8").strip()
    return f"{text}\n\n{imagegen.level_prompt(level)}"


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene, backend, level = int(params["scene"]), params["backend"], params["level"]
    target = job_dir(cfg, scene, backend, level)
    if common.up_to_date(target, fingerprint):
        return json.loads((target / "record.json").read_text(encoding="utf-8"))
    size = size_for(cfg, scene)
    source = input_path(cfg, scene)
    if not source.exists() or Image.open(source).size != size:
        imagegen.resize_to(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate_dedither.png", source, size)
    prompt = prompt_for(cfg, scene, level)
    tmp = common.scratch_dir(cfg, f"redraw-{scene}-{backend}-{level}")
    try:
        record = imagegen.generate(cfg, backend, prompt=prompt, images=[source], size=size,
                                   seed=int(params.get("seed", 7)), out=tmp / "redraw.png", level=level,
                                   log_dir=tmp)
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
