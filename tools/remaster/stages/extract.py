"""`extract`: run the existing extractors per scene/resource, then the audit.

Wraps, unchanged:
  tools/extract_rosetattoo_assets.py        -> work/extract/rooms/scene_NN/
  tools/extract_rosetattoo_room_sprites.py  -> work/extract/room-sprites/scene_NNN/
  tools/extract_rosetattoo_canimations.py   -> work/extract/canimations/scene_NNN/
  tools/extract_rosetattoo_sprites.py       -> work/extract/sprites/<resource>/
  tools/audit_rosetattoo_extraction.py      -> work/extract/audit.json

Each tool writes into a scratch directory; the finished scene/resource is
moved into its stable location with a content-hash stamp, so reruns skip
unchanged inputs and an interrupted job never leaves a partial tree.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ..config import Config
from ..jobs import JobSpec
from . import common

NAME = "extract"
SUMMARY = "Existing extractors (backgrounds, room sprites, cAnims, shared sprites) + extraction audit"

KINDS = {
    "rooms": ("extract_rosetattoo_assets.py", "scene_{:02d}"),
    "room-sprites": ("extract_rosetattoo_room_sprites.py", "scene_{:03d}"),
    "canimations": ("extract_rosetattoo_canimations.py", "scene_{:03d}"),
}
# Tools whose source the extraction output depends on (imports included).
DEPENDS = ["extract_rosetattoo_assets.py", "extract_rosetattoo_sprites.py"]


def out_root(cfg: Config) -> Path:
    return cfg.work / "extract"


def scene_dir(cfg: Config, kind: str, scene: int) -> Path:
    return out_root(cfg) / kind / KINDS[kind][1].format(scene)


def sprite_dir(cfg: Config, resource: str) -> Path:
    return out_root(cfg) / "sprites" / resource.replace(".", "_").lower()


def extract_scenes(cfg: Config, scenes: list[int], golden: bool) -> list[int]:
    if golden:
        return sorted(set(scenes) | {scene for scene, _ in cfg.golden_canims})
    return scenes


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    golden = not args.scenes and not args.all
    scenes = extract_scenes(cfg, common.resolve_scenes(cfg, args.scenes, args.all), golden)
    deps = [common.tool(name) for name in DEPENDS]
    specs: list[JobSpec] = []
    for scene in scenes:
        source = common.rrm(cfg, scene)
        if not source.is_file():
            raise SystemExit(f"missing game file {source}; set [paths].game_data in remaster.toml")
        for kind, (script, _) in KINDS.items():
            fp = common.fingerprint(source, common.tool(script), *deps, kind)
            specs.append(JobSpec(NAME, f"{kind}/{scene:03d}", {"kind": kind, "scene": scene}, fp))
    resources = args.resources or (cfg.golden.get("sprites", []) if golden or args.all else [])
    palette_scene = int(cfg.golden.get("sprite_palette_scene", 1))
    for resource in resources:
        fp = common.fingerprint(common.rrm(cfg, palette_scene), cfg.game_data / "VGS.LIB",
                                cfg.game_data / "WALK.LIB", *deps, resource, palette_scene)
        specs.append(JobSpec(NAME, f"sprites/{resource.lower()}",
                             {"kind": "sprites", "resource": resource, "palette_scene": palette_scene}, fp))
    audit_fp = common.fingerprint([s.fingerprint for s in specs], common.tool("audit_rosetattoo_extraction.py"))
    specs.append(JobSpec(NAME, "audit", {"kind": "audit", "scenes": scenes, "resources": resources}, audit_fp,
                         after=[s.id for s in specs]))
    return specs


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    kind = params["kind"]
    if kind in KINDS:
        return _run_scene(cfg, kind, int(params["scene"]), fingerprint)
    if kind == "sprites":
        return _run_sprite(cfg, params["resource"], int(params["palette_scene"]), fingerprint)
    if kind == "audit":
        return _run_audit(cfg, params["scenes"], params.get("resources", []))
    raise ValueError(f"unknown extract job kind {kind}")


def _run_scene(cfg: Config, kind: str, scene: int, fp: str) -> dict:
    target = scene_dir(cfg, kind, scene)
    if common.up_to_date(target, fp):
        return {"skipped": True, "path": str(target)}
    script, pattern = KINDS[kind]
    tmp = common.scratch_dir(cfg, f"extract-{kind}-{scene}")
    try:
        out = tmp / "out"
        common.run_tool(cfg, script, ["--data-dir", cfg.game_data, "--output-dir", out, "--scenes", scene])
        produced = out / pattern.format(scene)
        if not produced.is_dir():
            if kind == "canimations":  # rooms without cAnims (e.g. 9) produce no directory
                produced.mkdir(parents=True)
            else:
                raise RuntimeError(f"{script} produced no {produced.name}")
        common.write_stamp(produced, fp, kind=kind, scene=scene, tool=script)
        common.install_dir(produced, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"path": str(target), "files": sum(1 for _ in target.rglob("*.png"))}


def _run_sprite(cfg: Config, resource: str, palette_scene: int, fp: str) -> dict:
    target = sprite_dir(cfg, resource)
    if common.up_to_date(target, fp):
        return {"skipped": True, "path": str(target)}
    tmp = common.scratch_dir(cfg, f"extract-sprite-{resource}")
    try:
        common.run_tool(cfg, "extract_rosetattoo_sprites.py", [
            "--data-dir", cfg.game_data, "--output-dir", tmp, "--resources", resource,
            "--palette-scene", palette_scene])
        produced = tmp / resource.replace(".", "_").lower()
        if not produced.is_dir():
            raise RuntimeError(f"no output for {resource}")
        common.write_stamp(produced, fp, kind="sprites", resource=resource, palette_scene=palette_scene)
        common.install_dir(produced, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"path": str(target), "frames": sum(1 for _ in target.glob("frame_*.png"))}


def _run_audit(cfg: Config, scenes: list[int], resources: list[str]) -> dict:
    report = out_root(cfg) / "audit.json"
    common.run_tool(cfg, "audit_rosetattoo_extraction.py", [
        "--data-dir", cfg.game_data,
        "--room-source", out_root(cfg) / "room-sprites",
        "--canim-source", out_root(cfg) / "canimations",
        "--scenes", *scenes, "--report", report])
    audit = json.loads(report.read_text(encoding="utf-8"))
    manifest = {
        "stage": NAME,
        "game_data": str(cfg.game_data),
        "scenes": scenes,
        "resources": resources,
        "rooms": {f"{s:03d}": str(scene_dir(cfg, "rooms", s).relative_to(cfg.root)) for s in scenes},
        "audit": {"scenes_checked": audit["scenes_checked"], "scenes_passed": audit["scenes_passed"],
                  "errors": audit["errors"]},
    }
    common.write_json(out_root(cfg) / "manifest.json", manifest)
    if audit["errors"]:
        raise RuntimeError(f"extraction audit errors: {audit['errors']}")
    return manifest["audit"]
