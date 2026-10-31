#!/usr/bin/env python3
"""Smoke-test Rose Tattoo's initial post-load scene frames in isolated processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


MARKER = b"SHERLOCK_SCENE_SMOKE_RENDERED "
LOG_LIMIT = 1024 * 1024
PILOT_SCENES = (1, 2, 7, 18, 26, 36, 53, 68, 91)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def available_scenes(data_dir: Path) -> list[int]:
    scenes = set()
    for path in data_dir.iterdir():
        match = re.fullmatch(r"RES(\d{2})\.RRM", path.name, re.IGNORECASE)
        if match and path.is_file() and 1 <= int(match.group(1)) < 100:
            scenes.add(int(match.group(1)))
    return sorted(scenes)


def child_environment(scene: int, pack: Path, home: Path, haze_native_fallback: bool = False) -> dict[str, str]:
    # Do not let an interactive/debug session's renderer or SDL settings leak in.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SCUMMVM_", "SDL_", "XDG_"))}
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "TMPDIR": str(home),
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "SDL_RENDER_DRIVER": "software",
        "SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE": str(scene),
        "SCUMMVM_SHERLOCK_TATTOO_SCENE_SMOKE": "1",
        "SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES": str(pack),
        "SCUMMVM_SHERLOCK_TATTOO_HIRES_SCALE": "2",
        "SCUMMVM_SHERLOCK_TATTOO_HIRES_FORMAT": "rgba32",
        "SCUMMVM_SHERLOCK_TATTOO_PALETTE_SAFE_SPRITES": "1",
        "SCUMMVM_SHERLOCK_TATTOO_SPRITE_FILTER": "edge",
    })
    if haze_native_fallback:
        env["SCUMMVM_SHERLOCK_TATTOO_HAZE_NATIVE_FALLBACK"] = "1"
    return env


def run_child(command: list[str], env: dict[str, str], cwd: Path,
              scene: int, timeout: float) -> tuple[str, int | None, bytes, float]:
    """Wait for an exact, complete marker line, an exit, or the deadline.

    The process is stopped after a matching marker; the resulting termination
    code is not a crash. Logs are bounded even if the child is noisy.
    """
    start = time.monotonic()
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    assert proc.stdout is not None
    expected = MARKER + str(scene).encode("ascii")
    pending = b""
    log = bytearray()
    outcome = "timeout"
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = start + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    break
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    # A child can close stdout without exiting; that is not proof
                    # of either success or failure, so honor the same deadline.
                    try:
                        returncode = proc.wait(timeout=max(0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        break
                    outcome = "exited_without_marker" if returncode == 0 else "crash_or_error"
                    break
                log.extend(chunk[:max(0, LOG_LIMIT - len(log))])
                lines = (pending + chunk).split(b"\n")
                pending = lines.pop()
                # Preserve an invalid prefix for overlong partial lines; merely
                # truncating it could turn a suffix into a false marker.
                if len(pending) > 256:
                    pending = b"!"
                if any(line.rstrip(b"\r") == expected for line in lines):
                    # Preserve a crash already observed before we stop the process.
                    returncode = proc.poll()
                    outcome = "crash_or_error" if returncode not in (None, 0) else "rendered"
                    break
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait()
        proc.stdout.close()
    return outcome, proc.returncode, bytes(log), time.monotonic() - start


def inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def write_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scummvm", required=True, type=Path, help="Patched ScummVM executable")
    parser.add_argument("--data-dir", required=True, type=Path, help="Read-only local game data")
    parser.add_argument("--save", required=True, type=Path, help="Existing save file; copied, never modified")
    parser.add_argument("--pack", required=True, type=Path, help="Existing read-only override pack")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for isolated runs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pilot", action="store_true", help="Scenes 1,2,7,18,26,36,53,68,91")
    group.add_argument("--all", action="store_true", help="All RESnn.RRM files in game data")
    group.add_argument("--scenes", nargs="+", type=int, help="Explicit scene numbers")
    parser.add_argument("--timeout", type=float, default=30, help="Per-attempt deadline in seconds")
    parser.add_argument("--sdl-retries", type=int, default=0,
                        help="Retry this host's specific SDL blit error, recording every attempt (0-10)")
    parser.add_argument("--haze-native-fallback", action="store_true",
                        help="Use the engine's opt-in native fallback for the five haze rooms.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    if not 0 <= args.sdl_retries <= 10:
        parser.error("--sdl-retries must be between 0 and 10")
    for name in ("scummvm", "data_dir", "save", "pack", "output_dir"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if not args.scummvm.is_file() or not os.access(args.scummvm, os.X_OK):
        parser.error("--scummvm must be an executable file")
    if not args.save.is_file() or not args.data_dir.is_dir() or not args.pack.is_dir():
        parser.error("--save must be a file and --data-dir and --pack must be directories")
    if not args.output_dir.parent.is_dir() or args.output_dir.exists():
        parser.error("--output-dir must be new, with an existing parent directory")
    for protected in (args.data_dir, args.pack, args.save.parent):
        if inside(args.output_dir, protected):
            parser.error(f"--output-dir must not be within an input directory: {protected}")
    if any("\n" in str(path) or "\r" in str(path) for path in
           (args.data_dir, args.output_dir)):
        parser.error("paths with newlines cannot be represented in ScummVM config")
    available = available_scenes(args.data_dir)
    scenes = list(PILOT_SCENES) if args.pilot else available if args.all else args.scenes
    if not scenes or len(set(scenes)) != len(scenes) or any(s not in available for s in scenes):
        parser.error("scenes must be nonempty, unique, and have a RESnn.RRM in --data-dir")
    args.scenes = scenes
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_save = args.save.read_bytes()  # One immutable snapshot for every scene.
    if not source_save:
        raise SystemExit("The source save is empty")
    binary_hash = sha256(args.scummvm.read_bytes())
    manifest = args.pack / "manifest.json"
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "initial requested post-load scene frame only; not natural entrance or gameplay",
        "scummvm": str(args.scummvm), "scummvm_sha256": binary_hash,
        "data_dir": str(args.data_dir), "source_save": str(args.save),
        "source_save_sha256": sha256(source_save), "pack": str(args.pack),
        "pack_manifest_sha256": sha256(manifest.read_bytes()) if manifest.is_file() else None,
        "timeout_seconds": args.timeout,
        "sdl_retries": args.sdl_retries,
        "selected_scenes": args.scenes,
        "completed": False,
        "random_seed": 1,
        "renderer": {"backend": "surfacesdl", "sdl_video": "dummy", "sdl_audio": "dummy", "sdl_renderer": "software",
                     "hires_scale": 2, "hires_format": "rgba32", "sprite_filter": "edge",
                     "palette_safe_sprites": True, "haze_native_fallback": args.haze_native_fallback},
        "scenes": [],
    }
    args.output_dir.mkdir()  # Never reuse, truncate, or clear a previous run.
    report_file = args.output_dir / "report.json"
    write_report(report_file, report)
    for scene in args.scenes:
        scene_dir = args.output_dir / f"scene-{scene:03d}"
        scene_dir.mkdir()
        attempts = []
        for attempt in range(1, args.sdl_retries + 2):
            run_dir = scene_dir / f"attempt-{attempt:02d}"
            run_dir.mkdir()
            saves = run_dir / "saves"
            saves.mkdir()
            (saves / "rosetattoo.000").write_bytes(source_save)
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
            result, code, output, elapsed = run_child(command, child_environment(scene, args.pack, run_dir,
                                                                                 args.haze_native_fallback),
                                                      run_dir, scene, args.timeout)
            if result == "crash_or_error" and b"SDL_BlitSurface failed: Parameter 'src' is invalid!" in output:
                result = "sdl_blit_error"
            (run_dir / "stdout.log").write_bytes(output)
            attempts.append({"result": result, "process_returncode": code,
                             "elapsed_seconds": round(elapsed, 3),
                             "matched_marker": f"SHERLOCK_SCENE_SMOKE_RENDERED {scene}" if result == "rendered" else None,
                             "stdout_may_be_truncated": len(output) == LOG_LIMIT,
                             "stdout": str((run_dir / "stdout.log").relative_to(args.output_dir))})
            if result != "sdl_blit_error":
                break
        entry = {"scene": scene, "result": result, "attempts": attempts,
                 "hires_background_file_present": (args.pack / f"scene_{scene:03d}" / "background@2x.png").is_file()}
        report["scenes"].append(entry)
        write_report(report_file, report)
        print(f"scene {scene:03d}: {result} ({len(attempts)} attempt(s))", flush=True)
    report["completed"] = True
    write_report(report_file, report)
    return 0 if all(entry["result"] == "rendered" for entry in report["scenes"]) else 1


if __name__ == "__main__":
    sys.exit(main())
