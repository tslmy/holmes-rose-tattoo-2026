#!/usr/bin/env python3
"""Refine sprite silhouettes without rerunning the RGB upscaling model.

This optional experiment rebuilds alpha from the original indexed cutout,
optionally using a geometric Scale4x mask, then applies a small feather.
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


def scale2x_mask(image: np.ndarray) -> np.ndarray:
    """Apply the geometry-only Scale2x rule to a binary alpha array."""
    padded = np.pad(image, 1, mode="edge")
    center = padded[1:-1, 1:-1]
    north, west = padded[:-2, 1:-1], padded[1:-1, :-2]
    east, south = padded[1:-1, 2:], padded[2:, 1:-1]
    eligible = (north != south) & (west != east)
    result = np.empty((image.shape[0] * 2, image.shape[1] * 2), dtype=image.dtype)
    result[0::2, 0::2] = np.where(eligible & (west == north), west, center)
    result[0::2, 1::2] = np.where(eligible & (north == east), east, center)
    result[1::2, 0::2] = np.where(eligible & (west == south), west, center)
    result[1::2, 1::2] = np.where(eligible & (south == east), east, center)
    return result


def refine_alpha(source_alpha: Image.Image, target_size: tuple[int, int], radius: float,
                 thin_guard: bool = False, method: str = "gaussian",
                 preserve_support: bool = False) -> Image.Image:
    """Feather a native mask; optionally preserve short opaque/empty runs.

    The guard is a conservative heuristic, not a topology guarantee. It
    protects one- and two-pixel runs on either axis and softly tapers nearby.
    """
    if method not in ("gaussian", "scale4-box"):
        raise ValueError(f"unknown alpha refinement method: {method}")
    if method == "scale4-box" and target_size != (source_alpha.width * 2, source_alpha.height * 2):
        raise ValueError("scale4-box requires 2x output")
    baseline = source_alpha.resize(target_size, Image.Resampling.LANCZOS)
    if source_alpha.getextrema() in ((0, 0), (255, 255)):
        # A fully transparent/opaque frame has no contour to refine. This
        # also avoids huge temporary Scale4x arrays for opaque maps/stills.
        return baseline
    if method == "gaussian":
        candidate = baseline
    elif method == "scale4-box":
        native = np.asarray(source_alpha, dtype=np.uint8)
        if not np.isin(native, (0, 255)).all():
            raise ValueError("scale4-box requires binary native alpha")
        candidate = Image.fromarray(scale2x_mask(scale2x_mask(native)), mode="L")
        candidate = candidate.resize(target_size, Image.Resampling.BOX)
    if method == "gaussian" and not radius:
        return baseline
    feathered = candidate.filter(ImageFilter.GaussianBlur(radius)) if radius else candidate
    if not thin_guard:
        result = feathered
    else:
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
        result = Image.composite(baseline, feathered, guard)
    if preserve_support:
        # The renderer uses nonzero nearest-sampled alpha when recording scene
        # ownership. Keep that boolean coverage identical to the existing
        # Lanczos mask, while still allowing opacity changes inside it.
        support = np.asarray(baseline, dtype=np.uint8) != 0
        alpha = np.asarray(result, dtype=np.uint8)
        result = Image.fromarray(np.where(support, np.maximum(alpha, 1), 0).astype(np.uint8), mode="L")
    return result


def refine_resource(source_dir: Path, input_dir: Path, output_dir: Path,
                    scale: int, radius: float, skip_existing: bool = False,
                    thin_guard: bool = False, method: str = "gaussian",
                    preserve_support: bool = False) -> dict:
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
    if method not in ("gaussian", "scale4-box"):
        raise ValueError(f"unknown alpha refinement method: {method}")
    signature = {"method": ("preserve-font-alpha" if is_font else
                            "native-alpha-lanczos-gaussian" if method == "gaussian" else "native-alpha-scale4-box"),
                 "radius": 0.0 if is_font else radius, "scale": scale,
                 "thin_guard": False if is_font else thin_guard,
                 "preserve_support": False if is_font else preserve_support}
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
        if not is_font and (preserve_support or method == "scale4-box"):
            baseline = source.getchannel("A").resize(expected_size, Image.Resampling.LANCZOS)
            if baseline.tobytes() != enhanced.getchannel("A").tobytes():
                raise ValueError(f"{input_path}: expected unmodified Lanczos alpha")
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
                     refine_alpha(source.getchannel("A"), expected_size, radius, thin_guard, method, preserve_support))
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
    parser.add_argument("--radius", type=float, required=True, help="Gaussian feather in output pixels; 0 leaves the chosen reconstruction unblurred.")
    parser.add_argument("--method", choices=["gaussian", "scale4-box"], default="gaussian",
                        help="Alpha reconstruction method; scale4-box is an experimental geometric 2x contour.")
    parser.add_argument("--thin-guard", action="store_true", help="Experimentally preserve 1–2 native-pixel strokes and gaps.")
    parser.add_argument("--preserve-support", action="store_true",
                        help="Keep the original nonzero-alpha coverage used by the renderer's provenance checks.")
    parser.add_argument("--skip-existing", action="store_true", help="Resume a matching interrupted run.")
    args = parser.parse_args()
    if args.scale < 1 or not 0 <= args.radius <= 2:
        parser.error("--scale must be >= 1 and --radius must be between 0 and 2")
    if args.method == "scale4-box" and args.scale != 2:
        parser.error("--method scale4-box requires --scale 2")
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
                                 args.output_dir / relative, args.scale, args.radius, args.skip_existing,
                                 args.thin_guard, args.method, args.preserve_support)
        print(f"{relative}: {len(result['frames'])} alpha masks refined", flush=True)


if __name__ == "__main__":
    main()
