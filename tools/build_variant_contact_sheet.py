#!/usr/bin/env python3
"""Build a contact sheet comparing background variants across mod directories.

Rows are different "variant" directories (each iteration/experiment under
``mods/``, plus the original extracted background for reference). Columns
are a handful of scene numbers that are commonly represented across most
variants, so a human can visually scan and decide which variant to keep.

Usage:
    python3 tools/build_variant_contact_sheet.py
    python3 tools/build_variant_contact_sheet.py --scenes 1 2 4 7 18 36
    python3 tools/build_variant_contact_sheet.py --mods-dir mods --top-n 8
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCENE_DIR_RE = re.compile(r"^scene_0*(\d+)$")

# Candidate filenames to look for inside a variant's scene directory, in
# priority order (hires override packs use background@2x.png; the original
# extracted assets use background.png).
CANDIDATE_FILENAMES = ["background@2x.png", "background@4x.png", "background.png"]

THUMB_WIDTH = 320
LABEL_HEIGHT = 22
ROW_LABEL_WIDTH = 340
PADDING = 4
BG_COLOR = (24, 24, 24)
TEXT_COLOR = (230, 230, 230)
MISSING_COLOR = (60, 60, 60)


def scene_number_from_dirname(name: str) -> int | None:
    match = SCENE_DIR_RE.match(name)
    return int(match.group(1)) if match else None


def find_scene_image(variant_dir: Path, scene_number: int) -> Path | None:
    for pad_width in (3, 2, 1):
        scene_dir = variant_dir / f"scene_{scene_number:0{pad_width}d}"
        if scene_dir.is_dir():
            for filename in CANDIDATE_FILENAMES:
                candidate = scene_dir / filename
                if candidate.exists():
                    return candidate
    return None


def discover_variants(mods_dir: Path, extracted_dir: Path | None) -> list[tuple[str, Path]]:
    variants: list[tuple[str, Path]] = []
    if extracted_dir and extracted_dir.is_dir():
        variants.append(("(original) extracted/rosetattoo", extracted_dir))
    for entry in sorted(mods_dir.iterdir()):
        if entry.is_dir():
            variants.append((entry.name, entry))
    return variants


def scene_coverage(variants: list[tuple[str, Path]]) -> dict[int, int]:
    """Count, for each scene number, how many variants have that scene."""
    coverage: dict[int, int] = {}
    for _, variant_dir in variants:
        seen_scenes: set[int] = set()
        for child in variant_dir.iterdir():
            if not child.is_dir():
                continue
            scene_number = scene_number_from_dirname(child.name)
            if scene_number is None:
                continue
            seen_scenes.add(scene_number)
        for scene_number in seen_scenes:
            coverage[scene_number] = coverage.get(scene_number, 0) + 1
    return coverage


def pick_common_scenes(coverage: dict[int, int], top_n: int) -> list[int]:
    ranked = sorted(coverage.items(), key=lambda kv: (-kv[1], kv[0]))
    return sorted(scene for scene, _count in ranked[:top_n])


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
    return ImageFont.load_default()


def make_thumb(image_path: Path | None, width: int) -> Image.Image:
    if image_path is None:
        thumb_height = int(width * 0.375)
        placeholder = Image.new("RGB", (width, thumb_height), MISSING_COLOR)
        draw = ImageDraw.Draw(placeholder)
        font = load_font(14)
        text = "missing"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((width - tw) / 2, (thumb_height - th) / 2),
            text,
            fill=(150, 150, 150),
            font=font,
        )
        return placeholder

    img = Image.open(image_path).convert("RGB")
    ratio = width / img.width
    new_size = (width, max(1, int(img.height * ratio)))
    return img.resize(new_size, Image.LANCZOS)


def build_contact_sheet(
    variants: list[tuple[str, Path]],
    scenes: list[int],
    output_path: Path,
    thumb_width: int = THUMB_WIDTH,
) -> None:
    font = load_font(14)
    header_font = load_font(15)

    # Pre-resolve every cell's image path and thumbnail, so we can compute a
    # consistent row height (based on the tallest thumbnail across the row).
    grid: list[list[Image.Image]] = []
    row_heights: list[int] = []
    for _variant_name, variant_dir in variants:
        row_thumbs: list[Image.Image] = []
        max_height = 1
        for scene_number in scenes:
            image_path = find_scene_image(variant_dir, scene_number)
            thumb = make_thumb(image_path, thumb_width)
            row_thumbs.append(thumb)
            max_height = max(max_height, thumb.height)
        grid.append(row_thumbs)
        row_heights.append(max_height)

    col_width = thumb_width + PADDING
    total_width = ROW_LABEL_WIDTH + col_width * len(scenes) + PADDING
    total_height = LABEL_HEIGHT + sum(h + LABEL_HEIGHT + PADDING for h in row_heights) + PADDING

    sheet = Image.new("RGB", (total_width, total_height), BG_COLOR)
    draw = ImageDraw.Draw(sheet)

    # Column headers (scene numbers).
    y = 0
    for col_index, scene_number in enumerate(scenes):
        x = ROW_LABEL_WIDTH + col_index * col_width
        draw.text((x + 4, y + 4), f"scene {scene_number}", fill=TEXT_COLOR, font=header_font)
    y += LABEL_HEIGHT

    for (variant_name, _variant_dir), row_thumbs, row_height in zip(variants, grid, row_heights):
        # Row label.
        draw.text((4, y + row_height // 2 - 6), variant_name, fill=TEXT_COLOR, font=font)

        for col_index, thumb in enumerate(row_thumbs):
            x = ROW_LABEL_WIDTH + col_index * col_width
            sheet.paste(thumb, (x, y))
        y += row_height + LABEL_HEIGHT + PADDING

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)
    print(f"Wrote {output_path} ({total_width}x{total_height}, "
          f"{len(variants)} variants x {len(scenes)} scenes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mods-dir", type=Path, default=Path("mods"))
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=Path("extracted/rosetattoo"),
        help="Original extracted backgrounds directory, included as a reference row. "
        "Pass --no-original to omit it.",
    )
    parser.add_argument("--no-original", action="store_true")
    parser.add_argument(
        "--scenes",
        type=int,
        nargs="*",
        default=None,
        help="Explicit scene numbers to use as columns. If omitted, the "
        "--top-n scenes with the widest variant coverage are chosen automatically.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=6,
        help="Number of most-commonly-processed scenes to use as columns "
        "when --scenes isn't given.",
    )
    parser.add_argument("--thumb-width", type=int, default=THUMB_WIDTH)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("validation/variant-review/contact_sheet.jpg"),
    )
    args = parser.parse_args()

    extracted_dir = None if args.no_original else args.extracted_dir
    variants = discover_variants(args.mods_dir, extracted_dir)
    if not variants:
        raise SystemExit(f"No variant directories found under {args.mods_dir}")

    if args.scenes:
        scenes = sorted(args.scenes)
    else:
        coverage = scene_coverage(variants)
        scenes = pick_common_scenes(coverage, args.top_n)
        print(f"Auto-selected scenes by variant coverage: {scenes}")

    build_contact_sheet(variants, scenes, args.output, args.thumb_width)


if __name__ == "__main__":
    main()
