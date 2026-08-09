#!/usr/bin/env python3
"""Launch ScummVM for manual Rose Tattoo scene validation."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = [1, 2, 18, 20, 36, 42, 53, 91]
MACOS_APP_BINARY = Path("/Applications/ScummVM.app/Contents/MacOS/scummvm")
MACOS_WINDOW_ID_SWIFT = r"""
import CoreGraphics
import Foundation

let owner = CommandLine.arguments.dropFirst().first?.lowercased() ?? "scummvm"
let options = CGWindowListOption([.optionOnScreenOnly, .excludeDesktopElements])

guard let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
    exit(2)
}

for window in windows {
    let layer = window[kCGWindowLayer as String] as? Int ?? -1
    guard layer == 0 else {
        continue
    }

    let ownerName = (window[kCGWindowOwnerName as String] as? String ?? "").lowercased()
    guard ownerName == owner else {
        continue
    }

    if let windowNumber = window[kCGWindowNumber as String] as? UInt32 {
        print(windowNumber)
        exit(0)
    }
}

exit(1)
"""


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


def macos_window_id(owner: str) -> str | None:
    swift = shutil.which("swift")
    if not swift:
        return None

    result = subprocess.run(
        [swift, "-e", MACOS_WINDOW_ID_SWIFT, owner],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


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
        "--start-scene",
        type=int,
        help=(
            "Start directly in a scene when using the local ScummVM validation "
            "patch. Sets SCUMMVM_SHERLOCK_TATTOO_START_SCENE."
        ),
    )
    parser.add_argument(
        "--asset-overrides",
        type=Path,
        help=(
            "Directory for external Rose Tattoo asset overrides when using the "
            "local ScummVM override patch. Sets SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES."
        ),
    )
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
    parser.add_argument(
        "--capture-after",
        type=float,
        help="After launching ScummVM, wait this many seconds, capture the screen, then stop ScummVM.",
    )
    parser.add_argument(
        "--capture-output",
        type=Path,
        help="Screenshot path for --capture-after. Defaults under the screenshot directory.",
    )
    parser.add_argument(
        "--capture-mode",
        choices=["window", "screen"],
        default="window",
        help="Capture only the ScummVM window by default; use screen for full desktop capture.",
    )
    parser.add_argument(
        "--capture-window-owner",
        default="scummvm",
        help="macOS window owner process name for --capture-mode window.",
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
    if args.start_scene is not None:
        print(f"  SCUMMVM_SHERLOCK_TATTOO_START_SCENE={args.start_scene}")
    if args.asset_overrides is not None:
        print(f"  SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES={args.asset_overrides.resolve()}")
    print("  " + " ".join(cmd))

    if args.print_only:
        return
    env = os.environ.copy()
    if args.start_scene is not None:
        env["SCUMMVM_SHERLOCK_TATTOO_START_SCENE"] = str(args.start_scene)
    if args.asset_overrides is not None:
        env["SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES"] = str(args.asset_overrides.resolve())

    if args.capture_after is None:
        subprocess.run(cmd, check=True, env=env)
        return

    screencapture = shutil.which("screencapture")
    if not screencapture:
        raise SystemExit("--capture-after currently requires macOS screencapture on PATH")

    capture_output = args.capture_output
    if capture_output is None:
        scene_suffix = f"scene-{args.start_scene:03d}" if args.start_scene is not None else "launch"
        capture_output = screenshot_dir / f"desktop-{scene_suffix}.png"
    capture_output = capture_output.resolve()
    capture_output.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(cmd, env=env)
    try:
        import time

        time.sleep(args.capture_after)
        capture_cmd = [screencapture, "-x"]
        if args.capture_mode == "window":
            window_id = macos_window_id(args.capture_window_owner)
            if not window_id:
                raise SystemExit(
                    f"Could not find a visible {args.capture_window_owner!r} window for capture"
                )
            capture_cmd.append(f"-l{window_id}")
        capture_cmd.append(str(capture_output))
        subprocess.run(capture_cmd, check=True)
        print(f"\nCaptured: {capture_output}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    main()
