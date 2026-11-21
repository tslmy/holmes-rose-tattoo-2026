#!/usr/bin/env python3
"""Headless play-tests for what the per-room and cAnim checks miss: exits, the overhead map, the
inventory and scrolling rooms.

Test kinds, generated from the room files (exit table, walk zones) and an existing per-room survey
(<survey-dir>/room<N>/attempt-*/dumps/playtest.jsonl, see docs/playtest-input-script.md). Every room test
first walks Holmes onto the nearest free floor: the scene jump keeps his position from the save, often off
this room's floor, and his first walk from there can fail.

* exit     Click a free floor point inside each exit (arrow) zone (the engine ignores exits under a
           hotspot) and follow Holmes out: dumps while he walks, then the first frame of the next room
           (`waitscene`), a frame shortly after and the settled room. Exits to the overhead map (scenes
           90/100) dump the map instead. Zones off the first screen of a scrolling room are reached by
           walking Holmes to that side first.
* door     The same for an object verb whose use list changes scene (`*NN` names, e.g. Door > Open).
           If the verb menu lacks the verb (a person or another hotspot under the click), other points
           of the object are tried.
* scroll   Rooms wider than 640 px: walk Holmes to the right end, then the left end, over free floor,
           dumping each step. The expected scroll comes from the engine's rule (see walk_plan()).
* map      Leave Baker Street for the map, dump it, scroll it with the mouse and pick a location.
* inventory Right-click empty floor, choose Inventory, dump, close by clicking outside.
* journal  Right-click Watson where the save has him standing, choose Journal, dump.

  uv run python tools/playtest_ui_suite.py --scummvm scummvm-src/scummvm --data-dir scummvm \\
      --save validation/saves/rosetattoo.000 --survey-dir work/playtest/survey \\
      --pack it5=generated/playable-overrides-2x-remaster-it5 --output-dir work/playtest/ui-it5

Results: one directory per test and pack, results.json (with automatic checks: reached the expected
scene, scroll range, near-black dumps) and sheet-NN.jpg pages (one row per test and pack).

Limits of the script language this works around (see docs/playtest-input-script.md): `key` cannot
trigger engine actions (F1 journal, Tab inventory, Home/End on the map), `objects` does not list people
(Watson's position is hard-coded), and nothing waits for the map to appear (it is sampled).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

MAP_SCENES = (90, 100)
SCREEN_W, SCREEN_H = 640, 480
# Overhead map locations (WALK.LIB map.txt): scene -> world position of the icon. The map is 1280x960.
# Only locations whose icon the default save shows (Diogenes Club, for one, is not unlocked there).
MAP_PICKS = {1: ("221B Baker St.", 294, 156), 4: ("Scotland Yard", 160, 612)}
# Where the default save has Watson standing (screen point that opens his verb menu with "Journal").
JOURNAL_WATSON = {24: (304, 237)}
# waitidle returns once Holmes stops, but the room keeps scrolling towards its target for a while.
SCROLL_SETTLE = "waitframes 45"


# --- room data ----------------------------------------------------------------------------------

def room_layout(rrm: Path) -> dict:
    """Scroll width, walk zones and exit zones of a room file (layout as in Scene::loadScene())."""
    import extract_rosetattoo_assets as x

    r = x.Reader(rrm.read_bytes())
    r.seek(39)
    compressed = r.u8() > 0
    r.seek(r.u32())
    header = x.parse_header(r)

    def block(size: int) -> bytes:
        return x.decompress_lz(r, size) if compressed else r.read(size)

    r.read(x.PALETTE_SIZE)
    block((SCREEN_W + header.scroll_size) * SCREEN_H)
    bg_info = [{"filesize": r.u32(), "max_frames": r.u8(), "filename": x.c_string(r.read(9))}
               for _ in range(header.num_structs)]
    for size in (header.num_structs * x.OBJECT_RECORD_SIZE, header.desc_size, header.seq_size):
        block(size)
    x.skip_image_chunks(r, bg_info, header.num_images, compressed)
    x.skip_canim_table(r, header.num_animations, compressed)
    zones = x.parse_walk_zones(r, compressed)
    r.read(2 * len(zones) * len(zones))  # walk directory
    block(r.u16())  # walk points
    r.read(6)  # entrance
    r.read(10 * r.u8())  # scale zones
    exits = []
    for _ in range(r.u8()):  # Exit::load(), Rose Tattoo layout
        dest = x.c_string(r.read(41))
        left, top, width, height = r.s16(), r.s16(), r.u16(), r.u16()
        image, scene = r.u8(), r.s16()
        new_pos = [r.s16(), r.s16(), r.u16()]
        r.s16()  # allow
        exits.append({"dest": dest, "rect": [left, top, left + width, top + height], "scene": scene,
                      "image": image, "new_pos": new_pos})
    return {"scroll_size": header.scroll_size, "zones": zones, "exits": exits}


def survey(survey_dir: Path, scene: int) -> dict | None:
    """Hotspots (scene coordinates) and the start state of a room from its survey run."""
    logs = sorted(survey_dir.glob(f"room{scene}/attempt-*/dumps/playtest.jsonl"))
    if not logs:
        return None
    objects, state = None, None
    for line in logs[-1].read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["event"] == "OBJECTS":
            objects = event["data"]["objects"]
        elif event["event"] == "DUMP" and state is None:
            state = event["data"]["state"]
    if objects is None or state is None or state.get("scene") != scene:
        return None
    sx = state["scroll"][0]
    for o in objects:
        x0, y0, x1, y1 = o["screen_rect"]
        o["rect"] = [x0 + sx, y0, x1 + sx, y1]
    return {"objects": objects, "scroll": sx, "holmes": state["holmes"], "menu_mode": state.get("menu_mode", 0)}


def inside(rect, x, y) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def walkable(zones, x, y) -> bool:
    return any(z["x"] <= x < z["x"] + z["w"] and z["y"] <= y < z["y"] + z["h"] for z in zones)


def free_point(rect, hot, zones, width, exits=()):
    """A point in rect, not on a hotspot or another exit; prefer walkable floor, then the centre."""
    x0, y0 = max(rect[0], 0), max(rect[1], 0)
    x1, y1 = min(rect[2], width), min(rect[3], SCREEN_H)
    if x1 <= x0 or y1 <= y0:
        return None
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    best = None
    steps = 12
    for i in range(steps + 1):
        for j in range(steps + 1):
            x = int(x0 + (x1 - 1 - x0) * i / steps)
            y = int(y0 + (y1 - 1 - y0) * j / steps)
            if any(inside(h, x, y) for h in hot) or any(inside(e, x, y) for e in exits):
                continue
            score = (0 if walkable(zones, x, y) else 1000) + abs(x - cx) + abs(y - cy) / 2
            if best is None or score < best[0]:
                best = (score, x, y)
    return None if best is None else (best[1], best[2])


def floor_point(zones, x, y, avoid, box=None):
    """The walkable point nearest (x, y), inside box (x0, y0, x1, y1) if given, that no avoid rect holds."""
    best = None
    for z in zones:
        zx0, zy0, zx1, zy1 = z["x"], z["y"], z["x"] + z["w"] - 1, z["y"] + z["h"] - 1
        if box:
            zx0, zy0, zx1, zy1 = max(zx0, box[0]), max(zy0, box[1]), min(zx1, box[2] - 1), min(zy1, box[3] - 1)
        if zx1 < zx0 or zy1 < zy0:
            continue
        xs = {min(max(x, zx0), zx1), *range(zx0, zx1 + 1, 12), zx1}
        ys = {min(max(y, zy0), zy1), (zy0 + zy1) // 2}
        for px in xs:
            for py in ys:
                if any(inside(a, px, py) for a in avoid):
                    continue
                d = abs(px - x) + abs(py - y)
                if best is None or d < best[0]:
                    best = (d, px, py)
    return None if best is None else (best[1], best[2])


def extreme_floor_point(zones, avoid, box, side, y):
    """The walkable point in box furthest to one side (ties: nearest y) that no avoid rect holds."""
    best = None
    for z in zones:
        zx0, zy0 = max(z["x"], box[0]), max(z["y"], box[1])
        zx1, zy1 = min(z["x"] + z["w"] - 1, box[2] - 1), min(z["y"] + z["h"] - 1, box[3] - 1)
        if zx1 < zx0 or zy1 < zy0:
            continue
        for px in {*range(zx0, zx1 + 1, 6), zx1}:
            for py in {min(max(y, zy0), zy1), (zy0 + zy1) // 2}:
                if any(inside(a, px, py) for a in avoid):
                    continue
                key = (px if side == "right" else -px, -abs(py - y))
                if best is None or key > best[0]:
                    best = (key, (px, py))
    return None if best is None else best[1]


def walk_plan(zones, avoid, start, scroll, ssize, side, width):
    """Clicks that walk Holmes to one end of a scrolling room, each with the scroll at click time, and the
    scroll they should leave. Walking right past screen x 480 (left past screen x 80) scrolls the room
    (TattooUserInterface); measured in play, the scroll then settles with Holmes about 324 px from the
    left (316 px from the right) screen edge, clamped to the room. So walking cannot scroll fully to an
    end whose free floor stops short of the edge."""
    clicks, (x, y), s = [], start, scroll
    for _ in range(8):
        p = extreme_floor_point(zones, avoid, (max(s + 8, 0), 0, min(s + 632, width), SCREEN_H), side, y)
        if p is None or (p[0] <= x + 8 if side == "right" else p[0] >= x - 8):
            break
        clicks.append((p, s))
        if side == "right" and p[0] - s > 500:
            s = min(max(s, p[0] - 324), ssize)
        elif side == "left" and p[0] - s < 60:
            s = max(min(s, p[0] - 316), 0)
        x, y = p
    return clicks, s, (x, y)


def click_lines(clicks, tag: str) -> list[str]:
    lines = []
    for i, ((x, y), s) in enumerate(clicks):
        lines += [f"click {x - s} {y}", "waitframes 10", "waitidle 30000", SCROLL_SETTLE, f"log {tag}_{i + 1}"]
    return lines


# --- scripts ------------------------------------------------------------------------------------

def follow_lines(dest: int) -> list[str]:
    """After the click that leaves the room: dumps while walking, then the next room or the map."""
    lines = []
    for t in (20, 40, 60):
        lines += ["waitframes 20", f"dump 1{t // 20}_t{t:03d}"]
    if dest in MAP_SCENES:
        # No wait exists for the map (its loop never satisfies waitscene/waitidle): sample it.
        for t in (120, 180, 240, 300, 360, 420):
            lines += ["waitframes 60", f"dump 2{t // 60}_t{t:03d}"]
        lines += ["move 320 240", "waitframes 30", "dump 30_map"]
    else:
        lines += [f"waitscene {dest} 40000", "dump 30_arrive", "waitframes 15", "dump 31_arrive_15f",
                  "waitidle 30000", "dump 32_settled"]
    return lines


def build_tests(args) -> list[dict]:
    tests = []
    for meta in sorted((ROOT / "work/extract/rooms").glob("scene_*/metadata.json")):
        room = json.loads(meta.read_text(encoding="utf-8"))
        scene = int(room["scene_id"])
        if args.scenes and scene not in args.scenes:
            continue
        info = survey(args.survey_dir, scene)
        if info is None:
            continue
        layout = room_layout(Path(room["source"]) if Path(room["source"]).is_file()
                             else args.data_dir / f"RES{scene:02d}.RRM")
        zones = layout["zones"]
        width = SCREEN_W + layout["scroll_size"]
        hot = [o["rect"] for o in info["objects"]]
        exit_rects = [e["rect"] for e in layout["exits"]]
        s0, ssize = info["scroll"], layout["scroll_size"]
        head = [f"waitscene {scene} 30000", "waitidle 30000", "dump 01_room"]
        # The scene jump keeps Holmes where the save had him, often off this room's floor. He can still walk
        # from there; walk plans start from the nearest floor point. (Walking him onto the floor first broke
        # exits: standing at the floor point nearest an exit, a click on it starts no walk, so it never fires.)
        hx, hy = info["holmes"]
        home = floor_point(zones, hx, hy, hot + exit_rects, (s0 + 8, 0, s0 + 632, SCREEN_H)) or (hx, hy)
        settle = []
        if info["menu_mode"] not in (0, 20):
            # After the post-load scene jump some rooms sit in talk mode with no window, ignoring all input.
            tests.append({"kind": "room", "scene": scene, "name": "talk-mode", "label": "room",
                          "skip": f"room opens in menu mode {info['menu_mode']} (talk, no window) after the "
                                  "scene jump and ignores input"})
            continue
        holmes_rect = [home[0] - 25, home[1] - 110, home[0] + 25, home[1] + 2]
        avoid = hot + exit_rects

        def reach(x: int) -> tuple[list[str], int] | None:
            """Lines that bring scene x on screen, and the scroll they should leave."""
            if s0 <= x < s0 + SCREEN_W:
                return [], s0
            if not ssize:
                return None
            clicks, s, _ = walk_plan(zones, avoid, home, s0, ssize, "right" if x >= s0 + SCREEN_W - 8 else "left",
                                     width)
            if not s + 8 <= x < s + SCREEN_W - 8:
                return None
            return click_lines(clicks, "scroll") + ["dump 02_scrolled"], s

        for i, ex in enumerate(layout["exits"]):
            others = [r for j, r in enumerate(exit_rects) if j != i]
            r = ex["rect"]
            point = (floor_point(zones, (r[0] + r[2]) // 2, (r[1] + r[3]) // 2, hot + others, r)
                     or free_point(r, hot, zones, width, others))
            test = {"kind": "exit", "scene": scene, "name": f"exit{i}", "dest": ex["scene"],
                    "label": ex["dest"] or f"exit {i}", "rect": r, "new_pos": ex["new_pos"]}
            if point is None:
                cover = [o["name"] for o in info["objects"] if o["rect"][0] < r[2] and o["rect"][2] > r[0]
                         and o["rect"][1] < r[3] and o["rect"][3] > r[1]]
                tests.append({**test, "skip": "exit zone fully covered by hotspots (" + ", ".join(cover) + ")"})
                continue
            pre = reach(point[0])
            if pre is None:
                tests.append({**test, "skip": "exit zone off screen and not reachable by scrolling"})
                continue
            lines, sx = pre
            tests.append({**test, "point": list(point), "scripts": [head + settle + lines
                          + [f"click {point[0] - sx} {point[1]}"] + follow_lines(ex["scene"])]})

        seen = set()
        for obj in room.get("objects") or []:
            for use in obj.get("uses") or []:
                codes = [n for n in use.get("names") or [] if re.match(r"^\*\d", n)]
                if not codes or not use.get("verb") or use.get("target"):
                    continue
                hits = [o for o in info["objects"] if o["name"].lower() == (obj.get("name") or "").lower()
                        and o["type"] in (4, 6)]
                if not hits or (hits[0]["name"], use["verb"]) in seen:
                    continue
                seen.add((hits[0]["name"], use["verb"]))
                dest = int(re.match(r"^\*(\d+)", codes[0]).group(1))
                r = hits[0]["rect"]
                # Candidate click points: free of other hotspots and of Holmes, then the plain centre.
                others = [o["rect"] for o in info["objects"] if o is not hits[0]]
                points = []
                quarters = [(r[0] + (r[2] - r[0]) * i // 4, r[1] + (r[3] - r[1]) * j // 4) for i in (1, 3) for j in (1, 2)]
                for cand in (free_point(r, others, [], width, [holmes_rect]), free_point(r, others, [], width),
                             ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2), *quarters):
                    if cand and list(cand) not in points:
                        points.append(list(cand))
                test = {"kind": "door", "scene": scene, "name": f"{hits[0]['name']}-{use['verb']}", "dest": dest,
                        "label": f"{hits[0]['name']} > {use['verb']}", "rect": r, "points": points}
                pre = reach(points[0][0])
                if pre is None:
                    tests.append({**test, "skip": "object off screen and not reachable by scrolling"})
                    continue
                lines, sx = pre
                tests.append({**test, "scripts": [head + settle + lines + [f"rclick {x - sx} {y}", "waitframes 6",
                              f"verb {use['verb']}"] + follow_lines(dest)
                              for x, y in points if sx <= x < sx + SCREEN_W]})

        if ssize:
            right, s_right, end = walk_plan(zones, avoid, home, s0, ssize, "right", width)
            left, s_left, _ = walk_plan(zones, avoid, end, s_right, ssize, "left", width)
            script = list(head) + settle
            for tag, clicks in (("2", right), ("3", left)):
                for i, ((x, y), s) in enumerate(clicks):
                    script += [f"click {x - s} {y}", "waitframes 10", "waitidle 30000", SCROLL_SETTLE,
                               f"dump {tag}{i + 1}_x{x}"]
            tests.append({"kind": "scroll", "scene": scene, "name": "scroll", "label": f"scroll 0..{ssize}",
                          "scroll_size": ssize, "planned": [s_right, s_left], "scripts": [script]})

        if scene in args.inventory_scenes:
            point = None
            for z in sorted(layout["zones"], key=lambda z: -z["w"] * z["h"]):
                rect = [max(z["x"], s0), z["y"], min(z["x"] + z["w"], s0 + SCREEN_W), z["y"] + z["h"]]
                point = free_point(rect, hot, layout["zones"], width, exit_rects)
                if point:
                    break
            if point:
                px, py = point[0] - s0, point[1]
                tests.append({"kind": "inventory", "scene": scene, "name": "inventory", "label": "Inventory",
                              "point": list(point), "scripts": [head + [
                                  f"rclick {px} {py}", "waitframes 6", "dump 02_verbs", "verb Inventory",
                                  "waitframes 30", "dump 03_inventory", "move 120 200", "waitframes 15",
                                  "dump 04_hover", "click 8 8", "waitframes 30", "dump 05_closed",
                                  "waitidle 20000", "dump 06_settled"]]})

    # Overhead map: leave Baker Street (scene 1, exit to the city) and pick a location.
    if not args.scenes or 1 in args.scenes:
        info = survey(args.survey_dir, 1)
        baker = room_layout(args.data_dir / "RES01.RRM")
        map_exits = [e for e in baker["exits"] if e["scene"] in MAP_SCENES]
        if info and map_exits:
            hot = [o["rect"] for o in info["objects"]]
            px, py = free_point(map_exits[0]["rect"], hot, baker["zones"], SCREEN_W)
            for target, (name, wx, wy) in MAP_PICKS.items():
                sx, sy = (0, 0) if wy < SCREEN_H else (0, SCREEN_H)  # scroll to the quarter holding the icon
                corner = "move 4 4" if sy == 0 else "move 4 476"
                tests.append({"kind": "map", "scene": 1, "name": f"map-{target}", "dest": target,
                              "label": f"map > {name}", "scripts": [[
                                  "waitscene 1 30000", "waitidle 30000", "dump 01_room", f"click {px} {py}",
                                  "waitframes 240", "move 320 240", "waitframes 30", "dump 10_map",
                                  "move 636 476", "waitframes 90", "move 320 240", "waitframes 10",
                                  "dump 11_bottom_right", corner, "waitframes 90", "move 320 240",
                                  "waitframes 10", "dump 12_scrolled", f"move {wx - sx} {wy - sy}", "waitframes 10",
                                  "dump 13_hover", f"click {wx - sx} {wy - sy}", "waitframes 45",
                                  "dump 14_closeup", f"waitscene {target} 60000", "dump 30_arrive",
                                  "waitidle 30000", "dump 32_settled"]]})

    # Journal: its only mouse route is the verb menu of Watson, and `objects` does not list people, so this
    # uses a spot where the save has Watson standing (room 24, found by the door test's verb menu).
    for scene, (x, y) in JOURNAL_WATSON.items():
        if args.scenes and scene not in args.scenes:
            continue
        tests.append({"kind": "journal", "scene": scene, "name": "journal", "label": "Journal (via Watson)",
                      "point": [x, y], "scripts": [[
                          f"waitscene {scene} 30000", "waitidle 30000", "dump 01_room", f"rclick {x} {y}",
                          "waitframes 6", "dump 02_verbs", "verb Journal", "waitframes 60", "dump 03_journal",
                          "waitframes 60", "dump 04_journal_later"]]})
    return tests


# --- running ------------------------------------------------------------------------------------

def slug(test: dict) -> str:
    return f"{test['kind']}_room{test['scene']:02d}_" + re.sub(r"[^A-Za-z0-9]+", "-", test["name"]).strip("-")


def brightness(path: str) -> float:
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        return ImageStat.Stat(im.convert("L").resize((160, 120))).mean[0]


def run(args, test: dict, label: str, pack: Path, script: Path) -> dict:
    out = args.output_dir / slug(test) / (label if script.stem.endswith("-a1") else f"{label}-{script.stem[-2:]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "tools/playtest_rosetattoo.py"), "--scummvm", str(args.scummvm),
           "--data-dir", str(args.data_dir), "--save", str(args.save), "--pack", str(pack),
           "--scene", str(test["scene"]), "--script", str(script), "--output-dir", str(out),
           "--sdl-retries", "10", "--timeout", "240"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dumps, states, logs, error = {}, {}, [], None
    for log in sorted(out.glob("attempt-*/dumps/playtest.jsonl"))[-1:]:
        for line in log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            data = event["data"]
            if event["event"] == "DUMP":
                dumps[data["name"]] = data["file"]
                states[data["name"]] = data["state"]
            elif event["event"] == "LOG":
                logs.append({"text": data.get("text"), "state": data.get("state")})
            elif event["event"] == "ERROR":
                error = data
                if data.get("file"):
                    dumps[f"error_line{data.get('line')}"] = data["file"]
    for err in sorted(out.glob("attempt-*/dumps/error_line*.png"))[-1:]:
        dumps.setdefault(err.stem, str(err))
    checks = []
    scenes = [s.get("scene") for s in states.values()]
    if test.get("dest") is not None:
        want = 100 if test["dest"] in MAP_SCENES else test["dest"]
        if want not in scenes:
            checks.append(f"never reached scene {want} (saw {sorted(set(scenes))})")
    if test["kind"] == "map" and 100 not in scenes:
        checks.append("map never shown")
    if test["kind"] == "scroll":
        xs = [s["scroll"][0] for s in states.values()]
        want_right, want_left = test["planned"]
        if xs and max(xs) < want_right:
            checks.append(f"scrolled right only to {max(xs)} (floor allows {want_right} of {test['scroll_size']})")
        if xs and min(xs) > want_left:
            checks.append(f"scrolled left only to {min(xs)} (floor allows {want_left})")
    dark = [name for name, f in dumps.items() if Path(f).is_file() and brightness(f) < 6]
    if dark:
        checks.append("near-black dumps: " + ", ".join(dark))
    if error:
        checks.append(f"script error line {error.get('line')}: {error.get('message') or error.get('text')}")
    return {"exit": proc.returncode, "dumps": dumps, "states": states, "logs": logs, "error": error,
            "checks": checks, "dir": str(out)}


def sheets(results: list[dict], labels: list[str], out_dir: Path, per_page: int = 8) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    tile_w, cols = 240, 7
    tile_h = tile_w * 3 // 4
    rows = []
    for r in results:
        if "runs" not in r:
            continue
        for lb in labels:
            run_ = r["runs"][lb]
            names = sorted(run_["dumps"])
            if len(names) > cols:  # keep the first, the last and evenly spread ones in between
                keep = {names[0], names[-1]}
                keep |= {names[round(i * (len(names) - 1) / (cols - 1))] for i in range(cols)}
                names = sorted(keep)[:cols]
            row = Image.new("RGB", (cols * (tile_w + 4), tile_h + 36), (24, 24, 24))
            draw = ImageDraw.Draw(row)
            title = f"[{lb}] room {r['scene']} {r['kind']}: {r['label']}"
            if r.get("dest") is not None:
                title += f" -> {r['dest']}"
            draw.text((4, 2), title + ("   !! " + "; ".join(run_["checks"]) if run_["checks"] else ""),
                      fill=(255, 120, 120) if run_["checks"] else (230, 230, 230), font=font)
            for i, name in enumerate(names):
                path = Path(run_["dumps"][name])
                if not path.is_file():
                    continue
                with Image.open(path) as im:
                    row.paste(im.convert("RGB").resize((tile_w, tile_h)), (i * (tile_w + 4), 34))
                st = run_["states"].get(name, {})
                draw.text((i * (tile_w + 4) + 2, 20), f"{name} s{st.get('scene', '?')} x{st.get('scroll', ['?'])[0]}",
                          fill=(255, 220, 90), font=font)
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
        page.save(path, quality=82)
        pages.append(path)
    return pages


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scummvm", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--save", type=Path, required=True)
    ap.add_argument("--survey-dir", type=Path, required=True)
    ap.add_argument("--pack", action="append", default=[], metavar="LABEL=DIR",
                    help="Pack to test, as label=path (repeatable; sheet rows follow this order)")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--scenes", type=int, nargs="*", help="Only these scenes")
    ap.add_argument("--kinds", nargs="*", default=["exit", "door", "scroll", "map", "inventory", "journal"])
    ap.add_argument("--inventory-scenes", type=int, nargs="*", default=[1, 3, 36],
                    help="Rooms to open the inventory in")
    ap.add_argument("--jobs", type=int, default=3, help="Parallel engine runs")
    ap.add_argument("--list", action="store_true", help="Only list the tests")
    args = ap.parse_args()
    args.scummvm = args.scummvm.resolve()
    args.data_dir = args.data_dir.resolve()
    args.save = args.save.resolve()
    packs = [(p.split("=", 1)[0], Path(p.split("=", 1)[1]).resolve()) for p in args.pack]
    tests = [t for t in build_tests(args) if t["kind"] in args.kinds or "skip" in t]
    if args.list or not packs:
        for t in tests:
            print(json.dumps({k: v for k, v in t.items() if k != "scripts"}))
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    scripts = args.output_dir / "scripts"
    scripts.mkdir()
    jobs = []
    for test in tests:
        if "scripts" in test:
            files = []
            for n, lines in enumerate(test["scripts"], 1):
                path = scripts / f"{slug(test)}-a{n}.txt"
                path.write_text(f"# {test['kind']} room {test['scene']}: {test['label']} (variant {n})\n"
                                + "\n".join(lines + ["quit", ""]), encoding="utf-8")
                files.append(path)
            test["script_files"] = [str(f) for f in files]
            test["runs"] = {}
            jobs += [(test, label, pack, files) for label, pack in packs]

    def work(job):
        # Later variants (other click points) only run when the verb menu lacked the verb: a person or
        # another hotspot was under the click.
        test, label, pack, files = job
        for path in files:
            result = run(args, test, label, pack, path)
            result["variant"] = path.stem[-2:]
            if not (result["error"] and "verb menu" in json.dumps(result["error"])):
                break
        test["runs"][label] = result
        status = "; ".join(result["checks"]) or "ok"
        print(f"{slug(test)} [{label}] {result['variant']} {status}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(work, jobs))
    results = [{k: v for k, v in t.items() if k != "scripts"} for t in tests]
    (args.output_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    for page in sheets(results, [lb for lb, _ in packs], args.output_dir):
        print(page)


if __name__ == "__main__":
    main()
