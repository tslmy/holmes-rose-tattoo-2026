#!/usr/bin/env python3
"""Refine sprite silhouettes without rerunning the RGB upscaling model.

This optional experiment rebuilds alpha from the original indexed cutout,
then applies a configurable small Gaussian feather at output resolution.
It copies RGB pixels unchanged and preserves source hashes and sidecars. It
does not modify input trees, frame indices, offsets, or gameplay geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, PngImagePlugin

from sprite_metadata import resource_metadata_paths


def source_digest(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    digest = hashlib.sha256()
    digest.update(f"{rgba.width}x{rgba.height}:".encode("ascii"))
    digest.update(rgba.tobytes())
    return digest.hexdigest()


def safe_sidecars(metadata: dict, directory: Path) -> list[str]:
    names = metadata.get("sidecars", [])
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError(f"{directory}: invalid sidecar list")
    for name in names:
        if Path(name).name != name or name == "metadata.json" or name.startswith("frame_"):
            raise ValueError(f"{directory}: unsafe sidecar name {name!r}")
    return names


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def refine_alpha(source_alpha: Image.Image, target_size: tuple[int, int], radius: float,
                 thin_guard: bool = False) -> Image.Image:
    """Feather a native mask; optionally preserve short opaque/empty runs.

    The guard is a conservative heuristic, not a topology guarantee. It
    protects one- and two-pixel runs on either axis and softly tapers nearby.
    """
    baseline = source_alpha.resize(target_size, Image.Resampling.LANCZOS)
    if not radius:
        return baseline
    feathered = baseline.filter(ImageFilter.GaussianBlur(radius))
    if not thin_guard:
        return feathered
    opaque = np.asarray(source_alpha, dtype=np.uint8) != 0
    def short_runs(mask: np.ndarray) -> np.ndarray:
        padded = np.pad(mask, 2, constant_values=False)
        left, right = padded[2:-2, 1:-3], padded[2:-2, 3:-1]
        up, down = padded[1:-3, 2:-2], padded[3:-1, 2:-2]
        left2, right2 = padded[2:-2, :-4], padded[2:-2, 4:]
        up2, down2 = padded[:-4, 2:-2], padded[4:, 2:-2]
        thin_x = (~left & (~right | ~right2)) | (~right & (~left | ~left2))
        thin_y = (~up & (~down | ~down2)) | (~down & (~up | ~up2))
        return mask & (thin_x | thin_y)
    marked = short_runs(opaque) | short_runs(~opaque)
    guard = Image.fromarray(marked.astype(np.uint8) * 255, mode="L")
    guard = guard.resize(target_size, Image.Resampling.NEAREST)
    guard = guard.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.0))
    return Image.composite(baseline, feathered, guard)


def refine_resource(source_dir: Path, input_dir: Path, output_dir: Path,
                    scale: int, radius: float, skip_existing: bool = False,
                    thin_guard: bool = False) -> dict:
    if scale < 1 or radius < 0:
        raise ValueError("scale must be positive and radius nonnegative")
    if output_dir.resolve() in (source_dir.resolve(), input_dir.resolve()):
        raise ValueError("output must differ from both inputs")
    source_metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
    input_metadata = json.loads((input_dir / "metadata.json").read_text(encoding="utf-8"))
    if input_metadata.get("complete") is not True or input_metadata.get("scale") != scale:
        raise ValueError(f"{input_dir}: incomplete or wrong-scale upscale")
    sidecars = safe_sidecars(source_metadata, source_dir)
    if safe_sidecars(input_metadata, input_dir) != sidecars:
        raise ValueError(f"{input_dir}: sidecar metadata mismatch")
    for name in sidecars:
        if (source_dir / name).read_bytes() != (input_dir / name).read_bytes():
            raise ValueError(f"{input_dir}: sidecar {name} differs from source")

    records = source_metadata["frames"]
    output_metadata_path = output_dir / "metadata.json"
    is_font = input_metadata.get("upscaler") == "lanczos-alpha"
    signature = {"method": "preserve-font-alpha" if is_font else "native-alpha-lanczos-gaussian",
                 "radius": 0.0 if is_font else radius, "scale": scale,
                 "thin_guard": False if is_font else thin_guard}
    if skip_existing and output_metadata_path.exists():
        previous = json.loads(output_metadata_path.read_text(encoding="utf-8"))
        if previous.get("alpha_refinement") != signature:
            raise ValueError(f"{output_dir}: alpha-refinement settings differ")
    elif skip_existing and output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"{output_dir}: existing files have no refinement metadata")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_metadata_path, {**input_metadata, "complete": False,
                                             "alpha_refinement": signature})
    for name in sidecars:
        target = output_dir / name
        temporary = target.with_name(target.name + ".tmp")
        shutil.copyfile(input_dir / name, temporary)
        temporary.replace(target)

    output_records = []
    for record in records:
        source_path = source_dir / record["file"]
        name = f"{Path(record['file']).stem}@{scale}x.png"
        input_path, output_path = input_dir / name, output_dir / name
        with Image.open(source_path) as source_file:
            source = source_file.convert("RGBA")
        expected_native = (record["width"], record["height"])
        if source.size != expected_native:
            raise ValueError(f"{source_path}: dimensions differ from extraction metadata")
        expected_size = (source.width * scale, source.height * scale)
        source_hash = source_digest(source)
        input_bytes = input_path.read_bytes()
        input_hash = hashlib.sha256(input_bytes).hexdigest()
        with Image.open(input_path) as input_image:
            input_image.load()
            if input_image.mode != "RGBA" or input_image.size != expected_size:
                raise ValueError(f"{input_path}: incorrect mode or dimensions")
            if input_image.info.get("source_sha256") != source_hash:
                raise ValueError(f"{input_path}: source hash missing or mismatched")
            enhanced = input_image.copy()
        reusable = False
        if skip_existing and output_path.exists():
            try:
                with Image.open(output_path) as old:
                    old.load()
                    reusable = (old.mode == "RGBA" and old.size == expected_size
                                and old.info.get("source_sha256") == source_hash
                                and old.info.get("alpha_input_sha256") == input_hash)
            except (OSError, ValueError):
                pass
        if not reusable:
            alpha = (enhanced.getchannel("A") if is_font else
                     refine_alpha(source.getchannel("A"), expected_size, radius, thin_guard))
            enhanced.putalpha(alpha)
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("source_sha256", source_hash)
            pnginfo.add_text("alpha_input_sha256", input_hash)
            temporary = output_path.with_suffix(".png.tmp")
            enhanced.save(temporary, format="PNG", pnginfo=pnginfo)
            temporary.replace(output_path)
        output_records.append({**record, "file": name, "width": expected_size[0], "height": expected_size[1],
                               "offset_x": record["offset_x"] * scale, "offset_y": record["offset_y"] * scale})

    result = {**input_metadata, "complete": True, "alpha_mode": "soft", "alpha_refinement": signature,
              "frames": output_records}
    write_json_atomic(output_metadata_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resources", nargs="+", help="Relative resource paths; default: all source manifests.")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--radius", type=float, required=True, help="Gaussian feather in output pixels; 0 reproduces the Lanczos mask.")
    parser.add_argument("--thin-guard", action="store_true", help="Experimentally preserve 1–2 native-pixel strokes and gaps.")
    parser.add_argument("--skip-existing", action="store_true", help="Resume a matching interrupted run.")
    args = parser.parse_args()
    if args.scale < 1 or not 0 <= args.radius <= 2:
        parser.error("--scale must be >= 1 and --radius must be between 0 and 2")
    output = args.output_dir.resolve()
    for root in (args.source_dir.resolve(), args.input_dir.resolve()):
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            parser.error("--output-dir must not overlap either input tree")
    if args.output_dir.exists() and not args.skip_existing:
        parser.error("--output-dir already exists; choose a new directory or use --skip-existing")
    resources = args.resources or [str(path.parent.relative_to(args.source_dir))
                                  for path in resource_metadata_paths(args.source_dir)]
    for name in resources:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            parser.error(f"unsafe resource path: {name}")
        result = refine_resource(args.source_dir / relative, args.input_dir / relative,
                                 args.output_dir / relative, args.scale, args.radius, args.skip_existing, args.thin_guard)
        print(f"{relative}: {len(result['frames'])} alpha masks refined", flush=True)


if __name__ == "__main__":
    main()
