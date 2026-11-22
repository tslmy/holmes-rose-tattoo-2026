"""One smoke image per image backend in remaster.toml, with time and peak memory.

  uv run python -m remaster.mflux_smoke flux2-klein-edit [--scene 26]

Runs the backend's mflux CLI (installed with `uv tool install mflux`) on the
scene's de-dithered plate under /usr/bin/time -l, and writes the image plus
a JSON record to work/smoke/<backend>/ (game-derived, so not in git).
Peak memory is macOS's "peak memory footprint", which includes MLX's unified
(GPU) allocations, and "maximum resident set size" for comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

from .config import load_config

PROMPT = ("A photograph of a built Victorian film set, real materials, practical lighting, "
          "keep every object and its position, vibrant original colours. ")


def tool_path(name: str) -> str:
    found = shutil.which(name) or str(Path.home() / ".local/share/uv/tools/mflux/bin" / name)
    if not Path(found).exists():
        raise SystemExit(f"{name} not found; run `uv tool install --python 3.13 mflux`")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backend")
    parser.add_argument("--scene", type=int, default=26)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    args = parser.parse_args()

    cfg = load_config()
    backend = cfg.section("image").get("backends", {}).get(args.backend)
    if not backend:
        raise SystemExit(f"no [image.backends.{args.backend}] in remaster.toml")
    scene_dir = cfg.work / "prepare" / f"scene_{args.scene:03d}"
    plate = scene_dir / "plate_dedither.png"
    if not plate.exists():
        raise SystemExit(f"{plate} missing; run `uv run remaster extract prepare` first")
    out_dir = cfg.work / "smoke" / args.backend
    out_dir.mkdir(parents=True, exist_ok=True)
    source = Image.open(plate).convert("RGB")
    if source.width > 640:  # scrolling room: use the first screen
        source = source.crop((0, 0, 640, 480))
    conditioned = out_dir / "input.png"
    source.resize((args.width, args.height), Image.LANCZOS).save(conditioned)
    brief_path = cfg.work / "brief" / f"scene_{args.scene:03d}" / "visual_brief.txt"
    prompt = PROMPT + (brief_path.read_text().strip() if brief_path.exists() else "")
    output = out_dir / "output.png"
    steps = args.steps or int(backend.get("steps", 4))

    cmd = [tool_path(backend["tool"]), "--model", backend["model"], "--base-model", backend["base_model"],
           "--prompt", prompt, "--seed", str(args.seed), "--steps", str(steps),
           "--width", str(args.width), "--height", str(args.height), "--output", str(output)]
    if "controlnet" in backend["tool"]:
        cmd += ["--control", f"canny:{conditioned}:{backend.get('control_strength', 0.8)}"]
    else:
        cmd += ["--image-paths", str(conditioned)]
    cmd += args.extra
    print("$ " + " ".join(cmd[:1] + [c if len(c) < 120 else c[:117] + "..." for c in cmd[1:]]), flush=True)
    started = time.time()
    proc = subprocess.run(["/usr/bin/time", "-l", *cmd], capture_output=True, text=True)
    seconds = time.time() - started
    log = proc.stdout + proc.stderr
    (out_dir / "log.txt").write_text(log)

    def grab(label: str) -> int | None:
        match = re.search(r"(\d+)\s+" + re.escape(label), log)
        return int(match.group(1)) if match else None

    record = {
        "backend": args.backend, "model": backend["model"], "tool": backend["tool"], "scene": args.scene,
        "size": [args.width, args.height], "steps": steps, "seed": args.seed,
        "exit_code": proc.returncode, "seconds": round(seconds, 1),
        "peak_memory_footprint_gb": round((grab("peak memory footprint") or 0) / 1e9, 2),
        "max_rss_gb": round((grab("maximum resident set size") or 0) / 1e9, 2),
        "output": str(output.relative_to(cfg.root)) if output.exists() else None,
        "date": time.strftime("%Y-%m-%d %H:%M"),
    }
    (out_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    if proc.returncode != 0:
        print(log[-3000:], file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
