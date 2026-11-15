"""`baseline`: the de-dithered Real-ESRGAN reference every redraw is compared with.

Per scene, work/baseline/scene_NNN/plate_esrgan.png: the de-dithered plate at
2x (Real-ESRGAN x4plus, resized). It is also the source for writing and clue
regions, which are warped in from it rather than redrawn (docs/remaster-plan.md).

Per golden walk resource, work/baseline/sprites/<name>/frame_NNN.png: the
de-dithered frames at 2x with binary alpha (the sprite test's baseline).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from ..config import Config, TOOLS_DIR
from ..jobs import JobSpec
from . import common, prepare

NAME = "baseline"
SUMMARY = "De-dithered Real-ESRGAN 2x baseline for plates and the sprite-test frames"
CODE = [Path(__file__)]
SCALE = 2


def out_root(cfg: Config) -> Path:
    return cfg.work / "baseline"


def sprite_resources(cfg: Config) -> list[str]:
    test = cfg.section("bakeoff").get("sprite_test", {})
    return [c["resource"] for c in test.get("characters", {}).values()]


def model_path(cfg: Config) -> Path:
    return cfg.root / "models" / "esrgan" / "RealESRGAN_x4plus.pth"


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    for scene in common.resolve_scenes(cfg, args.scenes, args.all):
        prep = prepare.out_root(cfg) / f"scene_{scene:03d}"
        fp = common.fingerprint(common.read_stamp(prep).get("fingerprint"), model_path(cfg), SCALE, *CODE)
        specs.append(JobSpec(NAME, f"scene/{scene:03d}", {"kind": "scene", "scene": scene}, fp,
                             after=[f"prepare:scene/{scene:03d}"]))
    if not args.scenes:
        for resource in sprite_resources(cfg):
            src = prepare.out_root(cfg) / "sprites" / resource.lower().replace(".", "_")
            fp = common.fingerprint(common.read_stamp(src).get("fingerprint"), model_path(cfg), SCALE, *CODE)
            specs.append(JobSpec(NAME, f"sprites/{resource.lower()}",
                                 {"kind": "sprites", "resource": resource}, fp,
                                 after=[f"prepare:sprites/{resource.lower()}"]))
    return specs


def _esrgan(cfg: Config):
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import upscale_rosetattoo_sprites as up  # noqa: E402 - existing tool, used unchanged
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return up, up.load_esrgan_model(model_path(cfg), device), device


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    if params["kind"] == "scene":
        scene = int(params["scene"])
        target = out_root(cfg) / f"scene_{scene:03d}"
        if common.up_to_date(target, fingerprint):
            return {"skipped": True}
        up, model, device = _esrgan(cfg)
        plate = Image.open(prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate_dedither.png").convert("RGB")
        result = up.run_esrgan(model, device, plate, tile_size=256 if plate.width > 640 else 0)
        result = result.resize((plate.width * SCALE, plate.height * SCALE), Image.LANCZOS)
        tmp = common.scratch_dir(cfg, f"baseline-{scene}")
        result.save(tmp / "plate_esrgan.png")
        common.write_stamp(tmp, fingerprint, scene=scene, scale=SCALE, model=model_path(cfg).name)
        common.install_dir(tmp, target)
        return {"size": list(result.size)}
    resource = params["resource"]
    name = resource.lower().replace(".", "_")
    target = out_root(cfg) / "sprites" / name
    if common.up_to_date(target, fingerprint):
        return {"skipped": True}
    up, model, device = _esrgan(cfg)
    src = prepare.out_root(cfg) / "sprites" / name
    tmp = common.scratch_dir(cfg, f"baseline-{name}")
    frames = sorted(src.glob("frame_*.png"))
    for path in frames:
        up.upscale_frame(Image.open(path), SCALE, model, device, alpha_mode="binary").save(tmp / path.name)
    common.write_stamp(tmp, fingerprint, resource=resource, scale=SCALE)
    common.install_dir(tmp, target)
    return {"frames": len(frames)}
