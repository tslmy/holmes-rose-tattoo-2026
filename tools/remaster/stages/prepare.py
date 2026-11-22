"""`prepare`: de-dither, build plates, write structure/edge control images.

Per scene, into work/prepare/scene_NNN/:
  background.png            original background (copy)
  background_dedither.png   de-dithered background
  plate.png                 default-state room sprites composited on the background
                            (all objects, or only single-frame "static" objects when
                            [prepare] plate_layers = "static")
  plate_dedither.png        de-dithered plate (the redraw input)
  plate_layers.png          layer index (0 = background, n = layer n in plate.json)
  plate.json                layers, skipped objects, de-dither settings, dither gauge
  edges.png                 edge/structure control from the de-dithered plate

Per golden sprite resource / cAnim, into work/prepare/sprites/<name>/ and
work/prepare/canimations/scene_NNN/canim_NNN/: de-dithered frames with alpha
unchanged and the extractor's metadata.json copied through.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

from .. import dedither, plates
from ..config import Config
from ..jobs import JobSpec
from . import common, extract

NAME = "prepare"
SUMMARY = "De-dither, build default-state plates, edge control images"
CODE = [Path(dedither.__file__), Path(plates.__file__), Path(__file__)]


def settings(cfg: Config) -> dict:
    section = cfg.section("prepare")
    plate_layers = section.get("plate_layers", "all")
    if plate_layers not in ("all", "static"):
        raise ValueError(f"[prepare] plate_layers must be \"all\" or \"static\", got {plate_layers!r}")
    return {
        "radius": int(section.get("dedither_radius", 1)),
        "plate_layers": plate_layers,
        "threshold": float(section.get("dedither_threshold", 48.0)),
        "iterations": int(section.get("dedither_iterations", 2)),
        "edge_low": float(section.get("edge_low", 40)),
        "edge_high": float(section.get("edge_high", 120)),
    }


def out_root(cfg: Config) -> Path:
    return cfg.work / "prepare"


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    golden = not args.scenes and not args.all
    scenes = common.resolve_scenes(cfg, args.scenes, args.all)
    opts = settings(cfg)
    specs = []
    for scene in scenes:
        rooms = extract.scene_dir(cfg, "rooms", scene)
        sprites = extract.scene_dir(cfg, "room-sprites", scene)
        fp = common.fingerprint(common.read_stamp(rooms).get("fingerprint"),
                                common.read_stamp(sprites).get("fingerprint"), opts, *CODE)
        specs.append(JobSpec(NAME, f"scene/{scene:03d}", {"kind": "scene", "scene": scene}, fp,
                             after=[f"extract:rooms/{scene:03d}", f"extract:room-sprites/{scene:03d}"],
                             tags=["plate-flag-state", "dedither-settings"]))
    if golden or args.all or args.resources:
        for resource in args.resources or cfg.golden.get("sprites", []):
            src = extract.sprite_dir(cfg, resource)
            fp = common.fingerprint(common.read_stamp(src).get("fingerprint"), opts, *CODE)
            specs.append(JobSpec(NAME, f"sprites/{resource.lower()}",
                                 {"kind": "frames", "source": str(src.relative_to(cfg.root)),
                                  "target": str((out_root(cfg) / "sprites" / src.name).relative_to(cfg.root))},
                                 fp, after=[f"extract:sprites/{resource.lower()}"], tags=["dedither-settings"]))
    if golden or args.all:
        for scene, index in cfg.golden_canims:
            src = extract.scene_dir(cfg, "canimations", scene) / f"canim_{index:03d}"
            fp = common.fingerprint(common.read_stamp(src.parent).get("fingerprint"), opts, *CODE)
            target = out_root(cfg) / "canimations" / f"scene_{scene:03d}" / f"canim_{index:03d}"
            specs.append(JobSpec(NAME, f"canim/{scene:03d}-{index:03d}",
                                 {"kind": "frames", "source": str(src.relative_to(cfg.root)),
                                  "target": str(target.relative_to(cfg.root))},
                                 fp, after=[f"extract:canimations/{scene:03d}"], tags=["dedither-settings"]))
    return specs


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    if params["kind"] == "scene":
        return prepare_scene(cfg, int(params["scene"]), fingerprint)
    if params["kind"] == "frames":
        return prepare_frames(cfg, cfg.root / params["source"], cfg.root / params["target"], fingerprint)
    raise ValueError(params)


def prepare_scene(cfg: Config, scene: int, fp: str) -> dict:
    target = out_root(cfg) / f"scene_{scene:03d}"
    if common.up_to_date(target, fp):
        return {"skipped": True}
    rooms = extract.scene_dir(cfg, "rooms", scene)
    sprites = extract.scene_dir(cfg, "room-sprites", scene)
    metadata = json.loads((rooms / "metadata.json").read_text(encoding="utf-8"))
    opts = settings(cfg)
    dd = {k: opts[k] for k in ("radius", "threshold", "iterations")}

    tmp = common.scratch_dir(cfg, f"prepare-{scene}")
    try:
        background = Image.open(rooms / "background.png").convert("RGB")
        background.save(tmp / "background.png")
        bg_dd = dedither.dedither_image(background, **dd)
        bg_dd.save(tmp / "background_dedither.png")

        layers, skipped = plates.plan_layers(metadata, sprites,
                                             static_only=(opts["plate_layers"] == "static"))
        plate, index = plates.build_plate(background, layers)
        plate.save(tmp / "plate.png")
        index.save(tmp / "plate_layers.png")
        plate_dd = dedither.dedither_image(plate, **dd)
        plate_dd.save(tmp / "plate_dedither.png")
        dedither.edge_map(plate_dd, opts["edge_low"], opts["edge_high"]).save(tmp / "edges.png")

        for layer in layers:  # keep paths repo-relative in the record
            layer.source = str(Path(layer.source).resolve().relative_to(cfg.root))
        record = plates.layers_json(layers, skipped, frozenset())
        record.update({
            "scene": scene,
            "scene_name": metadata.get("scene_name", ""),
            "plate_layers": opts["plate_layers"],
            "size": list(background.size),
            "dedither": dd,
            "dither_energy": {
                "background": round(dedither.dither_energy(background), 3),
                "background_dedither": round(dedither.dither_energy(bg_dd), 3),
                "plate": round(dedither.dither_energy(plate), 3),
                "plate_dedither": round(dedither.dither_energy(plate_dd), 3),
            },
        })
        common.write_json(tmp / "plate.json", record)
        common.write_stamp(tmp, fp, scene=scene)
        common.install_dir(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"layers": len(layers), "skipped": len(skipped), "dither_energy": record["dither_energy"]}


def prepare_frames(cfg: Config, source: Path, target: Path, fp: str) -> dict:
    if common.up_to_date(target, fp):
        return {"skipped": True}
    if not (source / "metadata.json").is_file():
        raise FileNotFoundError(source / "metadata.json")
    dd = {k: settings(cfg)[k] for k in ("radius", "threshold", "iterations")}
    tmp = common.scratch_dir(cfg, f"prepare-frames-{source.name}")
    try:
        before = after = 0.0
        frames = sorted(source.glob("frame_*.png"))
        for path in frames:
            image = Image.open(path)
            image = image.convert("RGBA")
            out = dedither.dedither_image(image, **dd)
            out.save(tmp / path.name)
            before += dedither.dither_energy(image)
            after += dedither.dither_energy(out)
        shutil.copy2(source / "metadata.json", tmp / "metadata.json")
        for sidecar in source.glob("*.json"):
            if sidecar.name not in ("metadata.json",) and not sidecar.name.startswith("."):
                shutil.copy2(sidecar, tmp / sidecar.name)
        count = max(len(frames), 1)
        common.write_json(tmp / "prepare.json", {"source": str(source.relative_to(cfg.root)), "dedither": dd,
                                                 "frames": len(frames),
                                                 "dither_energy_mean": [round(before / count, 3),
                                                                        round(after / count, 3)]})
        common.write_stamp(tmp, fp)
        common.install_dir(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"frames": len(frames)}
