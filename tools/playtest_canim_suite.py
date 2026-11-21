#!/usr/bin/env python3
"""Play every reachable cutscene animation (cAnim) headlessly and compare two override packs.

For each room, a survey run (tools/playtest_rosetattoo.py with a script that only logs `objects`) gives
the hotspots visible from the save. An object's extracted "uses" entries name the verb that plays a cAnim
directly (canim_num != 99, no inventory target). For every such (object, verb) visible in the survey this
tool writes a script: right-click the hotspot, pick the verb, wait for the cAnim, dump two frames of it,
dismiss any text and dump the result. It runs each script with each pack and writes a before/after sheet.

  python3 tools/playtest_canim_suite.py --scummvm scummvm-src/scummvm --data-dir scummvm \\
      --save validation/saves/rosetattoo.000 --survey-dir work/playtest/survey \\
      --pack before=generated/playable-overrides-2x-remaster-it4 \\
      --pack after=generated/playable-overrides-2x-remaster-it5 --output-dir work/playtest/canims-it5

Survey runs are expected at <survey-dir>/room<N>/attempt-*/dumps/playtest.jsonl. Results: one directory
per test and pack, results.json, and sheet-NN.jpg pages (before left, after right).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def survey_objects(survey_dir: Path, scene: int) -> list[dict]:
    logs = sorted(survey_dir.glob(f"room{scene}/attempt-*/dumps/playtest.jsonl"))
    if not logs:
        return []
    for line in logs[-1].read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "OBJECTS":
            return event["data"]["objects"]
    return []


def canim_names(scene: int) -> dict[int, str]:
    names = {}
    for meta in (ROOT / "work/extract/canimations" / f"scene_{scene:03d}").glob("canim_*/metadata.json"):
        anim = json.loads(meta.read_text(encoding="utf-8"))["animation"]
        names[int(anim["index"])] = anim.get("name", "")
    return names


def candidates(survey_dir: Path) -> list[dict]:
    """(scene, hotspot, verb, cAnim) for every direct cAnim trigger visible in the survey."""
    tests = []
    for meta in sorted((ROOT / "work/extract/rooms").glob("scene_*/metadata.json")):
        room = json.loads(meta.read_text(encoding="utf-8"))
        scene = int(room["scene_id"])
        visible = [o for o in survey_objects(survey_dir, scene) if o.get("type") == 6]
        if not visible:
            continue
        names = canim_names(scene)
        seen = set()
        for obj in room.get("objects") or []:
            for use in obj.get("uses") or []:
                verb, target, number = use.get("verb") or "", use.get("target") or "", use.get("canim_num")
                if not verb or target or number in (None, 99, 0):
                    continue
                number -= 1  # the room file's cAnim number is 1-based; the engine's (and the override's) 0-based
                if number not in names:
                    continue
                hits = [o for o in visible if o["name"].lower() == (obj.get("name") or "").lower()]
                if not hits or (scene, hits[0]["name"], verb) in seen:
                    continue
                seen.add((scene, hits[0]["name"], verb))
                x0, y0, x1, y1 = hits[0]["screen_rect"]
                if (x0 + x1) // 2 >= 640:
                    continue  # off screen in a scrolling room: needs a scroll first (not scripted yet)
                tests.append({"scene": scene, "object": hits[0]["name"], "verb": verb, "canim": number,
                              "canim_name": names[number], "point": [(x0 + x1) // 2, (y0 + y1) // 2]})
    return tests


def script_for(test: dict) -> str:
    x, y = test["point"]
    return "\n".join([
        f"# {test['object']} -> {test['verb']} plays cAnim {test['canim']} ({test['canim_name']})",
        f"waitscene {test['scene']}", "waitidle 30000", "dump 01_room",
        f"rclick {x} {y}", "waitframes 6", f"verb {test['verb']}",
        "waitcanim 20000", "waitframes 6", "dump 02_canim_early", "waitframes 12", "dump 03_canim_later",
        "waitframes 90", "dump 04_after", "quit", ""])


def slug(test: dict) -> str:
    return f"room{test['scene']:02d}_" + re.sub(r"[^A-Za-z0-9]+", "-", f"{test['object']}_{test['verb']}").strip("-")


def run(args, test: dict, label: str, pack: Path, script: Path) -> dict:
    out = args.output_dir / slug(test) / label
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "tools/playtest_rosetattoo.py"), "--scummvm", str(args.scummvm),
           "--data-dir", str(args.data_dir), "--save", str(args.save), "--pack", str(pack),
           "--scene", str(test["scene"]), "--script", str(script), "--output-dir", str(out),
           "--sdl-retries", "10"]  # the dummy backend's startup blit error can hit several attempts in a row
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dumps = {}
    played = set()
    error = None
    for log in sorted(out.glob("attempt-*/dumps/playtest.jsonl"))[-1:]:
        for line in log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["event"] == "DUMP":
                dumps[event["data"]["name"]] = event["data"]["file"]
                canim = (event["data"].get("state") or {}).get("canim")
                if canim:
                    played.add(f"{canim.get('number')}:{canim.get('name')}")
            elif event["event"] == "ERROR":
                error = event["data"]
    # the extracted canim_num may not be the engine's number: record whatever cAnim actually played
    return {"exit": proc.returncode, "dumps": dumps, "canim_played": bool(played), "played": sorted(played),
            "error": error}


def sheets(results: list[dict], labels: list[str], out_dir: Path, per_page: int = 6) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    rows = []
    for r in results:
        if not all(r["runs"][lb]["canim_played"] for lb in labels):
            continue
        for name in ("02_canim_early", "03_canim_later"):
            if not all(name in r["runs"][lb]["dumps"] for lb in labels):
                continue
            tiles = [Image.open(r["runs"][lb]["dumps"][name]).convert("RGB") for lb in labels]
            w = 480
            h = w * tiles[0].height // tiles[0].width
            row = Image.new("RGB", (len(tiles) * (w + 8), h + 22), (24, 24, 24))
            for i, t in enumerate(tiles):
                row.paste(t.resize((w, h)), (i * (w + 8), 22))
            shown = ",".join(r["runs"][labels[-1]]["played"])
            ImageDraw.Draw(row).text((4, 2), f"Room {r['scene']} {r['object']} > {r['verb']}: {shown} "
                                     f"({name[3:]}); " + " | ".join(labels), fill=(230, 230, 230), font=font)
            rows.append(row)
    pages = []
    for p in range(0, len(rows), per_page):
        chunk = rows[p:p + per_page]
        page = Image.new("RGB", (max(r.width for r in chunk), sum(r.height + 6 for r in chunk)), (24, 24, 24))
        y = 0
        for r in chunk:
            page.paste(r, (0, y))
            y += r.height + 6
        path = out_dir / f"sheet-{p // per_page + 1:02d}.jpg"
        page.save(path, quality=80)
        pages.append(path)
    return pages


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scummvm", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--save", type=Path, required=True)
    ap.add_argument("--survey-dir", type=Path, required=True)
    ap.add_argument("--pack", action="append", required=True, metavar="LABEL=DIR",
                    help="Pack to test, as label=path; give one or more (sheet columns follow this order)")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--scenes", type=int, nargs="*", help="Only these scenes")
    ap.add_argument("--list", action="store_true", help="Only list the tests")
    args = ap.parse_args()
    args.scummvm = args.scummvm.resolve()
    packs = [(p.split("=", 1)[0], Path(p.split("=", 1)[1]).resolve()) for p in args.pack]
    tests = [t for t in candidates(args.survey_dir) if not args.scenes or t["scene"] in args.scenes]
    if args.list:
        for t in tests:
            print(json.dumps(t))
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    scripts = args.output_dir / "scripts"
    scripts.mkdir()
    results = []
    for test in tests:
        script = scripts / f"{slug(test)}.txt"
        script.write_text(script_for(test), encoding="utf-8")
        runs = {label: run(args, test, label, pack, script) for label, pack in packs}
        results.append({**test, "runs": runs})
        played = ", ".join(f"{lb}:{','.join(r['played']) or 'none'}" for lb, r in runs.items())
        print(f"{slug(test)} {test['canim_name']}: {played}", flush=True)
    (args.output_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    for page in sheets(results, [lb for lb, _ in packs], args.output_dir):
        print(page)


if __name__ == "__main__":
    main()
