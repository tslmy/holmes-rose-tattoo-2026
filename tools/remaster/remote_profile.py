"""Profile the remote image server (OpenAI-style /v1/images/generations).

  REMASTER_IMAGE_API_KEY=... uv run python -m remaster.remote_profile \
      [--url http://10.0.0.26:8000] [--repeat 3] [--models flux3-image-klein ...]

For every model the server lists at /healthz, times text-to-image and edit
requests (the edit sends the same 1024x768 scene-26 input as the local smoke
test, base64 in `image`) with the local smoke prompt and seed. The first call
per model and mode includes any model load on the server; later calls are warm.
The server reports no memory figures, so only wall-clock time is recorded.
Images and records go to work/smoke/remote/ (game-derived, not in git).
The key is read from the environment, never from the repo.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import time
from pathlib import Path

from PIL import Image

from .config import load_config
from .llm import get_json, post_json
from .mflux_smoke import PROMPT

T2I_PROMPT = ("A photograph of a Victorian billiard room in 1894 London, green baize tables, gas lamps, "
              "dark wood panelling, real materials, practical lighting.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://10.0.0.26:8000")
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--modes", nargs="*", default=["t2i", "edit"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scene", type=int, default=26)
    args = parser.parse_args()

    key = os.environ.get("REMASTER_IMAGE_API_KEY")
    cfg = load_config()
    out_dir = cfg.work / "smoke" / "remote"
    out_dir.mkdir(parents=True, exist_ok=True)
    health = get_json(args.url.rstrip("/") + "/healthz", api_key=key)
    models = args.models or health.get("models", [])

    edit_input = out_dir / "input.png"
    plate = cfg.work / "prepare" / f"scene_{args.scene:03d}" / "plate_dedither.png"
    source = Image.open(plate).convert("RGB")
    if source.width > 640:
        source = source.crop((0, 0, 640, 480))
    source.resize((args.width, args.height), Image.LANCZOS).save(edit_input)
    edit_b64 = base64.b64encode(edit_input.read_bytes()).decode("ascii")

    records = []
    for model in models:
        for mode in args.modes:
            times = []
            for run in range(args.repeat):
                payload = {"prompt": T2I_PROMPT if mode == "t2i" else PROMPT, "model": model,
                           "width": args.width, "height": args.height, "seed": args.seed}
                if mode == "edit":
                    payload["image"] = edit_b64
                started = time.monotonic()
                error = None
                try:
                    response = post_json(args.url.rstrip("/") + "/v1/images/generations", payload, 1800, key)
                except (OSError, RuntimeError) as err:
                    response, error = {}, str(err)[:300]
                seconds = round(time.monotonic() - started, 2)
                output = None
                if response.get("data"):
                    output = out_dir / f"{model}-{mode}.png"
                    output.write_bytes(base64.b64decode(response["data"][0]["b64_json"]))
                    times.append(seconds)
                rec = {"model": model, "mode": mode, "run": run, "seconds": seconds, "error": error,
                       "output": str(output.relative_to(cfg.root)) if output else None}
                print(json.dumps(rec), flush=True)
                records.append(rec)
            if times:
                print(f"== {model} {mode}: first {times[0]} s, warm median "
                      f"{statistics.median(times[1:]) if len(times) > 1 else '-'} s", flush=True)

    summary = {"url": args.url, "size": [args.width, args.height], "seed": args.seed, "scene": args.scene,
               "date": time.strftime("%Y-%m-%d %H:%M"), "runs": records}
    (out_dir / "profile.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if all(not r["error"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
