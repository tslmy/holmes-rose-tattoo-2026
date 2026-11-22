#!/usr/bin/env python3
"""Build a playable Rose Tattoo override pack from the remaster assemble stage's output.

Runs tools/assemble_rosetattoo_playable_pack.py (backgrounds + temporal sprites), then copies each room's
own overrides into sprites/<resource>/, where the runtime reads every scene sprite:
room objects (scene_NNN/room_NNN) and cutscene animations (scene_NNN/canim_NNN). They replace the
temporal frames for those resources.

  python3 tools/build_remaster_pack.py --output-dir generated/playable-overrides-2x-remaster-it8
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_room_overrides(background_dir: Path, pack: Path) -> int:
    count = 0
    for scene_dir in sorted(background_dir.glob("scene_*")):
        for resource in sorted(scene_dir.glob("room_*")) + sorted(scene_dir.glob("canim_*")):
            if not resource.is_dir():
                continue
            target = pack / "sprites" / scene_dir.name / resource.name
            target.mkdir(parents=True, exist_ok=True)
            for frame in sorted(resource.glob("frame_*@2x.png")):
                dest = target / frame.name
                dest.unlink(missing_ok=True)
                # an APFS clone costs no space; fall back to a plain copy elsewhere
                if subprocess.run(["cp", "-c", str(frame), str(dest)], capture_output=True).returncode:
                    shutil.copy2(frame, dest)
                count += 1
    return count


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--background-dir", type=Path, default=ROOT / "work/assemble/backgrounds")
    ap.add_argument("--sprite-dir", type=Path, default=ROOT / "enhanced/sprites-temporal")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    subprocess.run([sys.executable, str(ROOT / "tools/assemble_rosetattoo_playable_pack.py"),
                    "--background-dir", str(args.background_dir), "--sprite-dir", str(args.sprite_dir),
                    "--output-dir", str(args.output_dir)], check=True)
    count = copy_room_overrides(args.background_dir, args.output_dir)
    manifest_path = args.output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["room_override_frames_copied"] = count
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"room/canim override frames: {count}")


if __name__ == "__main__":
    main()
