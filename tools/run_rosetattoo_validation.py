#!/usr/bin/env python3
"""Launch ScummVM for manual Rose Tattoo scene validation."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = [1, 2, 18, 20, 36, 42, 53, 91]
MACOS_APP_BINARY = Path("/Applications/ScummVM.app/Contents/MacOS/scummvm")


def find_scummvm(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    path_scummvm = shutil.which("scummvm")
    if path_scummvm:
        return path_scummvm
    if MACOS_APP_BINARY.exists():
        return str(MACOS_APP_BINARY)
    return None


def command_for(
    scummvm: str,
    data_dir: Path,
    save_dir: Path,
    screenshot_dir: Path,
    *,
    save_slot: int | None,
    window_size: str,
    fullscreen: bool,
) -> list[str]:
    cmd = [
        scummvm,
        f"--path={data_dir}",
        f"--savepath={save_dir}",
        f"--screenshotpath={screenshot_dir}",
        "--aspect-ratio",
        "--stretch-mode=pixel-perfect",
    ]
    if fullscreen:
        cmd.append("--fullscreen")
    else:
        cmd.extend(["--no-fullscreen", f"--window-size={window_size}"])
    if save_slot is not None:
        cmd.append(f"--save-slot={save_slot}")
    cmd.append("sherlock:rosetattoo")
    return cmd


def print_scene_commands(scenes: list[int]) -> None:
    print("\nScene jump commands:")
    for scene in scenes:
        print(f"  scene {scene}")
    print("\nIn ScummVM:")
    print("  1. Start or skip into gameplay.")
    print("  2. Open the debugger with Ctrl+Alt+D.")
    print("  3. Type one of the scene commands above, then press Enter.")
    print("  4. Use Alt+S to save a screenshot to the screenshot directory.")
    print("  5. Type continue to leave the debugger.")
    print("\nUseful Sherlock debugger commands:")
    print("  showall   reveal all map locations")
    print("  scene N   jump to room/scene N")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch ScummVM with Rose Tattoo validation paths and scene-jump notes."
    )
    parser.add_argument("--scummvm", help="Path to a ScummVM executable")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "scummvm")
    parser.add_argument("--save-dir", type=Path, default=ROOT / "validation" / "saves")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=ROOT / "validation" / "screenshots",
    )
    parser.add_argument("--scenes", type=int, nargs="*", default=DEFAULT_SCENES)
    parser.add_argument("--save-slot", type=int, help="Load a numbered ScummVM save slot on start")
    parser.add_argument(
        "--window-size",
        default="1280,960",
        help="Window size passed to ScummVM when not using --fullscreen.",
    )
    parser.add_argument("--fullscreen", action="store_true", help="Start ScummVM fullscreen")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print launch/debugger instructions without starting ScummVM.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    save_dir = args.save_dir.resolve()
    screenshot_dir = args.screenshot_dir.resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    print_scene_commands(args.scenes)
    print(f"\nGame data:   {data_dir}")
    print(f"Saves:       {save_dir}")
    print(f"Screenshots: {screenshot_dir}")

    scummvm = find_scummvm(args.scummvm)
    if not scummvm:
        print("\nNo scummvm executable found on PATH.")
        print("Install ScummVM or pass --scummvm /path/to/scummvm.")
        print("On macOS this is often /Applications/ScummVM.app/Contents/MacOS/scummvm.")
        return

    cmd = command_for(
        scummvm,
        data_dir,
        save_dir,
        screenshot_dir,
        save_slot=args.save_slot,
        window_size=args.window_size,
        fullscreen=args.fullscreen,
    )
    print("\nLaunch command:")
    print("  " + " ".join(cmd))

    if args.print_only:
        return
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
