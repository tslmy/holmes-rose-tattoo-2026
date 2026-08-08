#!/usr/bin/env python3
"""Extract Rose Tattoo room backgrounds and prompt metadata.

This follows ScummVM's Sherlock/Rose Tattoo room loader closely enough to pull
the base 8-bit room image, scene title, object fields, embedded examine text,
and prompt notes from RESxx.RRM files.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


SCREEN_WIDTH = 640
SCREEN_HEIGHT = 480
PALETTE_SIZE = 256 * 3
OBJECT_RECORD_SIZE = 625


SPRITE_TYPES = {
    0: "invalid",
    1: "character",
    2: "cursor",
    3: "static_bg_shape",
    4: "active_bg_shape",
    5: "remove",
    6: "no_shape",
    7: "hidden",
    8: "hide_shape",
    128: "hidden_character",
}

A_TYPES = {
    0: "object",
    1: "person",
    2: "solid",
    3: "talk",
    4: "flag_set",
    5: "delta",
    6: "walk_around",
    7: "talk_every",
    8: "talk_move",
    9: "pal_change",
    10: "pal_change2",
    11: "script_zone",
    12: "blank_zone",
    13: "nowalk_zone",
}


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def seek(self, pos: int) -> None:
        self.pos = pos

    def skip(self, count: int) -> None:
        self.pos += count

    def read(self, count: int) -> bytes:
        out = self.data[self.pos : self.pos + count]
        if len(out) != count:
            raise EOFError(f"wanted {count} bytes at {self.pos}, got {len(out)}")
        self.pos += count
        return out

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def s16(self) -> int:
        value = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def s32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value


@dataclass
class RoomHeader:
    num_structs: int
    num_images: int
    num_animations: int
    desc_size: int
    seq_size: int
    scroll_size: int
    bytes_written: int
    fade_style: int


def c_string(raw: bytes) -> str:
    raw = raw.split(b"\x00", 1)[0]
    text = raw.decode("cp437", errors="replace")
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def text_at(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    return c_string(blob[offset:])


def looks_natural_language(text: str) -> bool:
    if len(text) < 20:
        return False
    if not re.search(r"[a-z]{3}", text):
        return False
    if text.lower().startswith("size:") or '" size:' in text.lower():
        return False
    printable = sum(ch.isprintable() for ch in text)
    if printable / max(len(text), 1) < 0.95:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters / max(len(text), 1) > 0.45


def natural_language_strings(blob: bytes) -> list[str]:
    strings: list[str] = []
    seen: set[str] = set()
    for part in blob.split(b"\x00"):
        text = c_string(part)
        if not looks_natural_language(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        strings.append(text)
    return strings


def palette_6bit_to_8bit(value: int) -> int:
    return (value << 2) | (value >> 4)


def decompress_lz(source: Reader, out_size: int, has_input_size: bool = True) -> bytes:
    in_size = source.s32() if has_input_size else -1
    end_pos = source.pos + in_size if in_size != -1 else None
    window = bytearray([0xFF] * 4096)
    window_pos = 0xFEE
    cmd = 0
    out = bytearray()

    while len(out) < out_size and (end_pos is None or source.pos < end_pos):
        cmd >>= 1
        if not (cmd & 0x100):
            cmd = source.u8() | 0xFF00

        if cmd & 1:
            literal = source.u8()
            out.append(literal)
            window[window_pos] = literal
            window_pos = (window_pos + 1) & 0x0FFF
        else:
            copy_pos = source.u8()
            copy_len = source.u8()
            copy_pos |= (copy_len & 0xF0) << 4
            copy_len = (copy_len & 0x0F) + 3

            for _ in range(copy_len):
                if len(out) >= out_size:
                    break
                literal = window[copy_pos]
                copy_pos = (copy_pos + 1) & 0x0FFF
                out.append(literal)
                window[window_pos] = literal
                window_pos = (window_pos + 1) & 0x0FFF

    if end_pos is not None and source.pos < end_pos:
        source.seek(end_pos)
    if len(out) < out_size:
        out.extend(b"\x00" * (out_size - len(out)))
    return bytes(out[:out_size])


def parse_journal(path: Path) -> dict[int, str]:
    scenes: dict[int, str] = {}
    if not path.exists():
        return scenes
    pattern = re.compile(r'^\s*(\d+)\.\s*"([^"]+)"')
    for line in path.read_text(encoding="cp437", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            scenes[int(match.group(1))] = match.group(2)
    return scenes


def parse_header(reader: Reader) -> RoomHeader:
    return RoomHeader(
        num_structs=reader.u16(),
        num_images=reader.u16(),
        num_animations=reader.u16(),
        desc_size=reader.u16(),
        seq_size=reader.u16(),
        scroll_size=reader.u16(),
        bytes_written=reader.u32(),
        fade_style=reader.u8(),
    )


def parse_object_record(record: bytes, desc_text: bytes, index: int) -> dict:
    r = Reader(record)
    name = c_string(r.read(12))
    description = c_string(r.read(41))
    r.skip(4)
    sequence_offset = r.u16()
    r.skip(10)

    walk_count = r.u8()
    allow = r.u8()
    frame_number = r.s16()
    sequence_number = r.s16()
    position = {"x": r.s16(), "y": r.s16()}
    delta = {"x": r.s16(), "y": r.s16()}
    sprite_type = r.u16()
    old_position = {"x": r.s16(), "y": r.s16()}
    old_size = {"w": r.u16(), "h": r.u16()}
    goto = {"x": r.s16(), "y": r.s16()}
    look_flag = r.s16()
    required_flag_0 = r.s16()
    no_shape_size = {"w": r.u16(), "h": r.u16()}
    status = r.u16()
    image_index = r.u8()
    max_frames = r.u16()
    flags = r.u8()
    action_type = r.u8()
    look_frames = r.u8()
    seq_counter = r.u8()
    look_position = {"x": r.u16(), "y": r.s16(), "facing": None}
    look_position["facing"] = r.u8()
    look_canim = r.u8()
    seq_stack = r.u8()
    seq_to = r.u8()
    desc_offset = r.u16()
    seq_counter_2 = r.u8()
    seq_size = r.u16()

    uses = []
    for _ in range(6):
        verb = c_string(r.read(12))
        canim_num = r.u8()
        canim_speed = r.u8()
        if canim_speed & 0x80:
            canim_speed = -(canim_speed & 0x7F)
        names = [c_string(r.read(12)) for _ in range(4)]
        use_flag = r.s16()
        target = c_string(r.read(12))
        uses.append(
            {
                "verb": verb,
                "target": target,
                "names": [n for n in names if n],
                "canim_num": canim_num,
                "canim_speed": canim_speed,
                "use_flag": use_flag,
            }
        )

    quick_draw = r.u8()
    scale_val = r.u16()
    required_flag_1 = r.s16()
    goto_seq = r.u8()
    talk_seq = r.u8()
    restore_slot = r.u8()

    examine = text_at(desc_text, desc_offset)
    bounds = None
    if no_shape_size["w"] or no_shape_size["h"]:
        bounds = {
            "x": position["x"],
            "y": position["y"],
            "w": no_shape_size["w"],
            "h": no_shape_size["h"],
        }
    elif old_size["w"] or old_size["h"]:
        bounds = {
            "x": position["x"],
            "y": position["y"],
            "w": old_size["w"],
            "h": old_size["h"],
        }

    return {
        "index": index,
        "name": name,
        "description": description,
        "examine": examine,
        "sprite_type": SPRITE_TYPES.get(sprite_type, f"unknown_{sprite_type}"),
        "action_type": A_TYPES.get(action_type, f"unknown_{action_type}"),
        "position": position,
        "bounds": bounds,
        "walk_count": walk_count,
        "allow": allow,
        "image_index": image_index,
        "max_frames": max_frames,
        "look_position": look_position,
        "look_flag": look_flag,
        "required_flags": [required_flag_0, required_flag_1],
        "status": status,
        "sequence_offset": sequence_offset,
        "sequence_number": sequence_number,
        "sequence_size": seq_size,
        "frame_number": frame_number,
        "delta": delta,
        "goto": goto,
        "flags": flags,
        "look_frames": look_frames,
        "seq_counter": seq_counter,
        "seq_counter_2": seq_counter_2,
        "seq_stack": seq_stack,
        "seq_to": seq_to,
        "look_canim": look_canim,
        "quick_draw": quick_draw,
        "scale_val": scale_val,
        "goto_seq": goto_seq,
        "talk_seq": talk_seq,
        "restore_slot": restore_slot,
        "uses": [
            use
            for use in uses
            if use["verb"] or use["target"] or use["names"] or use["use_flag"]
        ],
    }


def image_from_indices(indices: bytes, width: int, height: int, palette: bytes) -> Image.Image:
    img = Image.frombytes("P", (width, height), indices)
    rgb_palette = [palette_6bit_to_8bit(v) for v in palette]
    img.putpalette(rgb_palette)
    return img.convert("RGB")


def prompt_terms(description_texts: Iterable[str], limit: int) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in description_texts:
        value = re.sub(r"\s+", " ", value).strip()
        if not looks_natural_language(value) or value in seen:
            continue
        seen.add(value)
        terms.append(value)
        if len(terms) >= limit:
            return terms
    return terms


def write_prompt(path: Path, scene_id: int, scene_name: str, terms: list[str]) -> None:
    details = "\n".join(f"- {term}" for term in terms) or "- No extracted detail text."
    path.write_text(
        "\n".join(
            [
                f"Scene {scene_id:02d}: {scene_name}",
                "",
                "Enhance this 1996 point-and-click adventure background.",
                "Preserve the original 640x480 composition, camera angle, geometry, walkable paths,",
                "doorways, props, lighting direction, and every puzzle-relevant object.",
                "Do not add new readable text, people, doors, exits, inventory items, or clues.",
                "Keep it as a hand-painted Victorian detective adventure game background.",
                "",
                "Pinned visual details extracted from game resources:",
                details,
                "",
            ]
        ),
        encoding="utf-8",
    )


def extract_scene(rrm_path: Path, scene_name: str, output_root: Path, term_limit: int) -> dict:
    scene_id_match = re.search(r"RES(\d+)\.RRM$", rrm_path.name, re.IGNORECASE)
    if not scene_id_match:
        raise ValueError(f"could not infer scene id from {rrm_path}")
    scene_id = int(scene_id_match.group(1))

    reader = Reader(rrm_path.read_bytes())
    reader.seek(39)
    compressed = reader.u8() > 0
    header_offset = reader.u32()
    reader.seek(header_offset)
    header = parse_header(reader)
    full_width = SCREEN_WIDTH + header.scroll_size
    palette = reader.read(PALETTE_SIZE)
    pixel_count = full_width * SCREEN_HEIGHT
    bg_indices = (
        decompress_lz(reader, pixel_count)
        if compressed
        else reader.read(pixel_count)
    )

    bg_info = []
    for _ in range(header.num_structs):
        bg_info.append(
            {
                "filesize": reader.u32(),
                "max_frames": reader.u8(),
                "filename": c_string(reader.read(9)),
            }
        )

    shape_bytes = (
        decompress_lz(reader, header.num_structs * OBJECT_RECORD_SIZE)
        if compressed
        else reader.read(header.num_structs * OBJECT_RECORD_SIZE)
    )
    desc_text = (
        decompress_lz(reader, header.desc_size)
        if compressed
        else reader.read(header.desc_size)
    )

    objects = [
        parse_object_record(
            shape_bytes[i * OBJECT_RECORD_SIZE : (i + 1) * OBJECT_RECORD_SIZE],
            desc_text,
            i + 1,
        )
        for i in range(header.num_structs)
    ]
    description_texts = natural_language_strings(desc_text)

    # Later resource sections hold sequences, image chunks, animations, walk
    # zones, exits, and sounds. They are intentionally left for follow-up
    # extractors so this first pass stays focused on reliable room backdrops and
    # prompt text.
    scene_dir = output_root / f"scene_{scene_id:02d}"
    scene_dir.mkdir(parents=True, exist_ok=True)

    image_from_indices(bg_indices, full_width, SCREEN_HEIGHT, palette).save(
        scene_dir / "background.png"
    )

    terms = prompt_terms(description_texts, term_limit)
    metadata = {
        "scene_id": scene_id,
        "scene_name": scene_name,
        "source": str(rrm_path),
        "background": {
            "width": full_width,
            "height": SCREEN_HEIGHT,
            "scroll_size": header.scroll_size,
            "compressed": compressed,
        },
        "header": {
            "num_structs": header.num_structs,
            "num_images": header.num_images,
            "num_animations": header.num_animations,
            "desc_size": header.desc_size,
            "seq_size": header.seq_size,
            "bytes_written": header.bytes_written,
            "fade_style": header.fade_style,
        },
        "image_chunks": bg_info,
        "description_texts": description_texts,
        "objects": objects,
        "prompt_terms": terms,
        "notes": [
            "Background is the raw room backdrop before runtime object/sprite compositing.",
            "Object bounds are best-effort metadata from room object records.",
            "Animation and sprite frame extraction is intentionally left for a later pass.",
        ],
    }
    (scene_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_prompt(scene_dir / "prompt.txt", scene_id, scene_name, terms)
    return metadata


def scene_paths(data_dir: Path, selected: list[int] | None) -> list[Path]:
    if selected:
        return [data_dir / f"RES{scene_id:02d}.RRM" for scene_id in selected]
    return sorted(data_dir.glob("RES*.RRM"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Rose Tattoo room backgrounds and prompt metadata."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    parser.add_argument("--output-dir", type=Path, default=Path("extracted/rosetattoo"))
    parser.add_argument(
        "--scenes",
        type=int,
        nargs="*",
        help="Optional scene numbers, e.g. --scenes 1 2 18. Defaults to all RES*.RRM files.",
    )
    parser.add_argument("--term-limit", type=int, default=40)
    args = parser.parse_args()

    journal = parse_journal(args.data_dir / "JOURNAL.TXT")
    extracted = []
    for path in scene_paths(args.data_dir, args.scenes):
        if not path.exists():
            raise FileNotFoundError(path)
        scene_id = int(re.search(r"RES(\d+)\.RRM$", path.name, re.IGNORECASE).group(1))
        scene_name = journal.get(scene_id, f"Scene {scene_id:02d}")
        metadata = extract_scene(path, scene_name, args.output_dir, args.term_limit)
        extracted.append(metadata)
        print(
            f"scene {scene_id:02d}: {scene_name} "
            f"({metadata['background']['width']}x{metadata['background']['height']}, "
            f"{len(metadata['objects'])} objects)"
        )

    manifest = {
        "data_dir": str(args.data_dir),
        "scene_count": len(extracted),
        "scenes": [
            {
                "scene_id": item["scene_id"],
                "scene_name": item["scene_name"],
                "width": item["background"]["width"],
                "height": item["background"]["height"],
                "object_count": len(item["objects"]),
            }
            for item in extracted
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
