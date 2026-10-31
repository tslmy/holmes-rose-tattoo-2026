#!/usr/bin/env python3
"""Upscale extracted Rose Tattoo sprite/cursor/character frames.

Consumes the per-resource frame directories produced by
extract_rosetattoo_sprites.py (extracted/sprites/<resource>/frame_NNN.png +
metadata.json) and produces upscaled RGBA PNGs under a matching output tree,
preserving per-frame transparency and offsets.

Unlike the background pipeline (`flux_redraw_rosetattoo_backgrounds.py`),
this intentionally does NOT run a generative redraw pass: these are small,
silhouette-critical interactive elements (cursors, inventory items,
walk-cycle frames) where hallucinated new detail or a shifted
hotspot/hand-position would break gameplay recognizability. Instead this
runs a non-diffusion ESRGAN-family super-resolution model locally
via the `spandrel` model-loading library and PyTorch (CPU/CUDA/Apple `mps`),
which estimates plausible high-frequency detail without a text prompt. It can
still alter or invent small details, which require visual validation.

This is entirely local/offline: no server process, no Automatic1111/Forge
WebUI install required - just `uv sync --extra esrgan` and a one-time model
weight download (see docs/esrgan-setup.md). `spandrel` loads the model's
native `.pth` checkpoint directly (no legacy `basicsr` dependency), so any
ESRGAN/RealESRGAN-family checkpoint works as a drop-in `--model-path`.

Since ESRGAN-family models expect an opaque RGB image, the alpha channel is
upscaled separately. Nearby opaque colors are extended into transparent pixels
before inference to avoid gray fringes when a soft alpha edge is composited.
The legacy binary mask remains the default; ``--alpha-mode soft`` generates
antialiased edges for a renderer that supports partial alpha.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

from sprite_metadata import resource_metadata_paths

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = ROOT / "models" / "esrgan" / "RealESRGAN_x4plus.pth"
FLATTEN_FILL = (128, 128, 128)
ALPHA_THRESHOLD = 128


def resource_mode(mode: str, resource_dir: Path) -> str:
    if mode != "auto":
        return mode
    return "font" if re.fullmatch(r"font[1-8]_vgs", resource_dir.name, re.IGNORECASE) else "photo"


def load_esrgan_model(model_path: Path, device: str):
    from spandrel import ModelLoader

    model = ModelLoader().load_from_file(str(model_path))
    return model.to(device).eval()


def run_esrgan_whole(model, device: str, image: Image.Image) -> Image.Image:
    """Runs one RGB PIL image through a spandrel-loaded ESRGAN model.

    Returns an image upscaled by the model's own native scale factor (e.g.
    4x for RealESRGAN_x4plus) - callers resize to the requested --scale
    afterwards if it differs.
    """
    import torch

    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)
    with torch.no_grad():
        output = model(tensor)
    output = output.clamp(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()
    return Image.fromarray((output * 255.0 + 0.5).astype(np.uint8), mode="RGB")


def run_esrgan(model, device: str, image: Image.Image, tile_size: int = 0, tile_pad: int = 16) -> Image.Image:
    """Optionally infer overlapping source tiles to bound accelerator memory."""
    if tile_size <= 0 or (image.width <= tile_size and image.height <= tile_size):
        return run_esrgan_whole(model, device, image)
    if tile_pad < 0:
        raise ValueError("tile padding must be nonnegative")
    output = None
    native_scale = None
    for y in range(0, image.height, tile_size):
        for x in range(0, image.width, tile_size):
            end_x, end_y = min(x + tile_size, image.width), min(y + tile_size, image.height)
            left, top = max(x - tile_pad, 0), max(y - tile_pad, 0)
            right, bottom = min(end_x + tile_pad, image.width), min(end_y + tile_pad, image.height)
            tile = image.crop((left, top, right, bottom))
            enhanced = run_esrgan_whole(model, device, tile)
            if enhanced.width % tile.width or enhanced.height % tile.height:
                raise ValueError("Model produced a non-integer tile scale")
            scale_x, scale_y = enhanced.width // tile.width, enhanced.height // tile.height
            if scale_x != scale_y or (native_scale is not None and native_scale != scale_x):
                raise ValueError("Model produced inconsistent tile scales")
            if output is None:
                native_scale = scale_x
                output = Image.new("RGB", (image.width * scale_x, image.height * scale_y))
            core = enhanced.crop(((x - left) * scale_x, (y - top) * scale_y,
                                  (end_x - left) * scale_x, (end_y - top) * scale_y))
            output.paste(core, (x * scale_x, y * scale_y))
    if output is None:
        raise ValueError("Cannot upscale an empty image")
    return output


def extend_transparent_rgb(rgba: Image.Image, radius: int = 3) -> Image.Image:
    """Extend foreground colors just beyond the cutout before RGB inference.

    The model has no alpha channel. A neutral fill immediately adjacent to an
    opaque pixel would be mixed into a soft edge and leave a visible halo.
    Dilation only supplies context; it never changes the independently resized
    alpha mask or the source pixels inside the cutout.
    """
    source = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)
    rgb = source[..., :3].copy()
    known = source[..., 3] != 0
    rgb[~known] = FLATTEN_FILL
    for _ in range(radius):
        padded_rgb = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
        padded_known = np.pad(known, ((1, 1), (1, 1)), mode="constant")
        height, width = known.shape
        new_rgb = rgb.copy()
        new_known = known.copy()
        for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            neighbour_known = padded_known[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
            take = ~new_known & neighbour_known
            neighbour_rgb = padded_rgb[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
            new_rgb[take] = neighbour_rgb[take]
            new_known[take] = True
        rgb, known = new_rgb, new_known
    return Image.fromarray(rgb, mode="RGB")


def upscale_frame(frame: Image.Image, scale: int, model, device: str, alpha_mode: str = "binary",
                  tile_size: int = 0, tile_pad: int = 16) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")

    flattened = extend_transparent_rgb(rgba)

    upscaled_rgb = (run_esrgan(model, device, flattened, tile_size=tile_size, tile_pad=tile_pad)
                    if tile_size else run_esrgan(model, device, flattened))
    target_size = (rgba.width * scale, rgba.height * scale)
    if upscaled_rgb.size != target_size:
        upscaled_rgb = upscaled_rgb.resize(target_size, Image.Resampling.LANCZOS)

    upscaled_alpha = alpha.resize(target_size, Image.Resampling.LANCZOS)
    if alpha_mode == "binary":
        upscaled_alpha = upscaled_alpha.point(lambda v: 255 if v >= ALPHA_THRESHOLD else 0)
    elif alpha_mode != "soft":
        raise ValueError(f"unknown alpha mode: {alpha_mode}")

    result = upscaled_rgb.convert("RGBA")
    result.putalpha(upscaled_alpha)
    return result


def upscale_font_glyph(frame: Image.Image, scale: int) -> Image.Image:
    """Upscales a single bitmap-font glyph frame without calling any AI
    upscaler.

    Font glyphs (FONT1.VGS..FONT8.VGS) are tiny (often under 10x10px)
    monochrome stencils - the glyph "shape" lives entirely in the alpha
    channel (RGB is always solid black, since the engine recolors glyphs
    via whichever palette/color is active at draw time - see
    Fonts::writeString() in fonts.cpp). Running these through a
    photographic ESRGAN model would blur or hallucinate texture into
    strokes that are only 1-2 pixels wide; a plain smooth (Lanczos) resize
    of just the alpha channel instead produces clean anti-aliased edges
    while keeping the glyph recognizable, matching how vector/hinted fonts
    look when rendered at a larger size.
    """
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    target_size = (rgba.width * scale, rgba.height * scale)
    upscaled_alpha = alpha.resize(target_size, Image.Resampling.LANCZOS)

    result = Image.new("RGBA", target_size, (255, 255, 255, 0))
    result.putalpha(upscaled_alpha)
    return result


def upscale_resource_dir(
    input_dir: Path,
    output_dir: Path,
    scale: int,
    model,
    device: str,
    model_name: str,
    mode: str = "photo",
    alpha_mode: str = "binary",
    skip_existing: bool = False,
    model_sha256: str | None = None,
    frame_cache: dict[tuple, Path] | None = None,
    tile_size: int = 0,
    tile_pad: int = 16,
) -> dict:
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("input and output resource directories must differ")
    metadata_path = input_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    output_dir.mkdir(parents=True, exist_ok=True)
    signature = {"scale": scale, "upscaler": model_name if mode != "font" else "lanczos-alpha",
                 "alpha_mode": alpha_mode if mode != "font" else "soft",
                 "model_sha256": model_sha256 if mode != "font" else None}
    if tile_size and mode != "font":
        signature.update({"tile_size": tile_size, "tile_pad": tile_pad})
    output_metadata_path = output_dir / "metadata.json"
    if skip_existing and not output_metadata_path.exists() and any(output_dir.glob("frame_*@*x.png")):
        raise ValueError(f"Cannot resume {output_dir}: existing frames have no generation metadata")
    if skip_existing and output_metadata_path.exists():
        previous = json.loads(output_metadata_path.read_text(encoding="utf-8"))
        for key, value in signature.items():
            if previous.get(key) != value:
                raise ValueError(f"Cannot resume {output_dir}: {key} differs from previous run")
    # Record the configuration before writing any frame so an interrupted run
    # cannot be resumed with a different scale, model, or alpha policy.
    (output_dir / "metadata.json").write_text(
        json.dumps({**metadata, **signature, "complete": False}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_frames = []
    for frame_record in metadata["frames"]:
        frame_path = input_dir / frame_record["file"]
        # Scale-qualified filename (frame_NNN@Sx.png), matching the
        # background pipeline's background@Nx.png convention - this lets the
        # engine's loadRoseTattooHiresCursorOverride() pick the exact scale
        # in play and lets multiple scales coexist under one output dir.
        scaled_name = f"{Path(frame_record['file']).stem}@{scale}x.png"
        out_path = output_dir / scaled_name
        expected_size = (frame_record["width"] * scale, frame_record["height"] * scale)
        with Image.open(frame_path) as source:
            frame = source.convert("RGBA")
        if frame.size != (frame_record["width"], frame_record["height"]):
            raise ValueError(f"{frame_path}: source dimensions differ from metadata")
        digest = hashlib.sha256()
        digest.update(f"{frame.width}x{frame.height}:".encode("ascii"))
        digest.update(frame.tobytes())
        source_hash = digest.hexdigest()
        key = (source_hash, scale, model_name, model_sha256, mode, alpha_mode, tile_size, tile_pad)
        valid_existing = False
        if skip_existing and out_path.exists():
            try:
                with Image.open(out_path) as existing:
                    existing.load()
                    valid_existing = (existing.size == expected_size and existing.mode == "RGBA"
                                      and existing.info.get("source_sha256") == source_hash)
            except (OSError, ValueError):
                pass
        if not valid_existing:
            temporary_path = out_path.with_suffix(".png.tmp")
            cached = frame_cache.get(key) if frame_cache is not None else None
            if cached is not None and cached.is_file() and cached != out_path:
                shutil.copyfile(cached, temporary_path)
            else:
                if frame.getchannel("A").getbbox() is None:
                    # Several game resources contain invisible placeholder
                    # frames. Their dimensions and index still matter, but
                    # an RGB model cannot improve a fully transparent image.
                    upscaled = Image.new("RGBA", expected_size, (0, 0, 0, 0))
                elif mode == "font":
                    upscaled = upscale_font_glyph(frame, scale)
                else:
                    upscaled = upscale_frame(frame, scale, model, device, alpha_mode, tile_size, tile_pad)
                if upscaled.size != expected_size:
                    raise ValueError(f"{frame_path}: expected {expected_size}, got {upscaled.size}")
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text("source_sha256", source_hash)
                upscaled.save(temporary_path, format="PNG", pnginfo=pnginfo)
            temporary_path.replace(out_path)
        if frame_cache is not None:
            frame_cache[key] = out_path
        out_frames.append(
            {
                **frame_record,
                "file": scaled_name,
                "width": expected_size[0],
                "height": expected_size[1],
                "offset_x": frame_record["offset_x"] * scale,
                "offset_y": frame_record["offset_y"] * scale,
            }
        )

    out_metadata = {
        **metadata,
        **signature,
        "complete": True,
        "frames": out_frames,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(out_metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Upscale extracted Rose Tattoo sprite/cursor/character frames via a "
            "local ESRGAN-family real super-resolution model (spandrel + "
            "PyTorch), preserving per-frame transparency and offsets."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "extracted" / "sprites",
        help="Root directory of extract_rosetattoo_sprites.py output.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "enhanced" / "sprites",
        help="Directory to write upscaled frame PNGs and metadata into.",
    )
    parser.add_argument(
        "--resources",
        nargs="+",
        default=None,
        help=(
            "Resource subdirectory names under --input-dir to upscale "
            "(e.g. rmouse_vgs omouse_vgs watson_vgs). Omit to upscale every "
            "resource directory found under --input-dir."
        ),
    )
    parser.add_argument("--scale", type=int, default=4, help="Upscale factor (default: 4x).")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=DEFAULT_MODEL_PATH,
        help="Local ESRGAN-family .pth checkpoint (see docs/esrgan-setup.md).",
    )
    parser.add_argument("--device", default="mps", help="torch device: mps, cuda, or cpu.")
    parser.add_argument(
        "--alpha-mode", choices=["binary", "soft"], default="binary",
        help="Cutout policy: legacy binary mask or antialiased soft alpha (requires compatible renderer).",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Resume a matching interrupted run.")
    parser.add_argument("--tile-size", type=int, default=0,
                        help="Maximum source pixels per tile, or 0 for untiled inference (default).")
    parser.add_argument("--tile-pad", type=int, default=16,
                        help="Overlapping source pixels around each inference tile (default: 16).")
    parser.add_argument(
        "--mode",
        choices=["photo", "font", "auto"],
        default="photo",
        help=(
            "'photo' (default) upscales via the local ESRGAN model, suitable "
            "for cursors/items/characters. 'font' does a fast local "
            "Lanczos-smoothed alpha-only upscale with no model call, "
            "appropriate for tiny monochrome bitmap font glyphs "
            "(FONT1.VGS-FONT8.VGS) where photographic detail would blur "
            "thin strokes rather than help legibility. 'auto' selects font "
            "mode for FONT1.VGS-FONT8.VGS and photo mode for other resources."
        ),
    )
    args = parser.parse_args()
    if args.scale < 1:
        parser.error("--scale must be >= 1")
    if args.tile_size < 0 or args.tile_pad < 0:
        parser.error("--tile-size and --tile-pad must be nonnegative")
    input_root, output_root = args.input_dir.resolve(), args.output_dir.resolve()
    if input_root == output_root or output_root.is_relative_to(input_root) or input_root.is_relative_to(output_root):
        parser.error("--input-dir and --output-dir must not overlap")

    if args.resources:
        resource_dirs = [args.input_dir / name for name in args.resources]
    else:
        resource_dirs = [path.parent for path in resource_metadata_paths(args.input_dir)]

    model = None
    model_sha256 = None
    if args.mode != "font":
        if not args.model_path.exists():
            raise SystemExit(f"ESRGAN model not found: {args.model_path}. See docs/esrgan-setup.md.")
        print(f"Loading ESRGAN model from {args.model_path} onto {args.device} ...")
        with args.model_path.open("rb") as model_file:
            model_sha256 = hashlib.file_digest(model_file, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else None
        if model_sha256 is None:
            # Python 3.10 does not have hashlib.file_digest.
            digest = hashlib.sha256()
            with args.model_path.open("rb") as model_file:
                for block in iter(lambda: model_file.read(1024 * 1024), b""):
                    digest.update(block)
            model_sha256 = digest.hexdigest()
        model = load_esrgan_model(args.model_path, args.device)

    frame_cache: dict[tuple, Path] = {}
    for resource_dir in resource_dirs:
        if not resource_dir.exists():
            print(f"  warning: {resource_dir} not found, skipping")
            continue
        output_dir = args.output_dir / resource_dir.relative_to(args.input_dir)
        metadata = upscale_resource_dir(
            resource_dir,
            output_dir,
            args.scale,
            model,
            args.device,
            args.model_path.stem,
            mode=resource_mode(args.mode, resource_dir),
            alpha_mode=args.alpha_mode,
            skip_existing=args.skip_existing,
            model_sha256=model_sha256,
            frame_cache=frame_cache,
            tile_size=args.tile_size,
            tile_pad=args.tile_pad,
        )
        print(
            f"{resource_dir.relative_to(args.input_dir)}: {len(metadata['frames'])} frames "
            f"upscaled {args.scale}x -> {output_dir}"
        )


if __name__ == "__main__":
    main()
