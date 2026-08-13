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
runs a real (non-generative) ESRGAN-family super-resolution model locally
via the `spandrel` model-loading library and PyTorch (CPU/CUDA/Apple `mps`),
which reconstructs plausible high-frequency detail without redrawing
content - appropriate for cleanly scaling up existing brush strokes.

This is entirely local/offline: no server process, no Automatic1111/Forge
WebUI install required - just `uv sync --extra esrgan` and a one-time model
weight download (see docs/esrgan-setup.md). `spandrel` loads the model's
native `.pth` checkpoint directly (no legacy `basicsr` dependency), so any
ESRGAN/RealESRGAN-family checkpoint works as a drop-in `--model-path`.

Since ESRGAN-family models expect an opaque RGB image, each frame's alpha
channel is separated first: the RGB is flattened onto a neutral fill color
before upscaling (so ESRGAN doesn't try to reconstruct "detail" out of fully
transparent regions), while the alpha channel is upscaled independently with
a smooth resize + threshold, keeping cutout edges crisp (matching how the
original engine's masks are always fully opaque or fully transparent, never
partially blended).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = ROOT / "models" / "esrgan" / "RealESRGAN_x4plus.pth"
FLATTEN_FILL = (128, 128, 128)
ALPHA_THRESHOLD = 128


def load_esrgan_model(model_path: Path, device: str):
    from spandrel import ModelLoader

    model = ModelLoader().load_from_file(str(model_path))
    return model.to(device).eval()


def run_esrgan(model, device: str, image: Image.Image) -> Image.Image:
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


def upscale_frame(frame: Image.Image, scale: int, model, device: str) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")

    flattened = Image.new("RGB", rgba.size, FLATTEN_FILL)
    flattened.paste(rgba, mask=alpha)

    upscaled_rgb = run_esrgan(model, device, flattened)
    target_size = (rgba.width * scale, rgba.height * scale)
    if upscaled_rgb.size != target_size:
        upscaled_rgb = upscaled_rgb.resize(target_size, Image.Resampling.LANCZOS)

    upscaled_alpha = alpha.resize(target_size, Image.Resampling.LANCZOS)
    upscaled_alpha = upscaled_alpha.point(lambda v: 255 if v >= ALPHA_THRESHOLD else 0)

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
) -> dict:
    metadata_path = input_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    output_dir.mkdir(parents=True, exist_ok=True)
    out_frames = []
    for frame_record in metadata["frames"]:
        frame_path = input_dir / frame_record["file"]
        with Image.open(frame_path) as frame:
            if mode == "font":
                upscaled = upscale_font_glyph(frame, scale)
            else:
                upscaled = upscale_frame(frame, scale, model, device)
        # Scale-qualified filename (frame_NNN@Sx.png), matching the
        # background pipeline's background@Nx.png convention - this lets the
        # engine's loadRoseTattooHiresCursorOverride() pick the exact scale
        # in play and lets multiple scales coexist under one output dir.
        scaled_name = f"{Path(frame_record['file']).stem}@{scale}x.png"
        out_path = output_dir / scaled_name
        upscaled.save(out_path)
        out_frames.append(
            {
                **frame_record,
                "file": scaled_name,
                "width": upscaled.width,
                "height": upscaled.height,
                "offset_x": frame_record["offset_x"] * scale,
                "offset_y": frame_record["offset_y"] * scale,
            }
        )

    out_metadata = {
        **metadata,
        "scale": scale,
        "upscaler": model_name if mode != "font" else "lanczos-alpha",
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
        "--mode",
        choices=["photo", "font"],
        default="photo",
        help=(
            "'photo' (default) upscales via the local ESRGAN model, suitable "
            "for cursors/items/characters. 'font' does a fast local "
            "Lanczos-smoothed alpha-only upscale with no model call, "
            "appropriate for tiny monochrome bitmap font glyphs "
            "(FONT1.VGS-FONT8.VGS) where photographic detail would blur "
            "thin strokes rather than help legibility."
        ),
    )
    args = parser.parse_args()

    if args.resources:
        resource_dirs = [args.input_dir / name for name in args.resources]
    else:
        resource_dirs = sorted(
            d for d in args.input_dir.iterdir() if d.is_dir() and (d / "metadata.json").exists()
        )

    model = None
    if args.mode != "font":
        if not args.model_path.exists():
            raise SystemExit(f"ESRGAN model not found: {args.model_path}. See docs/esrgan-setup.md.")
        print(f"Loading ESRGAN model from {args.model_path} onto {args.device} ...")
        model = load_esrgan_model(args.model_path, args.device)

    for resource_dir in resource_dirs:
        if not resource_dir.exists():
            print(f"  warning: {resource_dir} not found, skipping")
            continue
        output_dir = args.output_dir / resource_dir.name
        metadata = upscale_resource_dir(
            resource_dir,
            output_dir,
            args.scale,
            model,
            args.device,
            args.model_path.stem,
            mode=args.mode,
        )
        print(
            f"{resource_dir.name}: {len(metadata['frames'])} frames "
            f"upscaled {args.scale}x -> {output_dir}"
        )


if __name__ == "__main__":
    main()
