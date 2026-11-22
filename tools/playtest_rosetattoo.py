#!/usr/bin/env python3
"""Run a scripted, headless Rose Tattoo play-test and collect frame dumps.

The patched ScummVM Sherlock engine reads a command script from
SCUMMVM_SHERLOCK_TATTOO_INPUT_SCRIPT, injects its mouse/keyboard input as
ordinary ScummVM events, and writes PNG dumps of the presented hires frame
plus playtest.jsonl into SCUMMVM_SHERLOCK_TATTOO_DUMP_DIR. This driver sets
up an isolated run (copied save, config, HOME) and reports the result. See
docs/playtest-input-script.md for the script format.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PREFIX = "SHERLOCK_PLAYTEST_"
SDL_BLIT_ERROR = "SDL_BlitSurface failed: Parameter 'src' is invalid!"


def child_environment(args: argparse.Namespace, run_dir: Path, dumps: Path) -> dict[str, str]:
    # Keep an interactive session's renderer, SDL or ScummVM settings out.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SCUMMVM_", "SDL_", "XDG_"))}
    env.update({
        "HOME": str(run_dir),
        "XDG_CONFIG_HOME": str(run_dir / "config"),
        "XDG_DATA_HOME": str(run_dir / "data"),
        "XDG_CACHE_HOME": str(run_dir / "cache"),
        "TMPDIR": str(run_dir),
        "SCUMMVM_SHERLOCK_TATTOO_INPUT_SCRIPT": str(args.script),
        "SCUMMVM_SHERLOCK_TATTOO_DUMP_DIR": str(dumps),
        "SCUMMVM_SHERLOCK_TATTOO_HIRES_SCALE": "2",
        "SCUMMVM_SHERLOCK_TATTOO_HIRES_FORMAT": "rgba32",
        "SCUMMVM_SHERLOCK_TATTOO_PALETTE_SAFE_SPRITES": "1",
        "SCUMMVM_SHERLOCK_TATTOO_SPRITE_FILTER": "edge",
    })
    if args.pack:
        env["SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES"] = str(args.pack)
    if args.scene is not None:
        # Existing post-load hook: jump from the save's room to this scene.
        env["SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE"] = str(args.scene)
    if not args.window:
        env.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "SDL_RENDER_DRIVER": "software"})
    for item in args.env:
        key, _, value = item.partition("=")
        env[key] = value
    return env


def parse_events(output: str) -> list[dict]:
    events = []
    for line in output.splitlines():
        if not line.startswith(PREFIX):
            continue
        kind, _, payload = line[len(PREFIX):].partition(" ")
        try:
            data = json.loads(payload) if payload.startswith("{") else payload
        except json.JSONDecodeError:
            data = payload
        events.append({"event": kind, "data": data})
    return events


def run_once(args: argparse.Namespace, run_dir: Path, save: bytes) -> dict:
    saves = run_dir / "saves"
    dumps = run_dir / "dumps"
    saves.mkdir(parents=True)
    dumps.mkdir()
    (saves / "rosetattoo.000").write_bytes(save)
    config = run_dir / "scummvm.ini"
    config.write_text(
        "[scummvm]\nautosave_period=0\nupdates_check=0\n"
        "[rosetattoo]\nengineid=sherlock\ngameid=rosetattoo\n"
        f"path={args.data_dir}\nplatform=pc\nlanguage=en\nextra=CD\n"
        "fade_style=false\noriginalsaveload=false\n", encoding="utf-8")
    command = [str(args.scummvm), f"--config={config}", f"--path={args.data_dir}",
               f"--savepath={saves}", f"--screenshotpath={run_dir}",
               f"--logfile={run_dir / 'scummvm.log'}", "--gfx-mode=surfacesdl",
               "--no-fullscreen", "--gui-theme=builtin", "--music-driver=null",
               "--random-seed=1", "--save-slot=0"]
    themes = args.scummvm.parent / "gui" / "themes"
    if themes.is_dir():
        command.extend([f"--themepath={themes}", f"--extrapath={themes}"])
    command.append("rosetattoo")

    start = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=run_dir, env=child_environment(args, run_dir, dumps),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, timeout=args.timeout)
        output, code, timed_out = proc.stdout, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        output, code, timed_out = exc.stdout or b"", None, True
    elapsed = time.monotonic() - start
    (run_dir / "stdout.log").write_bytes(output)
    text = output.decode("utf-8", "replace")
    events = parse_events(text)
    kinds = [e["event"] for e in events]

    if timed_out:
        result = "timeout"
    elif "ERROR" in kinds:
        result = "script_error"
    elif code != 0:
        result = "sdl_blit_error" if SDL_BLIT_ERROR in text else "crash_or_error"
    elif "QUIT" in kinds or "END" in kinds:
        result = "passed"
    else:
        result = "exited_without_script"
    return {
        "result": result, "returncode": code, "elapsed_seconds": round(elapsed, 3),
        "run_dir": str(run_dir), "stdout": str(run_dir / "stdout.log"),
        "dumps": [e["data"] for e in events if e["event"] == "DUMP"],
        "errors": [e["data"] for e in events if e["event"] == "ERROR"],
        "events": events,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scummvm", required=True, type=Path, help="Patched ScummVM executable")
    parser.add_argument("--data-dir", required=True, type=Path, help="Local Rose Tattoo game data")
    parser.add_argument("--save", required=True, type=Path, help="Save to load; copied, never modified")
    parser.add_argument("--script", required=True, type=Path, help="Play-test input script")
    parser.add_argument("--pack", type=Path, help="Override pack (omit for native art)")
    parser.add_argument("--scene", type=int, help="Jump to this scene right after loading the save")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for the run")
    parser.add_argument("--timeout", type=float, default=120, help="Per-attempt deadline in seconds")
    parser.add_argument("--sdl-retries", type=int, default=3,
                        help="Retry only the dummy backend's intermittent SDL blit error (0-10)")
    parser.add_argument("--window", action="store_true", help="Use a real window instead of SDL's dummy driver")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                        help="Extra environment for the engine (repeatable)")
    args = parser.parse_args(argv)
    for name in ("scummvm", "data_dir", "save", "script", "pack", "output_dir"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser().resolve())
    if not args.scummvm.is_file() or not os.access(args.scummvm, os.X_OK):
        parser.error("--scummvm must be an executable file")
    if not args.save.is_file() or not args.script.is_file() or not args.data_dir.is_dir():
        parser.error("--save and --script must be files and --data-dir a directory")
    if args.pack is not None and not args.pack.is_dir():
        parser.error("--pack must be a directory")
    if args.output_dir.exists() or not args.output_dir.parent.is_dir():
        parser.error("--output-dir must be new, with an existing parent directory")
    if not 0 <= args.sdl_retries <= 10:
        parser.error("--sdl-retries must be between 0 and 10")
    if any("=" not in item for item in args.env):
        parser.error("--env takes KEY=VALUE")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    save = args.save.read_bytes()
    args.output_dir.mkdir()
    attempts = []
    for attempt in range(1, args.sdl_retries + 2):
        entry = run_once(args, args.output_dir / f"attempt-{attempt:02d}", save)
        attempts.append(entry)
        if entry["result"] != "sdl_blit_error":
            break
    final = attempts[-1]
    report = {
        "scummvm": str(args.scummvm), "data_dir": str(args.data_dir), "save": str(args.save),
        "script": str(args.script), "scene": args.scene, "pack": str(args.pack) if args.pack else None,
        "window": args.window, "result": final["result"], "attempts": attempts,
    }
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{final['result']} after {len(attempts)} attempt(s); report: {args.output_dir / 'report.json'}")
    for dump in final["dumps"]:
        state = dump.get("state", {}) if isinstance(dump, dict) else {}
        canim = state.get("canim")
        canim_text = f" canim {canim['name']}#{canim['number']} frame {canim['frame']}" if canim else ""
        print(f"  dump {dump.get('file')} scene {state.get('scene')} holmes {state.get('holmes')}{canim_text}")
    for error in final["errors"]:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if final["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
