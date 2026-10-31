#!/usr/bin/env python3
"""Copy shared sprite extractions and repair known missing-palette resources.

Creates a new output directory; never modifies the supplied extraction tree.
The fallback room palettes are approximations for assets used in several
rooms. A complete extraction and in-game palette handling remain separate.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from extract_rosetattoo_sprites import extract_resource


GROUPS = (
    ("MAP.PAL", None, ("MAP.VGS",)),
    ("DARTBD.PAL", None, ("DARTBD.VGS", "DARTMAP.VGS", "DARTS.VGS", "DARTSLFT.VGS", "HAND1.VGS", "HAND2.VGS")),
    ("JOURNAL.PAL", None, ("JOURNAL.VGS",)),
    (None, 53, ("OPIUM.VGS",)),
    (None, 1, ("COAT3.VGS", "PAPER.VGS", "INTRFACE.VGS", "LOADING.VGS", "LOADING0.VGS", "LOADING1.VGS", "LOADING2.VGS")),
)


def prepare(source: Path, destination: Path, data_dir: Path) -> dict:
    if not source.is_dir() or not data_dir.is_dir():
        raise ValueError("Source extraction and game data directories must exist")
    source_path, destination_path = source.resolve(), destination.resolve()
    if source_path == destination_path or destination_path.is_relative_to(source_path) or source_path.is_relative_to(destination_path):
        raise ValueError("Source and output must not overlap")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite: {destination}")
    replace_names = {resource.replace(".", "_").lower() for _, _, group in GROUPS for resource in group}
    # Copy only resource directories and leave known-bad ones out, so there is
    # no interval where corrected PNGs overwrite an existing user's source.
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*replace_names))
    repaired = []
    for palette_resource, scene, resources in GROUPS:
        for resource in resources:
            name = resource.replace(".", "_").lower()
            metadata = extract_resource(data_dir, resource, destination / name,
                                        palette_scene=scene, palette_resource=palette_resource)
            repaired.append({"resource": resource, "frames": metadata["frame_count"],
                             "fallback_palette": metadata["fallback_palette"]})
    result = {"source": str(source.resolve()), "data_dir": str(data_dir.resolve()),
              "resource_count": sum(1 for path in destination.iterdir() if (path / "metadata.json").exists()),
              "repaired": repaired,
              "note": "Fallback room palettes do not represent runtime palette changes."}
    (destination / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("extracted/sprites"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("scummvm"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.source_dir, args.output_dir, args.data_dir), indent=2))


if __name__ == "__main__":
    main()
