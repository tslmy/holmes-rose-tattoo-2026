"""Image backends behind one call: local mflux CLIs and the remote image server.

`generate()` runs one image with a backend from `[image.backends.*]` in
remaster.toml and returns a record with wall time and, for local runs, peak
memory (macOS "peak memory footprint" from /usr/bin/time -l, which includes
MLX's unified allocations). `availability()` says why a backend cannot run
(weights not in the Hugging Face cache, CLI missing, no API key, server down)
so the bake-off records the reason instead of retrying.

Freedom levels (docs/remaster-plan.md, design decision 1) map to settings per
backend: `levels = { L0 = {...}, L1 = {...}, L2 = {...} }` in the backend's
table (e.g. `strength` for img2img backends) plus the level instruction that
`level_prompt()` appends to every prompt.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .config import Config
from .llm import get_json, post_json

LEVEL_PROMPTS = {
    "L0": ("Keep the exact camera, framing and perspective, and the position, size and outline of every "
           "object and figure. Change only materials, surface detail and lighting."),
    "L1": ("Keep the camera, framing and layout. Outlines may be refined by a few pixels, but nothing may "
           "move, appear or disappear."),
    "L2": ("Re-photograph the same room as a real built set: perspective and proportions may be corrected and "
           "objects may shift or re-scale a little, but every object and figure stays, in the same "
           "left-to-right and front-to-back order. Add nothing, remove nothing, keep all writing."),
}

KEY_FILE = Path.home() / ".config" / "shcrt" / "image_api_key"


class Unavailable(RuntimeError):
    """The backend cannot run here; recorded as a result, not retried."""


def gen_size(width: int, height: int, megapixels: float = 1.0, multiple: int = 16) -> tuple[int, int]:
    """Output size near `megapixels` with the input's aspect ratio, sides a multiple of 16."""
    scale = (megapixels * 1e6 / (width * height)) ** 0.5
    return (max(multiple, round(width * scale / multiple) * multiple),
            max(multiple, round(height * scale / multiple) * multiple))


def backend(cfg: Config, name: str) -> dict[str, Any]:
    table = cfg.section("image").get("backends", {})
    if name not in table:
        raise KeyError(f"no [image.backends.{name}] in {cfg.path.name}")
    return dict(table[name])


def level_settings(bk: dict[str, Any], level: str) -> dict[str, Any]:
    return dict(bk.get("levels", {}).get(level, {}))


def level_prompt(level: str) -> str:
    return LEVEL_PROMPTS[level]


def api_key(bk: dict[str, Any]) -> str | None:
    env = bk.get("api_key_env", "REMASTER_IMAGE_API_KEY")
    if os.environ.get(env):
        return os.environ[env]
    path = Path(os.path.expanduser(bk.get("api_key_file", str(KEY_FILE))))
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def mflux_tool(name: str) -> str | None:
    found = shutil.which(name) or str(Path.home() / ".local/bin" / name)
    return found if Path(found).exists() else None


def hf_cached(repo: str) -> bool:
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")
    snap = hub / ("models--" + repo.replace("/", "--")) / "snapshots"
    return snap.is_dir() and any(snap.iterdir())


_HEALTH: dict[str, list[str] | str] = {}


def availability(cfg: Config, name: str) -> str | None:
    """None when the backend can run now, else the reason it cannot."""
    try:
        bk = backend(cfg, name)
    except KeyError as err:
        return str(err)
    if bk.get("tool") == "http":
        key = api_key(bk)
        if not key:
            return (f"no API key: set {bk.get('api_key_env', 'REMASTER_IMAGE_API_KEY')} or write it to "
                    f"{bk.get('api_key_file', str(KEY_FILE))}")
        root = bk["url"].split("/v1/")[0]
        if root not in _HEALTH:
            try:
                _HEALTH[root] = list(get_json(root + "/healthz", timeout=10, api_key=key).get("models", []))
            except Exception as err:  # noqa: BLE001 - any failure means "not reachable now"
                _HEALTH[root] = f"server {root} unreachable: {str(err)[:200]}"
        health = _HEALTH[root]
        if isinstance(health, str):
            return health
        if health and bk["model"] not in health:
            return f"server lists {health}, not {bk['model']}"
        return None
    if bk.get("tool", "").startswith("mflux"):
        if not mflux_tool(bk["tool"]):
            return f"{bk['tool']} not installed (uv tool install --python 3.13 mflux)"
        if not hf_cached(bk["model"]):
            return f"weights {bk['model']} not in the Hugging Face cache (deleted or never downloaded)"
        return None
    return f"tool {bk.get('tool')!r} is not an image generation backend"


def _peak(log: str, label: str) -> float | None:
    match = re.search(r"(\d+)\s+" + re.escape(label), log)
    return round(int(match.group(1)) / 1e9, 2) if match else None


def generate(cfg: Config, name: str, *, prompt: str, images: list[Path], size: tuple[int, int], seed: int,
             out: Path, level: str | None = None, log_dir: Path | None = None) -> dict[str, Any]:
    """Run one image; returns {seconds, peak_memory_gb, settings, ...}. Raises Unavailable."""
    reason = availability(cfg, name)
    if reason:
        raise Unavailable(reason)
    bk = backend(cfg, name)
    settings = level_settings(bk, level) if level else {}
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    record: dict[str, Any] = {"backend": name, "model": bk.get("model"), "size": [width, height], "seed": seed,
                              "level": level, "settings": settings}
    if bk.get("tool") == "http":
        payload: dict[str, Any] = {"prompt": prompt, "model": bk["model"], "width": width, "height": height,
                                   "seed": seed}
        if images:
            payload["image"] = base64.b64encode(images[0].read_bytes()).decode("ascii")
        started = time.monotonic()
        response = post_json(bk["url"], payload, float(bk.get("timeout_s", 1800)), api_key(bk))
        record["seconds"] = round(time.monotonic() - started, 2)
        out.write_bytes(base64.b64decode(response["data"][0]["b64_json"]))
        record["peak_memory_gb"] = None  # the server reports none; this Mac's memory is not used
        record["where"] = "remote"
    else:
        steps = int(settings.get("steps", bk.get("steps", 4)))
        cmd = [mflux_tool(bk["tool"]), "--model", bk["model"], "--base-model", bk["base_model"],
               "--prompt", prompt, "--seed", str(seed), "--steps", str(steps),
               "--width", str(width), "--height", str(height), "--output", str(out)]
        if "guidance" in settings or "guidance" in bk:
            cmd += ["--guidance", str(settings.get("guidance", bk.get("guidance")))]
        if "controlnet" in bk["tool"]:
            kind = settings.get("control", bk.get("control", "canny"))
            cmd += ["--control", f"{kind}:{images[0]}:{settings.get('control_strength', 0.8)}"]
        elif "strength" in settings:  # img2img: init image + how strongly it is kept (mflux: 0 = ignored)
            cmd += ["--image", str(images[0]), str(settings["strength"])]
        elif images:
            cmd += ["--image-paths", *map(str, images)]
        cmd += [str(x) for x in bk.get("extra_args", [])]
        started = time.monotonic()
        proc = subprocess.run(["/usr/bin/time", "-l", *cmd], capture_output=True, text=True)
        record["seconds"] = round(time.monotonic() - started, 2)
        log = proc.stdout + proc.stderr
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{out.stem}.mflux.log").write_text(log, encoding="utf-8")
        record["peak_memory_gb"] = _peak(log, "peak memory footprint")
        record["max_rss_gb"] = _peak(log, "maximum resident set size")
        record["steps"] = steps
        record["where"] = "local"
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"{bk['tool']} exited {proc.returncode}: {log.strip()[-1200:]}")
    with Image.open(out) as image:
        record["output_size"] = list(image.size)
    return record


def resize_to(path_in: Path, path_out: Path, size: tuple[int, int], *, background=(128, 128, 128)) -> Path:
    """Write `path_in` resized to `size` as opaque RGB (alpha flattened on `background`)."""
    with Image.open(path_in) as image:
        rgba = image.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, background + (255,))
    flat.alpha_composite(rgba)
    path_out.parent.mkdir(parents=True, exist_ok=True)
    flat.convert("RGB").resize(size, Image.LANCZOS).save(path_out)
    return path_out
