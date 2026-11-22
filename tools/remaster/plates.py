"""Build a room plate: default-state room sprites composited onto the background.

Mirrors what the engine draws for a freshly loaded scene before any sequence
bytecode runs (scene.cpp `loadScene`, `checkBgShapes`; tattoo_scene.cpp
`drawAllShapes`):

- only `active_bg_shape` objects are drawn;
- an object is visible if its required flags pass with every story flag
  off (negative flag literals pass), the rule in `Scene::checkSceneFlags`;
- its image is frame 0 of room image slot `image_index` (one-based,
  `room_NNN/` from extract_rosetattoo_room_sprites.py), drawn at
  `position + frame offset`, mirrored if OBJ_FLIPPED, scaled if
  `scale_val != 256` (ImageFrame::sDraw* arithmetic);
- order: BEHIND shapes in object order, then NORMAL shapes sorted by their
  bottom edge (stable), then FORWARD shapes.

Not modelled: sequence-driven frame changes after load, quick-draw opaque
blits of transparent pixels, cAnims, people, palette effects. The plate is a
redraw input, not a gameplay reference; the layer index records which pixels
came from which object so static layers can be cut back out later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image

OBJ_BEHIND, OBJ_FLIPPED, OBJ_FORWARD = 1, 2, 4
SCALE_THRESHOLD = 256
Z_BEHIND, Z_NORMAL, Z_FORWARD = 0, 1, 2


def flags_pass(obj: dict, flags_on: frozenset[int] = frozenset()) -> bool:
    for literal in obj.get("required_flags", []):
        literal = int(literal)
        if literal == 0:
            continue
        if (literal > 0) != (abs(literal) in flags_on):
            return False
    return True


def z_class(flags: int) -> int:
    """checkBgShapes: NORMAL if (flags & 5) == 1, BEHIND if no OBJ_BEHIND, else FORWARD."""
    if flags & 5 == 1:
        return Z_NORMAL
    if not flags & OBJ_BEHIND:
        return Z_BEHIND
    return Z_FORWARD


def _scaled(size: int, scale: int) -> int:
    scale = scale or 1
    if scale >= SCALE_THRESHOLD:
        return (size - 1) * SCALE_THRESHOLD // scale + 1
    return size * SCALE_THRESHOLD // scale


def _scaled_offset(offset: int, scale: int) -> int:
    if scale == SCALE_THRESHOLD:
        return offset
    scale = scale or 1
    value = offset - 1 if scale >= SCALE_THRESHOLD else offset
    result = value * SCALE_THRESHOLD // scale
    return result + 1 if scale > SCALE_THRESHOLD else result


@dataclass
class Layer:
    layer: int           # 1-based id used in the layer index image
    object_index: int
    name: str
    image_index: int
    frame: int
    x: int
    y: int
    width: int
    height: int
    z: str
    flipped: bool
    scale_val: int
    source: str


def plan_layers(metadata: dict, sprite_scene_dir: Path,
                flags_on: frozenset[int] = frozenset(),
                static_only: bool = False) -> tuple[list[Layer], list[dict]]:
    """Return drawable layers in draw order, plus skipped objects with reasons.

    With static_only, objects whose room image has more than one frame are
    animated (people, walkers, fire) and are drawn by the engine over the
    background; they are skipped rather than baked into the plate.
    """
    entries = []
    skipped = []
    for order, obj in enumerate(metadata.get("objects", [])):
        if obj.get("sprite_type") != "active_bg_shape":
            continue
        name = obj.get("name", "")
        if not flags_pass(obj, flags_on):
            skipped.append({"object_index": obj["index"], "name": name, "reason": "required flags"})
            continue
        image_index = int(obj.get("image_index", 0))
        room_dir = sprite_scene_dir / f"room_{image_index:03d}"
        meta_path = room_dir / "metadata.json"
        if image_index <= 0 or not meta_path.is_file():
            skipped.append({"object_index": obj["index"], "name": name, "reason": f"no room image {image_index}"})
            continue
        frames = json.loads(meta_path.read_text(encoding="utf-8")).get("frames", [])
        if not frames or frames[0].get("invalid_placeholder"):
            skipped.append({"object_index": obj["index"], "name": name, "reason": "no valid frame 0"})
            continue
        if static_only and len(frames) > 1:
            skipped.append({"object_index": obj["index"], "name": name, "reason": "animated"})
            continue
        frame = frames[0]
        scale = int(obj.get("scale_val", SCALE_THRESHOLD)) or SCALE_THRESHOLD
        width, height = int(frame["width"]), int(frame["height"])
        off_x, off_y = int(frame["offset_x"]), int(frame["offset_y"])
        if scale != SCALE_THRESHOLD:
            width, height = _scaled(width, scale), _scaled(height, scale)
            off_x, off_y = _scaled_offset(off_x, scale), _scaled_offset(off_y, scale)
        flags = int(obj.get("flags", 0))
        x = int(obj["position"]["x"]) + off_x
        y = int(obj["position"]["y"]) + off_y
        z = z_class(flags)
        entries.append((z, y + height if z == Z_NORMAL else 0, order, Layer(
            layer=0, object_index=int(obj["index"]), name=name, image_index=image_index, frame=0,
            x=x, y=y, width=width, height=height, z=("behind", "normal", "forward")[z],
            flipped=bool(flags & OBJ_FLIPPED), scale_val=scale,
            source=str((room_dir / frame["file"]).as_posix()),
        )))
    entries.sort(key=lambda item: item[:3])
    layers = []
    for number, (_, _, _, layer) in enumerate(entries, start=1):
        layer.layer = number
        layers.append(layer)
    return layers, skipped


def build_plate(background: Image.Image, layers: list[Layer]) -> tuple[Image.Image, Image.Image]:
    """Composite layers; return (plate RGB, layer index L image, 0 = background)."""
    plate = background.convert("RGBA")
    index = np.zeros((plate.height, plate.width), dtype=np.uint8)
    for layer in layers:
        sprite = Image.open(layer.source).convert("RGBA")
        if (sprite.width, sprite.height) != (layer.width, layer.height):
            sprite = sprite.resize((layer.width, layer.height), Image.NEAREST)
        if layer.flipped:
            sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
        # Paste into a full-size layer: handles sprites partly off the plate.
        canvas = Image.new("RGBA", plate.size, (0, 0, 0, 0))
        canvas.paste(sprite, (layer.x, layer.y))
        plate = Image.alpha_composite(plate, canvas)
        covered = np.asarray(canvas)[..., 3] > 0
        index[covered] = min(layer.layer, 255)
    return plate.convert("RGB"), Image.fromarray(index, "L")


def layers_json(layers: list[Layer], skipped: list[dict], flags_on: frozenset[int]) -> dict:
    return {
        "flags_on": sorted(flags_on),
        "state": "all story flags off; frame 0 before sequences run",
        "layers": [asdict(layer) for layer in layers],
        "skipped": skipped,
    }
