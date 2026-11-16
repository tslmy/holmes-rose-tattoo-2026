"""`brief`: per-scene photoreal prompt briefs and the room's named objects.

Per scene, into work/brief/scene_NNN/:
  visual_brief.txt   STYLE + a short scene-specific description (the redraw prompt body)
  objects.json       the room's named objects in the default-state plate: box in original
                     coordinates, a short detector query, and whether the object carries
                     writing or is a clue (those regions are protected, docs/remaster-plan.md)
  brief.json         the LLM exchange, model, timing and any fallback used

The vision LLM from [llm] sees the de-dithered plate and the object list. If it
is unreachable or returns unusable JSON the stage still finishes: queries fall
back to the object's in-game name and writing/clue flags to a keyword list, and
brief.json says so.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..config import Config
from ..jobs import JobSpec
from ..llm import ChatClient
from . import common, extract, prepare

NAME = "brief"
SUMMARY = "Per-scene photoreal prompt briefs and named objects (detector queries, writing/clue flags)"
CODE = [Path(__file__)]

# The reference look (docs/remaster-plan.md, "Reference look"; ChatGPT-conversation.pdf):
# a photograph of a physical, built set, keeping the original's vibrant palette.
STYLE = ("A photograph of a physical, built Victorian film set in 1890s London, as if shot on location for "
         "a period drama: real materials (wood grain, brick, plaster, cloth, brass, glass), practical lighting "
         "from the lamps and windows that are in the scene, real-looking people in period costume. "
         "Keep the original picture's vibrant colour palette; do not desaturate or turn it brown.")

WRITING_WORDS = ("sign", "poster", "handbill", "banner", "newspaper", "paper", "letter", "note", "book", "map",
                 "chart", "directory", "summary", "formula", "sheet music", "kiosk", "plaque", "label",
                 "card", "notice", "plaque", "board")
CLUE_WORDS = ("clue", "stain", "blood", "button", "disk", "fob", "formula", "summary", "handbill", "pistol",
              "gun", "case", "glass", "chalk", "pebble", "scarf", "jacket")

SYSTEM = ("You help remaster a 1996 adventure game. You look at one room and describe it for a photoreal "
          "image model, and you name its objects so an open-vocabulary detector can find them. "
          "Answer with JSON only.")


def out_dir(cfg: Config, scene: int) -> Path:
    return cfg.work / "brief" / f"scene_{scene:03d}"


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    llm = cfg.section("llm")
    for scene in common.resolve_scenes(cfg, args.scenes, args.all):
        prep = prepare.out_root(cfg) / f"scene_{scene:03d}"
        fp = common.fingerprint(common.read_stamp(prep).get("fingerprint"),
                                common.read_stamp(extract.scene_dir(cfg, "rooms", scene)).get("fingerprint"),
                                STYLE, llm.get("vision_model"), *CODE)
        specs.append(JobSpec(NAME, f"scene/{scene:03d}", {"scene": scene}, fp,
                             after=[f"prepare:scene/{scene:03d}"], tags=["llm-models"]))
    return specs


def room_objects(metadata: dict, plate: dict) -> list[dict]:
    """Named objects visible in the default-state plate, with boxes in original coordinates.

    Hotspot bounds are used when they are real rectangles; objects whose bounds are a 1x1
    anchor take the box of their plate layer. States of one object (sign1/sign2) are merged.
    """
    layer_box = {l["object_index"]: (l["x"], l["y"], l["width"], l["height"]) for l in plate.get("layers", [])}
    width, height = plate.get("size", [640, 480])
    found: list[dict] = []
    for obj in metadata.get("objects", []):
        label = (obj.get("description") or "").strip()
        if not label or obj.get("sprite_type") == "hidden" or label.startswith('"'):
            continue
        b = obj.get("bounds", {})
        if b.get("w", 0) > 2 and b.get("h", 0) > 2:
            box = (b["x"], b["y"], b["w"], b["h"])
            source = "hotspot"
        elif obj["index"] in layer_box:
            box, source = layer_box[obj["index"]], "plate-layer"
        else:
            continue
        x, y, w, h = box
        x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        box = [x0, y0, x1 - x0, y1 - y0]
        dup = next((o for o in found if o["label"] == label and iou(o["box"], box) > 0.5), None)
        if dup:
            dup["names"].append(obj["name"])
            continue
        found.append({"id": f"o{len(found) + 1:02d}", "label": label, "names": [obj["name"]], "box": box,
                      "source": source, "kind": obj.get("action_type", "object"),
                      "layer": obj["index"] in layer_box})
    return found


def iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def keyword_flags(label: str) -> tuple[bool, bool]:
    text = label.lower()
    return any(w in text for w in WRITING_WORDS), any(w in text for w in CLUE_WORDS)


def default_query(obj: dict) -> str:
    if obj["kind"] == "person":
        return "person"
    return re.sub(r"[^a-z' -]", "", obj["label"].lower()).strip() or "object"


def user_prompt(scene_name: str, objects: list[dict], size: tuple[int, int]) -> str:
    rows = "\n".join(f'{o["id"]}: "{o["label"]}" box x={o["box"][0]} y={o["box"][1]} w={o["box"][2]} '
                     f'h={o["box"][3]}' for o in objects)
    return f"""The image is the room "{scene_name}" ({size[0]}x{size[1]} pixels). Its named objects:
{rows}

Return JSON with these keys:
"brief": 2-4 sentences describing what is visibly in the room (architecture, furniture, materials, light
sources, people and their clothes), written as a description of a real photograph. Only what is visible.
Name a sign's words only if you can read them clearly.
"objects": one entry per object id above: {{"id": ..., "query": a 1-4 word concrete visual noun phrase an
object detector can find in a photo of this room (e.g. "fire hydrant", "barred window", "man in apron"),
"writing": true if the object shows readable text, numbers or a printed page, "clue": true if it is a small
item a player would pick up or examine closely}}."""


def parse_reply(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    return json.loads(match.group(0))


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    scene = int(params["scene"])
    target = out_dir(cfg, scene)
    if common.up_to_date(target, fingerprint):
        return {"skipped": True}
    prep = prepare.out_root(cfg) / f"scene_{scene:03d}"
    rooms = extract.scene_dir(cfg, "rooms", scene)
    metadata = json.loads((rooms / "metadata.json").read_text(encoding="utf-8"))
    plate = json.loads((prep / "plate.json").read_text(encoding="utf-8"))
    objects = room_objects(metadata, plate)
    size = tuple(plate.get("size", [640, 480]))
    record: dict = {"scene": scene, "objects": len(objects), "fallback": []}
    reply: dict = {}
    try:
        client = ChatClient.from_config(cfg.section("llm"), "vision")
        record["model"] = f"{client.model} @ {client.base_url}"
        result = client.chat(SYSTEM, user_prompt(metadata.get("scene_name", ""), objects, size),
                             images=[prep / "plate_dedither.png"], max_tokens=1800, temperature=0.2)
        record["seconds"] = result.seconds
        record["reply"] = result.text
        reply = parse_reply(result.text)
    except Exception as err:  # noqa: BLE001 - the stage must finish without the LLM
        record["fallback"].append(f"llm: {type(err).__name__}: {str(err)[:300]}")
    by_id = {o.get("id"): o for o in reply.get("objects", []) if isinstance(o, dict)}
    for obj in objects:
        kw_writing, kw_clue = keyword_flags(obj["label"])
        got = by_id.get(obj["id"], {})
        obj["query"] = str(got.get("query") or default_query(obj)).strip()[:60]
        obj["writing"] = bool(got.get("writing", kw_writing)) and obj["kind"] != "person"
        obj["clue"] = bool(got.get("clue", kw_clue))
        if not got:
            record["fallback"].append(f"{obj['id']}: keyword query/flags")
    scene_brief = str(reply.get("brief") or "").strip()
    if not scene_brief:
        scene_brief = (f"{metadata.get('scene_name', 'A room')}, with " +
                       ", ".join(sorted({o['label'] for o in objects})[:20]) + ".")
        record["fallback"].append("brief: built from object names")
    tmp = common.scratch_dir(cfg, f"brief-{scene}")
    (tmp / "visual_brief.txt").write_text(f"{STYLE}\n\n{scene_brief}\n", encoding="utf-8")
    common.write_json(tmp / "objects.json", {"scene": scene, "size": list(size), "objects": objects})
    common.write_json(tmp / "brief.json", record)
    common.write_stamp(tmp, fingerprint, scene=scene)
    common.install_dir(tmp, target)
    return {"objects": len(objects), "writing": sum(o["writing"] for o in objects),
            "clue": sum(o["clue"] for o in objects), "fallbacks": len(record["fallback"]),
            "seconds": record.get("seconds")}
