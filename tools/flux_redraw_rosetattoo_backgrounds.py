#!/usr/bin/env python3
"""Neural photoreal redraw of Rose Tattoo backgrounds using FLUX.1 + diffusers.

This tool loads a FLUX.1 pipeline directly in-process with Hugging Face
`diffusers`, using Apple's Metal Performance Shaders (`mps`) backend so it
runs entirely on Apple Silicon GPU cores without any external server.

To fit comfortably in 32GB of unified memory (and a constrained local disk),
the diffusion transformer is loaded from a GGUF-quantized checkpoint
(city96's FLUX.1-schnell GGUF conversion, default Q4_K_S ~6.8GB) via
diffusers' `GGUFQuantizationConfig`, while the CLIP/T5 text encoders and VAE
are loaded at full fp16 precision from a non-gated mirror of the official
`black-forest-labs/FLUX.1-schnell` repo (`unsloth/FLUX.1-schnell`), since
those components are comparatively small and quantizing them would degrade
prompt fidelity (T5) or color accuracy (VAE) disproportionately.

FLUX.1-schnell is a distilled, CFG-free, few-step (1-4) model, so this tool
runs img2img (`FluxImg2ImgPipeline`), using the upscaled original background
as the init image and a low `--strength` to keep geometry, walk zones, and
puzzle-relevant object silhouettes - and small embedded text like signage -
intact while adding photoreal material/lighting detail. No ControlNet or
edge-map preprocessing is needed to hold structure at this low strength.

Usage:
    python3 tools/flux_redraw_rosetattoo_backgrounds.py --scenes 1 18 36 --scale 2
    python3 tools/flux_redraw_rosetattoo_backgrounds.py --scenes 1 18 36 \
        --scummvm-overrides mods/flux-hires-backgrounds

Setup (one-time, see docs/flux-setup.md):
    uv sync --extra flux
    # Download components into models/flux-schnell + models/flux-schnell-gguf/
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

DEFAULT_MODEL_DIR = Path("models/flux-schnell")
DEFAULT_GGUF = Path("models/flux-schnell-gguf/flux1-schnell-Q4_K_S.gguf")

STYLE_PROMPT = (
    "Faithful high-resolution photographic restoration of a 1996 hand-painted "
    "point-and-click adventure game background. Preserve the exact composition, "
    "camera angle, geometry, walkable paths, doorways, props, and every "
    "puzzle-relevant object and silhouette. Add realistic material texture, "
    "grime, period-accurate Victorian lighting, and true photographic tonal "
    "depth. Do not invent new readable text, signage, people, doors, exits, "
    "or clues; do not change architecture or object placement."
)


def scene_dirs(root: Path, selected: list[int] | None) -> list[Path]:
    if selected:
        return [root / f"scene_{scene_id:02d}" for scene_id in selected]
    return sorted(p for p in root.glob("scene_*") if p.is_dir())


def scene_id_from_dir(scene_dir: Path) -> int:
    return int(scene_dir.name.split("_", 1)[1])


def load_metadata(scene_dir: Path) -> dict[str, Any]:
    metadata_path = scene_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def load_scene_prompt_text(scene_dir: Path, prompt_brief_dir: Path | None, scene_id: int) -> str:
    if prompt_brief_dir:
        for dirname in (f"scene_{scene_id:03d}", f"scene_{scene_id:02d}", f"scene_{scene_id}"):
            brief_path = prompt_brief_dir / dirname / "visual_brief.txt"
            if brief_path.exists():
                return brief_path.read_text(encoding="utf-8").strip()
    prompt_path = scene_dir / "prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return ""


def make_prompt(scene_prompt: str) -> str:
    # Keep the combined prompt short - FLUX's T5 encoder is more literal than
    # SDXL's CLIP and long game-text blurbs (character backstories, etc.) can
    # crowd out the structural/style instructions that matter most here.
    trimmed = "\n".join(scene_prompt.splitlines()[:6]).strip()
    return f"{STYLE_PROMPT}\n\n{trimmed}".strip()


def round_to_multiple(value: int, multiple: int = 16) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def build_init_image(background: Image.Image, scale: float) -> Image.Image:
    width = round_to_multiple(int(background.width * scale))
    height = round_to_multiple(int(background.height * scale))
    return background.convert("RGB").resize((width, height), Image.LANCZOS)


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


def build_contact_sheet(pairs: list[tuple[int, Image.Image, Image.Image]], output_path: Path) -> None:
    """Two-column (original | FLUX redraw) contact sheet, one row per scene."""
    if not pairs:
        return
    thumb_width = 480
    label_height = 22
    padding = 6
    font = load_font(15)

    row_images = []
    row_height = 0
    for scene_id, original, redraw in pairs:
        orig_thumb = original.resize((thumb_width, int(thumb_width * original.height / original.width)), Image.LANCZOS)
        redraw_thumb = redraw.resize((thumb_width, int(thumb_width * redraw.height / redraw.width)), Image.LANCZOS)
        h = max(orig_thumb.height, redraw_thumb.height)
        row_height = max(row_height, h)
        row_images.append((scene_id, orig_thumb, redraw_thumb, h))

    total_width = padding + (thumb_width + padding) * 2
    total_height = padding + sum(label_height + h + padding for _, _, _, h in row_images)
    sheet = Image.new("RGB", (total_width, total_height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)

    y = padding
    for scene_id, orig_thumb, redraw_thumb, h in row_images:
        draw.text((padding, y), f"scene {scene_id:02d} - original", fill=(230, 230, 230), font=font)
        draw.text((padding * 2 + thumb_width, y), f"scene {scene_id:02d} - FLUX redraw", fill=(230, 230, 230), font=font)
        y += label_height
        sheet.paste(orig_thumb, (padding, y))
        sheet.paste(redraw_thumb, (padding * 2 + thumb_width, y))
        y += h + padding

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    print(f"Wrote contact sheet {output_path} ({total_width}x{total_height}, {len(pairs)} scenes)")


def load_pipeline(model_dir: Path, gguf_path: Path, device: str):
    import torch
    from diffusers import FluxImg2ImgPipeline, FluxTransformer2DModel, GGUFQuantizationConfig

    print(f"Loading GGUF-quantized transformer from {gguf_path} ...")
    transformer_config_dir = model_dir / "transformer"
    transformer = FluxTransformer2DModel.from_single_file(
        str(gguf_path),
        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
        torch_dtype=torch.bfloat16,
        config=str(transformer_config_dir) if (transformer_config_dir / "config.json").exists() else None,
    )

    print(f"Loading text encoders / VAE / tokenizers from {model_dir} ...")
    pipe = FluxImg2ImgPipeline.from_pretrained(
        str(model_dir),
        transformer=transformer,
        torch_dtype=torch.bfloat16,
    )
    pipe.to(device)
    pipe.enable_attention_slicing()
    return pipe


def process_scene(
    pipe,
    scene_dir: Path,
    output_root: Path,
    scale: float,
    strength: float,
    steps: int,
    seed: int,
    prompt_brief_dir: Path | None,
    skip_existing: bool,
) -> tuple[Image.Image, Image.Image] | None:
    import torch

    scene_id = scene_id_from_dir(scene_dir)
    background_path = scene_dir / "background.png"
    if not background_path.exists():
        print(f"  scene {scene_id}: no background.png, skipping")
        return None

    scene_output_dir = output_root / scene_dir.name
    output_path = scene_output_dir / f"background_flux@{scale:g}x.png"
    if skip_existing and output_path.exists():
        print(f"  scene {scene_id}: {output_path} already exists, skipping (--skip-existing)")
        original = Image.open(background_path).convert("RGB")
        redraw = Image.open(output_path).convert("RGB")
        return original, redraw

    original = Image.open(background_path).convert("RGB")
    init_image = build_init_image(original, scale)

    scene_prompt = load_scene_prompt_text(scene_dir, prompt_brief_dir, scene_id)
    prompt = make_prompt(scene_prompt)

    generator = torch.Generator(device="cpu").manual_seed(seed)

    print(f"  scene {scene_id}: generating at {init_image.width}x{init_image.height} "
          f"(strength={strength}, steps={steps}) ...")
    started = time.monotonic()
    result = pipe(
        prompt=prompt,
        image=init_image,
        height=init_image.height,
        width=init_image.width,
        strength=strength,
        num_inference_steps=steps,
        guidance_scale=0.0,  # FLUX.1-schnell is a CFG-free distilled model.
        generator=generator,
    )
    redraw = result.images[0]
    elapsed = time.monotonic() - started
    print(f"  scene {scene_id}: done in {elapsed:.1f}s")
    if redraw.size != init_image.size:
        # Defensive safety net: FluxImg2ImgPipeline silently defaults height/
        # width to 1024x1024 (its default_sample_size * vae_scale_factor)
        # whenever they're omitted, ignoring the init image's own size - we
        # pass them explicitly above, but resize here too in case a future
        # diffusers version reintroduces its own internal rounding/cropping,
        # so ScummVM never sees an override with unexpected dimensions.
        print(f"  scene {scene_id}: resizing {redraw.size} -> {init_image.size} "
              f"to match the requested init size")
        redraw = redraw.resize(init_image.size, Image.LANCZOS)

    scene_output_dir.mkdir(parents=True, exist_ok=True)
    redraw.save(output_path)
    if (scene_dir / "prompt.txt").exists():
        shutil.copy2(scene_dir / "prompt.txt", scene_output_dir / "prompt.txt")
    if (scene_dir / "metadata.json").exists():
        shutil.copy2(scene_dir / "metadata.json", scene_output_dir / "metadata.json")
    (scene_output_dir / "flux_prompt_used.txt").write_text(prompt, encoding="utf-8")
    (scene_output_dir / "flux_generation.json").write_text(
        json.dumps(
            {
                "scene_id": scene_id,
                "scale": scale,
                "strength": strength,
                "steps": steps,
                "seed": seed,
                "guidance_scale": 0.0,
                "init_size": [init_image.width, init_image.height],
                "elapsed_seconds": round(elapsed, 1),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return original, redraw


def copy_to_override(scene_dir: Path, redraw: Image.Image, scale: float, override_root: Path) -> None:
    scene_id = scene_id_from_dir(scene_dir)
    override_dir = override_root / f"scene_{scene_id:03d}"
    override_dir.mkdir(parents=True, exist_ok=True)
    scale_int = int(scale) if float(scale).is_integer() else scale
    redraw.save(override_dir / f"background@{scale_int}x.png")
    if (scene_dir / "prompt.txt").exists():
        shutil.copy2(scene_dir / "prompt.txt", override_dir / "prompt.txt")
    if (scene_dir / "metadata.json").exists():
        shutil.copy2(scene_dir / "metadata.json", override_dir / "metadata.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, default=Path("extracted/rosetattoo"))
    parser.add_argument("--output-dir", type=Path, default=Path("generated/flux-redraws"))
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--gguf-transformer", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--prompt-brief-dir", type=Path, default=Path("profiles/neural/prompt-briefs"))
    parser.add_argument("--scenes", type=int, nargs="*", default=None)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--strength", type=float, default=0.45,
                         help="img2img denoise strength; lower preserves more of the original geometry.")
    parser.add_argument("--steps", type=int, default=4, help="FLUX.1-schnell is trained for 1-4 steps.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--scummvm-overrides", type=Path, default=None)
    parser.add_argument("--contact-sheet", type=Path, default=Path("validation/contact-sheets/flux-redraws.jpg"))
    parser.add_argument("--no-contact-sheet", action="store_true")
    args = parser.parse_args()

    if not args.model_dir.exists():
        raise SystemExit(f"Model dir not found: {args.model_dir}. See docs/flux-setup.md.")
    if not args.gguf_transformer.exists():
        raise SystemExit(f"GGUF transformer not found: {args.gguf_transformer}. See docs/flux-setup.md.")

    dirs = scene_dirs(args.input_dir, args.scenes)
    if not dirs:
        raise SystemExit(f"No scene directories found under {args.input_dir}")

    pipe = load_pipeline(args.model_dir, args.gguf_transformer, args.device)

    pairs: list[tuple[int, Image.Image, Image.Image]] = []
    for scene_dir in dirs:
        result = process_scene(
            pipe,
            scene_dir,
            args.output_dir,
            args.scale,
            args.strength,
            args.steps,
            args.seed,
            args.prompt_brief_dir if args.prompt_brief_dir.exists() else None,
            args.skip_existing,
        )
        if result is None:
            continue
        original, redraw = result
        pairs.append((scene_id_from_dir(scene_dir), original, redraw))
        if args.scummvm_overrides:
            copy_to_override(scene_dir, redraw, args.scale, args.scummvm_overrides)

    if not args.no_contact_sheet:
        build_contact_sheet(pairs, args.contact_sheet)


if __name__ == "__main__":
    main()
