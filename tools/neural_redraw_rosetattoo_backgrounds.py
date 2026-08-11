#!/usr/bin/env python3
"""Redraw Rose Tattoo backgrounds with a local Stable Diffusion-compatible API."""

from __future__ import annotations

import argparse
import base64
import copy
import io
import json
import math
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class RedrawResult:
    scene_id: int
    scene_name: str
    input: str
    init_image: str
    edge_image: str
    prompt: str
    output: str
    scummvm_override: str | None
    scale: int
    seed: int
    denoising_strength: float
    steps: int
    cfg_scale: float
    checkpoint: str | None
    profile: str | None
    controlnet_weight: float
    controlnet_guidance_end: float
    controlnet_control_mode: str
    original_blend_strength: float
    init_upscaler: str
    attempt_count: int
    tile_count: int
    skipped: bool
    drift_warning: bool
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    expected_size: tuple[int, int]
    blank: bool
    rms_delta_from_init: float


def scene_dirs(root: Path, selected: list[int] | None) -> list[Path]:
    if selected:
        return [root / f"scene_{scene_id:02d}" for scene_id in selected]
    return sorted(p for p in root.glob("scene_*") if p.is_dir())


def load_metadata(scene_dir: Path) -> dict[str, Any]:
    metadata_path = scene_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def load_settings_file(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_setting_values(args: argparse.Namespace, values: dict[str, Any]) -> None:
    option_map = {
        "checkpoint": "checkpoint",
        "steps": "steps",
        "cfg_scale": "cfg_scale",
        "denoising_strength": "denoising_strength",
        "sampler": "sampler",
        "style_prompt": "style_prompt",
        "negative_prompt": "negative_prompt",
        "controlnet_model": "controlnet_model",
        "controlnet_module": "controlnet_module",
        "controlnet_weight": "controlnet_weight",
        "controlnet_processor_res": "controlnet_processor_res",
        "controlnet_threshold_a": "controlnet_threshold_a",
        "controlnet_threshold_b": "controlnet_threshold_b",
        "controlnet_guidance_end": "controlnet_guidance_end",
        "controlnet_control_mode": "controlnet_control_mode",
        "original_blend_strength": "original_blend_strength",
        "tile_width": "tile_width",
        "tile_overlap": "tile_overlap",
        "warn_rms_delta": "warn_rms_delta",
        "edge_source": "edge_source",
        "init_upscaler": "init_upscaler",
        "liberal_art": "liberal_art",
        "liberal_art_denoise": "liberal_art_denoise",
        "liberal_art_margin": "liberal_art_margin",
        "liberal_art_mask_blur": "liberal_art_mask_blur",
    }
    for key, value in values.items():
        if key in {"profile", "scenes", "scene_overrides", "defaults"}:
            continue
        if key not in option_map:
            raise ValueError(f"Unknown settings key: {key}")
        setattr(args, option_map[key], value)


def scene_settings_for(
    base_args: argparse.Namespace,
    settings: dict[str, Any],
    scene_id: int,
) -> argparse.Namespace:
    scene_args = copy.copy(base_args)
    scene_args.profile = settings.get("profile")
    apply_setting_values(scene_args, settings.get("defaults", {}))

    scene_overrides = settings.get("scene_overrides", {})
    override = scene_overrides.get(str(scene_id), scene_overrides.get(scene_id, {}))
    if override:
        scene_args.profile = override.get("profile", scene_args.profile)
        apply_setting_values(scene_args, override)
    return scene_args


def validate_generation_settings(args: argparse.Namespace) -> None:
    if args.scale < 1:
        raise ValueError("--scale must be >= 1")
    if args.tile_width < 256:
        raise ValueError("--tile-width must be >= 256")
    if args.tile_overlap < 0 or args.tile_overlap >= args.tile_width:
        raise ValueError("--tile-overlap must be between 0 and --tile-width")
    if args.steps < 1:
        raise ValueError("--steps must be >= 1")
    if not 0 <= args.denoising_strength <= 1:
        raise ValueError("--denoising-strength must be between 0 and 1")
    if not 0 <= args.controlnet_weight <= 2:
        raise ValueError("--controlnet-weight must be between 0 and 2")
    if not 0 <= args.controlnet_guidance_end <= 1:
        raise ValueError("--controlnet-guidance-end must be between 0 and 1")
    if not 0 <= args.original_blend_strength <= 1:
        raise ValueError("--original-blend-strength must be between 0 and 1")
    if args.retry_drift_attempts < 1:
        raise ValueError("--retry-drift-attempts must be >= 1")
    if not 0 <= args.liberal_art_denoise <= 1:
        raise ValueError("--liberal-art-denoise must be between 0 and 1")
    if args.liberal_art_margin < 0:
        raise ValueError("--liberal-art-margin must be >= 0")
    if args.liberal_art_mask_blur < 0:
        raise ValueError("--liberal-art-mask-blur must be >= 0")


def effective_settings_summary(scene_id: int, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "profile": args.profile,
        "checkpoint": args.checkpoint,
        "steps": args.steps,
        "cfg_scale": args.cfg_scale,
        "denoising_strength": args.denoising_strength,
        "sampler": args.sampler,
        "controlnet_model": args.controlnet_model,
        "controlnet_weight": args.controlnet_weight,
        "controlnet_guidance_end": args.controlnet_guidance_end,
        "controlnet_control_mode": args.controlnet_control_mode,
        "controlnet_threshold_a": args.controlnet_threshold_a,
        "controlnet_threshold_b": args.controlnet_threshold_b,
        "original_blend_strength": args.original_blend_strength,
        "tile_width": args.tile_width,
        "tile_overlap": args.tile_overlap,
        "warn_rms_delta": args.warn_rms_delta,
        "edge_source": args.edge_source,
        "init_upscaler": args.init_upscaler,
        "liberal_art": args.liberal_art,
        "liberal_art_denoise": args.liberal_art_denoise,
        "liberal_art_margin": args.liberal_art_margin,
        "liberal_art_mask_blur": args.liberal_art_mask_blur,
    }


def scene_dir_name(scene_id: int) -> str:
    return f"scene_{scene_id:03d}"


def find_scene_sidecar(root: Path, scene_id: int, name: str) -> Path | None:
    for dirname in (f"scene_{scene_id:03d}", f"scene_{scene_id:02d}", f"scene_{scene_id}"):
        path = root / dirname / name
        if path.exists():
            return path
    return None


def image_to_base64(img: Image.Image) -> str:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def image_file_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def base64_to_image(data: str) -> Image.Image:
    if "," in data and data.split(",", 1)[0].startswith("data:"):
        data = data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_api(base_url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            get_json(f"{base_url}/sdapi/v1/options", timeout=5)
            return
        except (urllib.error.URLError, TimeoutError) as err:
            last_error = err
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {base_url}: {last_error}")


def make_realism_prompt(scene_prompt: str, scene_name: str, style_prompt: str) -> str:
    parts = [
        (
            "Photorealistic faithful redraw of a Victorian London detective "
            f"adventure game background, scene: {scene_name}."
        ),
        (
            "Preserve the exact original camera angle, layout, architecture, "
            "doorways, props, silhouettes, lighting direction, signs, exits, "
            "walkable geometry, object positions, and puzzle-relevant details."
        ),
        (
            "Realistic materials, period-accurate grime, weathered brick, "
            "wood, glass, soot, gaslight, fog, worn fabric, cinematic natural "
            "lighting, high detail, sharp but not over-processed."
        ),
        (
            "Do not add new readable text, new people, new clues, new doors, "
            "new exits, modern objects, fantasy elements, or extra signage."
        ),
    ]
    if style_prompt.strip():
        parts.append(style_prompt.strip())
    parts.extend(
        [
            "",
            scene_prompt.strip(),
        ]
    )
    return "\n".join(parts)


def load_scene_prompt_text(
    scene_dir: Path,
    scene_id: int,
    prompt_brief_dir: Path | None,
) -> tuple[str, Path]:
    if prompt_brief_dir:
        brief_path = find_scene_sidecar(prompt_brief_dir, scene_id, "visual_brief.txt")
        if brief_path:
            return brief_path.read_text(encoding="utf-8"), brief_path
    prompt_path = scene_dir / "prompt.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)
    return prompt_path.read_text(encoding="utf-8"), prompt_path


def make_edge_image(
    scene_dir: Path,
    init_image: Image.Image,
    edge_source: str,
    requested_module: str,
    output_path: Path,
) -> str:
    """Build the ControlNet structural-guidance image for a scene.

    Returns the effective ControlNet preprocessor module name the caller
    should use with the resulting image.

    - ``canny``: hand the upscaled *unfiltered* init image straight to
      Automatic1111 so its own ``canny`` preprocessor module runs exactly
      once. Previously this function pre-filtered the image with PIL's
      ``FIND_EDGES`` *and* the API preprocessor still ran canny on top of
      that already-edge-filtered image, so edges effectively got detected
      twice - producing much denser/noisier structural constraints than
      intended and over-constraining fine painted texture/dithering rather
      than just architecture and geometry.
    - ``walk-zones`` / ``hotspots`` / ``combined``: use the game-semantic
      boundary rasters produced by extract_rosetattoo_assets.py (walkable
      floor rectangles and/or clickable-object bounds) instead of pixel-level
      edge detection on the painted background. These are already final
      boundary images, so the ControlNet preprocessor is bypassed by default
      (module "none") - the raster *is* the control signal. If the caller
      explicitly requested a non-default module (i.e. --controlnet-module
      was passed alongside a non-canny --edge-source), that explicit choice
      is respected instead.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if edge_source == "canny":
        init_image.convert("RGB").save(output_path)
        return requested_module

    mask_filename = {
        "walk-zones": "walk_zones_mask.png",
        "hotspots": "hotspots_mask.png",
        "combined": "structure_control.png",
    }[edge_source]
    mask_path = scene_dir / mask_filename
    if not mask_path.exists():
        print(
            f"  warning: {mask_path} not found (scene may predate walk-zone/hotspot "
            f"extraction); falling back to --edge-source canny for this scene"
        )
        init_image.convert("RGB").save(output_path)
        return requested_module

    with Image.open(mask_path) as mask:
        # Nearest-neighbor keeps the rectangle outlines crisp when upscaling
        # to the tile resolution, matching how boundaries stay sharp rather
        # than blurring into soft gradients like a photographic edge map.
        mask = mask.convert("RGB").resize(init_image.size, Image.Resampling.NEAREST)
        mask.save(output_path)
    return requested_module if requested_module != "canny" else "none"


def upscale_via_api(
    api_url: str,
    api_timeout: int,
    source: Image.Image,
    scale: int,
    upscaler: str,
) -> Image.Image:
    """Upscale via Automatic1111's /sdapi/v1/extra-single-image endpoint.

    A real super-resolution model (ESRGAN/SwinIR/DAT/etc.) reconstructs
    plausible high-frequency detail and, critically, treats the source
    image's palette-dithering noise and low-res JPEG-like blocking as
    something to clean up rather than something to preserve - unlike a
    naive Lanczos resize, which just smoothly interpolates the existing
    pixels (including their dithering pattern) to a bigger canvas. Since
    the diffusion pass afterwards runs at a moderate denoising strength to
    stay faithful to game geometry, it mostly polishes whatever the init
    image already looks like rather than removing baked-in artifacts - so
    starting from a cleaner, sharper init image matters far more than
    tweaking the diffusion pass alone.
    """
    payload = {
        "image": image_to_base64(source),
        "upscaling_resize": scale,
        "upscaler_1": upscaler,
    }
    response = post_json(f"{api_url}/sdapi/v1/extra-single-image", payload, api_timeout)
    image = response.get("image")
    if not image:
        raise RuntimeError(f"Upscale API returned no image (upscaler={upscaler!r})")
    return base64_to_image(image)


def make_init_image(
    args: argparse.Namespace,
    source: Image.Image,
    scale: int,
    output_path: Path,
) -> Image.Image:
    if args.init_upscaler.lower() not in ("lanczos", "nearest", "none"):
        try:
            init = upscale_via_api(args.api_url, args.api_timeout, source, scale, args.init_upscaler)
            if init.size != (source.width * scale, source.height * scale):
                init = init.resize(
                    (source.width * scale, source.height * scale),
                    Image.Resampling.LANCZOS,
                )
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            print(
                f"  warning: --init-upscaler {args.init_upscaler!r} failed ({exc}); "
                f"falling back to Lanczos for this scene"
            )
            init = source.resize(
                (source.width * scale, source.height * scale),
                Image.Resampling.LANCZOS,
            )
    else:
        init = source.resize(
            (source.width * scale, source.height * scale),
            Image.Resampling.LANCZOS,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    init.save(output_path)
    return init


def is_blank(img: Image.Image) -> bool:
    stat = ImageStat.Stat(img.convert("RGB"))
    return max(stat.stddev) < 0.5


def rms_delta(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))


def set_automatic1111_checkpoint(args: argparse.Namespace) -> None:
    if not args.checkpoint:
        return
    payload = {"sd_model_checkpoint": args.checkpoint}
    post_json(f"{args.api_url}/sdapi/v1/options", payload, args.api_timeout)


def automatic1111_img2img(
    args: argparse.Namespace,
    init_image: Image.Image,
    edge_path: Path,
    controlnet_module: str,
    prompt: str,
    seed: int,
) -> Image.Image:
    payload: dict[str, Any] = {
        "init_images": [image_to_base64(init_image)],
        "prompt": prompt,
        "negative_prompt": args.negative_prompt,
        "seed": seed,
        "sampler_name": args.sampler,
        "steps": args.steps,
        "cfg_scale": args.cfg_scale,
        "denoising_strength": args.denoising_strength,
        "width": init_image.width,
        "height": init_image.height,
        "resize_mode": 0,
        "batch_size": 1,
        "n_iter": 1,
        "restore_faces": False,
        "tiling": False,
        "do_not_save_samples": True,
        "do_not_save_grid": True,
    }
    if args.controlnet_model:
        payload["alwayson_scripts"] = {
            "ControlNet": {
                "args": [
                    {
                        "enabled": True,
                        "image": image_file_to_base64(edge_path),
                        "module": controlnet_module,
                        "model": args.controlnet_model,
                        "weight": args.controlnet_weight,
                        "resize_mode": "Just Resize",
                        "low_vram": False,
                        "processor_res": args.controlnet_processor_res,
                        "threshold_a": args.controlnet_threshold_a,
                        "threshold_b": args.controlnet_threshold_b,
                        "guidance_start": 0.0,
                        "guidance_end": args.controlnet_guidance_end,
                        "control_mode": args.controlnet_control_mode,
                        "pixel_perfect": True,
                    }
                ]
            }
        }

    response = post_json(f"{args.api_url}/sdapi/v1/img2img", payload, args.api_timeout)
    images = response.get("images") or []
    if not images:
        raise RuntimeError("Stable Diffusion API returned no images")
    return base64_to_image(images[0])


def automatic1111_inpaint(
    args: argparse.Namespace,
    base_image: Image.Image,
    mask_image: Image.Image,
    prompt: str,
    denoising_strength: float,
    mask_blur: int,
    seed: int,
) -> Image.Image:
    """Run a masked img2img pass that only regenerates pixels under the mask.

    Used for the --liberal-art pass: `mask_image` is white where the model is
    free to invent new decorative detail and black where the base image must
    stay byte-identical (Automatic1111's native inpainting semantics), so this
    is safe to run at a higher denoising strength than the geometry-preserving
    base redraw without risking walk-zone/hotspot drift.
    """
    payload: dict[str, Any] = {
        "init_images": [image_to_base64(base_image)],
        "mask": image_to_base64(mask_image),
        "mask_blur": mask_blur,
        "inpainting_fill": 1,
        "inpaint_full_res": False,
        "inpainting_mask_invert": 0,
        "prompt": prompt,
        "negative_prompt": args.negative_prompt,
        "seed": seed,
        "sampler_name": args.sampler,
        "steps": args.steps,
        "cfg_scale": args.cfg_scale,
        "denoising_strength": denoising_strength,
        "width": base_image.width,
        "height": base_image.height,
        "resize_mode": 0,
        "batch_size": 1,
        "n_iter": 1,
        "restore_faces": False,
        "tiling": False,
        "do_not_save_samples": True,
        "do_not_save_grid": True,
    }
    response = post_json(f"{args.api_url}/sdapi/v1/img2img", payload, args.api_timeout)
    images = response.get("images") or []
    if not images:
        raise RuntimeError("Stable Diffusion API returned no images for liberal-art pass")
    return base64_to_image(images[0])


def build_liberal_art_mask(
    scene_dir: Path,
    size: tuple[int, int],
    margin_px: int,
) -> Image.Image | None:
    """Build the inverted, dilated liberal-art mask for a scene, if available.

    Returns a grayscale image the same size as the redraw output: white =
    free for decorative invention, black = protected walk-zone/hotspot
    geometry that must not change. Returns None if the scene has no
    protect_mask.png sidecar (extract_rosetattoo_assets.py wasn't re-run, or
    the room had no parsed walk zones/hotspots), so callers can skip the
    pass entirely rather than treating an all-black/all-white mask as
    meaningful.
    """
    mask_path = scene_dir / "protect_mask.png"
    if not mask_path.exists():
        return None
    with Image.open(mask_path) as protect_mask:
        protect_mask = protect_mask.convert("L")
        # Nearest-neighbor keeps the resize edges crisp; mask_blur (applied
        # later by the API) is what actually softens the boundary.
        protect_mask = protect_mask.resize(size, Image.Resampling.NEAREST)
        if margin_px > 0:
            scale_x = size[0] / max(protect_mask.width, 1)
            dilation = max(1, round(margin_px * scale_x))
            # MaxFilter requires an odd kernel size.
            kernel = dilation * 2 + 1
            protect_mask = protect_mask.filter(ImageFilter.MaxFilter(kernel))
        return ImageOps.invert(protect_mask)


def apply_liberal_art_pass(
    scene_args: argparse.Namespace,
    scene_dir: Path,
    output_image: Image.Image,
    prompt: str,
    seed: int,
) -> Image.Image:
    """Run the optional liberal-art masked decorative pass on a finished redraw.

    No-ops (returns the input unchanged) when --no-liberal-art was passed or
    the scene has no protect_mask.png sidecar, so this is always safe to call
    unconditionally from process_scene().
    """
    if not scene_args.liberal_art:
        return output_image
    liberal_mask = build_liberal_art_mask(
        scene_dir, output_image.size, scene_args.liberal_art_margin
    )
    if liberal_mask is None:
        return output_image
    return automatic1111_inpaint(
        scene_args,
        output_image,
        liberal_mask,
        prompt,
        scene_args.liberal_art_denoise,
        scene_args.liberal_art_mask_blur,
        seed,
    )


def tile_positions(length: int, tile_length: int, overlap: int) -> list[int]:
    if length <= tile_length:
        return [0]
    stride = max(1, tile_length - overlap)
    positions = list(range(0, max(1, length - tile_length + 1), stride))
    final_position = length - tile_length
    if positions[-1] != final_position:
        positions.append(final_position)
    return positions


def horizontal_alpha(width: int) -> Image.Image:
    if width <= 1:
        return Image.new("L", (width, 1), 255)
    return Image.linear_gradient("L").resize((width, 1))


def paste_horizontal_tile(
    canvas: Image.Image,
    tile: Image.Image,
    x: int,
    covered_right: int,
) -> int:
    if x >= covered_right:
        canvas.paste(tile, (x, 0))
        return max(covered_right, x + tile.width)

    overlap_width = min(covered_right - x, tile.width)
    if overlap_width > 0:
        existing = canvas.crop((x, 0, x + overlap_width, tile.height))
        incoming = tile.crop((0, 0, overlap_width, tile.height))
        alpha = horizontal_alpha(overlap_width).resize((overlap_width, tile.height))
        canvas.paste(Image.composite(incoming, existing, alpha), (x, 0))

    if overlap_width < tile.width:
        canvas.paste(
            tile.crop((overlap_width, 0, tile.width, tile.height)),
            (x + overlap_width, 0),
        )
    return max(covered_right, x + tile.width)


def redraw_image(
    args: argparse.Namespace,
    init_image: Image.Image,
    edge_path: Path,
    controlnet_module: str,
    prompt: str,
    seed: int,
    scene_output_dir: Path,
) -> tuple[Image.Image, int]:
    if init_image.width <= args.tile_width:
        return (
            automatic1111_img2img(args, init_image, edge_path, controlnet_module, prompt, seed),
            1,
        )

    positions = tile_positions(init_image.width, args.tile_width, args.tile_overlap)
    canvas = Image.new("RGB", init_image.size)
    covered_right = 0
    with Image.open(edge_path) as edge_source:
        edge_image = edge_source.convert("RGB")
        for idx, x in enumerate(positions):
            tile_box = (
                x,
                0,
                min(x + args.tile_width, init_image.width),
                init_image.height,
            )
            init_tile = init_image.crop(tile_box)
            edge_tile = edge_image.crop(tile_box)
            tile_edge_path = (
                scene_output_dir / "control" / f"edges_tile_{idx:02d}@{args.scale}x.png"
            )
            tile_output_path = (
                scene_output_dir / "tiles" / f"tile_{idx:02d}@{args.scale}x.png"
            )
            tile_edge_path.parent.mkdir(parents=True, exist_ok=True)
            tile_output_path.parent.mkdir(parents=True, exist_ok=True)
            edge_tile.save(tile_edge_path)
            output_tile = automatic1111_img2img(
                args,
                init_tile,
                tile_edge_path,
                controlnet_module,
                prompt,
                seed + idx if seed >= 0 else -1,
            )
            if output_tile.size != init_tile.size:
                output_tile = output_tile.resize(init_tile.size, Image.Resampling.LANCZOS)
            output_tile.save(tile_output_path)
            covered_right = paste_horizontal_tile(canvas, output_tile, x, covered_right)
    return canvas, len(positions)


def blend_with_original(output_image: Image.Image, init_image: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return output_image
    if output_image.size != init_image.size:
        init_image = init_image.resize(output_image.size, Image.Resampling.LANCZOS)
    return Image.blend(output_image.convert("RGB"), init_image.convert("RGB"), strength)


def generate_candidate(
    scene_args: argparse.Namespace,
    init_image: Image.Image,
    edge_path: Path,
    controlnet_module: str,
    prompt: str,
    seed: int,
    scene_output_dir: Path,
    expected_size: tuple[int, int],
) -> tuple[Image.Image, int, float, bool]:
    output_image, tile_count = redraw_image(
        scene_args,
        init_image,
        edge_path,
        controlnet_module,
        prompt,
        seed,
        scene_output_dir,
    )
    if output_image.size != expected_size:
        output_image = output_image.resize(expected_size, Image.Resampling.LANCZOS)
    output_image = blend_with_original(
        output_image,
        init_image,
        scene_args.original_blend_strength,
    )
    blank = is_blank(output_image)
    delta = rms_delta(output_image, init_image)
    return output_image, tile_count, delta, blank


def copy_sidecars(scene_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("prompt.txt", "metadata.json"):
        source = scene_dir / name
        if source.exists():
            shutil.copy2(source, destination / name)


def process_scene(scene_dir: Path, args: argparse.Namespace, scene_args: argparse.Namespace) -> RedrawResult:
    metadata = load_metadata(scene_dir)
    scene_id = int(scene_dir.name.split("_", 1)[1])
    scene_name = metadata.get("scene_name", f"Scene {scene_id:02d}")
    input_path = scene_dir / "background.png"
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    scene_output_dir = args.output_dir / scene_dir_name(scene_id)
    init_path = scene_output_dir / "control" / f"init@{args.scale}x.png"
    edge_path = scene_output_dir / "control" / f"edges@{args.scale}x.png"
    output_path = scene_output_dir / f"background@{args.scale}x.png"
    prompt_output_path = scene_output_dir / "neural_prompt.txt"
    result_path = scene_output_dir / "result.json"
    seed = args.seed + scene_id if args.seed >= 0 else -1

    with Image.open(input_path).convert("RGB") as source:
        init_image = make_init_image(scene_args, source, args.scale, init_path)
        controlnet_module = make_edge_image(
            scene_dir, init_image, scene_args.edge_source, scene_args.controlnet_module, edge_path
        )
        expected_size = init_image.size
        input_size = source.size

    scene_prompt_text, scene_prompt_source = load_scene_prompt_text(
        scene_dir,
        scene_id,
        args.prompt_brief_dir,
    )
    prompt = make_realism_prompt(scene_prompt_text, scene_name, scene_args.style_prompt)
    scene_output_dir.mkdir(parents=True, exist_ok=True)
    prompt_output_path.write_text(prompt + "\n", encoding="utf-8")
    if args.prompt_brief_dir and args.prompt_brief_dir.exists():
        brief_copy = scene_output_dir / "visual_brief.txt"
        brief_copy.write_text(scene_prompt_text.strip() + "\n", encoding="utf-8")
        (scene_output_dir / "visual_brief_source.txt").write_text(
            str(scene_prompt_source) + "\n",
            encoding="utf-8",
        )
    copy_sidecars(scene_dir, scene_output_dir)

    if args.skip_existing and output_path.exists():
        with Image.open(output_path).convert("RGB") as output_image:
            output_size = output_image.size
            blank = is_blank(output_image)
            delta = rms_delta(output_image, init_image)
        if output_size != expected_size:
            raise ValueError(f"{output_path} is {output_size}, expected {expected_size}")
        if blank:
            raise ValueError(f"{output_path} appears blank")
        if args.retry_existing_drift and delta > scene_args.warn_rms_delta:
            print(
                f"scene {scene_id:03d}: regenerating existing drift "
                f"delta={round(delta, 3)} threshold={scene_args.warn_rms_delta}"
            )
        else:
            override_path = copy_to_override(args, scene_dir, scene_id, output_path, prompt_output_path)
            result = RedrawResult(
                scene_id=scene_id,
                scene_name=scene_name,
                input=str(input_path),
                init_image=str(init_path),
                edge_image=str(edge_path),
                prompt=str(prompt_output_path),
                output=str(output_path),
                scummvm_override=str(override_path) if override_path else None,
                scale=args.scale,
                seed=seed,
                denoising_strength=scene_args.denoising_strength,
                steps=scene_args.steps,
                cfg_scale=scene_args.cfg_scale,
                checkpoint=scene_args.checkpoint,
                profile=scene_args.profile,
                controlnet_weight=scene_args.controlnet_weight,
                controlnet_guidance_end=scene_args.controlnet_guidance_end,
                controlnet_control_mode=scene_args.controlnet_control_mode,
                original_blend_strength=scene_args.original_blend_strength,
                init_upscaler=scene_args.init_upscaler,
                attempt_count=0,
                tile_count=len(tile_positions(expected_size[0], scene_args.tile_width, scene_args.tile_overlap)),
                skipped=True,
                drift_warning=delta > scene_args.warn_rms_delta,
                input_size=input_size,
                output_size=output_size,
                expected_size=expected_size,
                blank=blank,
                rms_delta_from_init=round(delta, 3),
            )
            result_path.write_text(
                json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return result

    if args.backend != "automatic1111":
        raise ValueError(f"Unsupported backend: {args.backend}")

    best: tuple[Image.Image, int, float, bool, int, int] | None = None
    attempt_dir = scene_output_dir / "attempts"
    for attempt_index in range(args.retry_drift_attempts):
        attempt_seed = seed + (attempt_index * args.retry_seed_step) if seed >= 0 else -1
        output_image, tile_count, delta, blank = generate_candidate(
            scene_args,
            init_image,
            edge_path,
            controlnet_module,
            prompt,
            attempt_seed,
            scene_output_dir,
            expected_size,
        )
        if best is None or (not blank, -delta) > (not best[3], -best[2]):
            best = (output_image, tile_count, delta, blank, attempt_seed, attempt_index + 1)
        if not blank and delta <= scene_args.warn_rms_delta:
            break
        attempt_dir.mkdir(parents=True, exist_ok=True)
        output_image.save(attempt_dir / f"attempt_{attempt_index + 1:02d}_seed_{attempt_seed}.png")

    assert best is not None
    output_image, tile_count, delta, blank, seed, attempt_count = best

    if blank:
        raise ValueError(f"{output_path} appears blank")

    faithful_image = output_image
    output_image = apply_liberal_art_pass(scene_args, scene_dir, faithful_image, prompt, seed)
    if output_image is not faithful_image:
        if output_image.size != expected_size:
            output_image = output_image.resize(expected_size, Image.Resampling.LANCZOS)
        if is_blank(output_image):
            print(
                f"scene {scene_id:03d}: liberal-art pass produced a blank image, "
                "keeping the faithful base redraw instead"
            )
            output_image = faithful_image
        else:
            faithful_path = scene_output_dir / f"background_faithful@{args.scale}x.png"
            faithful_image.save(faithful_path)

    output_image.save(output_path)

    override_path = copy_to_override(args, scene_dir, scene_id, output_path, prompt_output_path)
    result = RedrawResult(
        scene_id=scene_id,
        scene_name=scene_name,
        input=str(input_path),
        init_image=str(init_path),
        edge_image=str(edge_path),
        prompt=str(prompt_output_path),
        output=str(output_path),
        scummvm_override=str(override_path) if override_path else None,
        scale=args.scale,
        seed=seed,
        denoising_strength=scene_args.denoising_strength,
        steps=scene_args.steps,
        cfg_scale=scene_args.cfg_scale,
        checkpoint=scene_args.checkpoint,
        profile=scene_args.profile,
        controlnet_weight=scene_args.controlnet_weight,
        controlnet_guidance_end=scene_args.controlnet_guidance_end,
        controlnet_control_mode=scene_args.controlnet_control_mode,
        original_blend_strength=scene_args.original_blend_strength,
        init_upscaler=scene_args.init_upscaler,
        attempt_count=attempt_count,
        tile_count=tile_count,
        skipped=False,
        drift_warning=delta > scene_args.warn_rms_delta,
        input_size=input_size,
        output_size=output_image.size,
        expected_size=expected_size,
        blank=blank,
        rms_delta_from_init=round(delta, 3),
    )
    result_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def copy_to_override(
    args: argparse.Namespace,
    scene_dir: Path,
    scene_id: int,
    output_path: Path,
    prompt_output_path: Path,
) -> Path | None:
    if not args.scummvm_overrides:
        return None
    override_dir = args.scummvm_overrides / f"scene_{scene_id:03d}"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_path = override_dir / f"background@{args.scale}x.png"
    shutil.copy2(output_path, override_path)
    copy_sidecars(scene_dir, override_dir)
    shutil.copy2(prompt_output_path, override_dir / "neural_prompt.txt")
    visual_brief_path = prompt_output_path.parent / "visual_brief.txt"
    if visual_brief_path.exists():
        shutil.copy2(visual_brief_path, override_dir / "visual_brief.txt")
    return override_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Redraw extracted Rose Tattoo backgrounds through a local diffusion API."
    )
    parser.add_argument("--backend", choices=["automatic1111"], default="automatic1111")
    parser.add_argument("--api-url", default="http://127.0.0.1:7860")
    parser.add_argument("--api-timeout", type=int, default=900)
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for the API before starting.",
    )
    parser.add_argument("--wait-timeout", type=int, default=180)
    parser.add_argument(
        "--checkpoint",
        help="Optional checkpoint name to select through the API.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "extracted" / "rosetattoo",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "generated" / "neural-redraws",
    )
    parser.add_argument("--scummvm-overrides", type=Path)
    parser.add_argument(
        "--settings-file",
        type=Path,
        help=(
            "JSON file with defaults and per-scene overrides for production "
            "photographic redraw passes."
        ),
    )
    parser.add_argument(
        "--prompt-brief-dir",
        type=Path,
        default=ROOT / "profiles" / "neural" / "prompt-briefs",
        help=(
            "Directory of scene_XXX/visual_brief.txt prompt briefs, generated "
            "for every scene by polish_rosetattoo_prompts.py (LLM-paraphrased, "
            "committed to git under profiles/ since they contain no verbatim "
            "game text). Defaults to the repo's checked-in briefs so every "
            "scene gets a consistent LLM-authored prompt with no per-scene "
            "manual overrides. Falls back to the scene's raw prompt.txt only "
            "if a brief is genuinely missing for that scene. Pass an empty "
            "string or a nonexistent path to force raw prompt.txt for all "
            "scenes instead."
        ),
    )
    parser.add_argument(
        "--print-effective-settings",
        action="store_true",
        help="Print resolved per-scene settings and exit without calling the API.",
    )
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument(
        "--scale",
        type=int,
        default=2,
        help=(
            "Output resolution multiplier applied to the native 640x480 (or similar) "
            "background. Default 2x. With --init-upscaler using a real AI upscaler "
            "(the new default), --scale 4 was verified to resolve genuinely more "
            "high-frequency detail than 2x - not just interpolated pixels - e.g. an "
            "interior candelabra silhouette became visible through a window at 4x "
            "that was lost at 2x, and stone/wood texture stayed sharper. Higher scale "
            "costs more generation time (wide rooms need more overlapping tiles) and "
            "disk space, so it isn't the new default for full-batch runs, but is "
            "recommended for hero scenes or a final high-quality pass."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Reuse existing scene outputs and refresh override copies.",
    )
    parser.add_argument(
        "--tile-width",
        type=int,
        default=1536,
        help="Maximum generated tile width after scaling; wider scenes are stitched.",
    )
    parser.add_argument("--tile-overlap", type=int, default=160)
    parser.add_argument(
        "--warn-rms-delta",
        type=float,
        default=18.0,
        help="Flag scene results whose RGB RMS drift from the init image is higher.",
    )
    parser.add_argument("--seed", type=int, default=36000)
    parser.add_argument(
        "--style-prompt",
        default=(
            "Render as a real location photograph, not as concept art: natural "
            "lens perspective, physically plausible surfaces, uneven handmade "
            "period construction, dust, smoke, dampness, patina, subtle film grain, "
            "imperfect exposure, grounded shadows, no plastic materials, no CGI look."
        ),
        help="Additional style guidance appended before extracted scene details.",
    )
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--cfg-scale", type=float, default=5.5)
    parser.add_argument("--sampler", default="DPM++ 2M SDE")
    parser.add_argument("--denoising-strength", type=float, default=0.38)
    parser.add_argument(
        "--init-upscaler",
        default="R-ESRGAN 4x+",
        help=(
            "Upscaler used to build the diffusion init image from the "
            "extracted background. A naive Lanczos resize just interpolates "
            "the source's existing pixels to a bigger canvas, which bakes in "
            "the original 256-color palette's dithering pattern and low-res "
            "softness - and since the diffusion pass runs at a moderate "
            "denoising strength to stay faithful to game geometry, it mostly "
            "polishes whatever the init image already looks like rather than "
            "removing that baked-in noise. A real super-resolution model "
            "(the default 'R-ESRGAN 4x+', or 'SwinIR 4x'/'DAT x4'/etc. - see "
            "Automatic1111's /sdapi/v1/upscalers) reconstructs plausible "
            "high-frequency detail and cleans up dithering/JPEG-like "
            "blocking instead of preserving it, giving the diffusion pass a "
            "much sharper, cleaner starting point. Pass 'lanczos' to restore "
            "the old offline-only behavior (useful for --skip-existing runs "
            "without a live API, or A/B comparisons)."
        ),
    )
    parser.add_argument(
        "--negative-prompt",
        default=(
            "cartoon, anime, illustration, fantasy, futuristic, modern cars, "
            "electric lights, extra people, readable new text, new signs, new "
            "doors, wrong architecture, warped perspective, distorted geometry, "
            "3d render, CGI, game engine render, plastic, dollhouse, miniature, "
            "low quality, blurry, oversharpened, watermark, signature"
        ),
    )
    parser.add_argument("--controlnet-model")
    parser.add_argument("--controlnet-module", default="canny")
    parser.add_argument(
        "--edge-source",
        choices=["canny", "walk-zones", "hotspots", "combined"],
        default="canny",
        help=(
            "Structural guidance source for ControlNet. 'canny' upscales the "
            "background and lets Automatic1111's own preprocessor detect "
            "edges from the painted pixels (can over-constrain fine texture "
            "and dithering). 'walk-zones'/'hotspots'/'combined' instead use "
            "the room's semantic walkable-floor rectangles and/or clickable-"
            "object bounds produced by extract_rosetattoo_assets.py "
            "(walk_zones_mask.png / hotspots_mask.png / structure_control.png), "
            "which preserves navigable geometry and interactive silhouettes "
            "without dictating brush-stroke detail. Non-canny sources bypass "
            "the server-side preprocessor (ControlNet module 'none') since "
            "the mask image is already the final boundary reference."
        ),
    )
    parser.add_argument("--controlnet-weight", type=float, default=0.75)
    parser.add_argument("--controlnet-processor-res", type=int, default=1024)
    parser.add_argument("--controlnet-threshold-a", type=float, default=100)
    parser.add_argument("--controlnet-threshold-b", type=float, default=200)
    parser.add_argument("--controlnet-guidance-end", type=float, default=0.75)
    parser.add_argument("--controlnet-control-mode", default="Balanced")
    parser.add_argument("--original-blend-strength", type=float, default=0.0)
    parser.add_argument(
        "--retry-drift-attempts",
        type=int,
        default=1,
        help="Number of alternate seeds to try when a generated image exceeds its drift threshold.",
    )
    parser.add_argument("--retry-seed-step", type=int, default=1000)
    parser.add_argument(
        "--retry-existing-drift",
        action="store_true",
        help="Regenerate existing --skip-existing outputs that exceed their drift threshold.",
    )
    parser.add_argument(
        "--liberal-art",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After the faithful geometry-preserving redraw pass, run a second "
            "masked img2img pass that is free to invent extra decorative "
            "detail (broken bricks, reliefs, weathering, clutter, etc.) "
            "anywhere NOT covered by the scene's protect_mask.png sidecar "
            "(union of walk zones + hotspot bounds, produced by "
            "extract_rosetattoo_assets.py). Protected pixels are left "
            "byte-identical via Automatic1111's native inpainting mask, so "
            "pathfinding/hotspot geometry can never drift even though this "
            "pass runs at a higher denoising strength than the base pass. "
            "Scenes missing a protect_mask.png sidecar are left untouched "
            "for this pass. Enabled by default; pass --no-liberal-art to "
            "restore the old single-pass behavior."
        ),
    )
    parser.add_argument(
        "--liberal-art-denoise",
        type=float,
        default=0.4,
        help=(
            "Denoising strength for the liberal-art masked pass. Higher than "
            "the base --denoising-strength since this pass is deliberately "
            "allowed to invent new detail, but still moderate since it "
            "starts from the already-faithful base redraw as strong visual "
            "context rather than from scratch. Calibrated against scene 18 "
            "(Cleopatra's Needle): 0.55 introduced an out-of-place wrought-"
            "iron gate and a large background structure; 0.35-0.4 stayed to "
            "subtle, in-period embellishment (weathered brick, texture, "
            "grime) without inventing new landmarks."
        ),
    )
    parser.add_argument(
        "--liberal-art-margin",
        type=int,
        default=12,
        help=(
            "Native-resolution pixel margin used to dilate (grow) the "
            "protected walk-zone/hotspot regions before inverting them into "
            "the liberal-art mask, so freeform generation can't creep up to "
            "the exact edge of geometry that must stay pixel-faithful."
        ),
    )
    parser.add_argument(
        "--liberal-art-mask-blur",
        type=int,
        default=24,
        help=(
            "Output-resolution mask_blur (soft edge feather) passed to "
            "Automatic1111 for the liberal-art pass, so the boundary between "
            "protected and freeform regions blends rather than showing a "
            "hard seam."
        ),
    )
    args = parser.parse_args()

    validate_generation_settings(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = load_settings_file(args.settings_file)
    if args.print_effective_settings:
        summaries = []
        for scene_dir in scene_dirs(args.input_dir, args.scenes):
            scene_id = int(scene_dir.name.split("_", 1)[1])
            scene_args = scene_settings_for(args, settings, scene_id)
            validate_generation_settings(scene_args)
            summaries.append(effective_settings_summary(scene_id, scene_args))
        print(json.dumps(summaries, indent=2, ensure_ascii=False))
        return

    if args.wait:
        wait_for_api(args.api_url, args.wait_timeout)

    results: list[RedrawResult] = []
    current_checkpoint: str | None = None
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        scene_id = int(scene_dir.name.split("_", 1)[1])
        scene_args = scene_settings_for(args, settings, scene_id)
        validate_generation_settings(scene_args)
        if scene_args.checkpoint != current_checkpoint:
            set_automatic1111_checkpoint(scene_args)
            current_checkpoint = scene_args.checkpoint
        result = process_scene(scene_dir, args, scene_args)
        results.append(result)
        print(
            f"scene {result.scene_id:03d}: {result.scene_name} "
            f"{result.input_size[0]}x{result.input_size[1]} -> "
            f"{result.output_size[0]}x{result.output_size[1]} "
            f"delta={result.rms_delta_from_init} tiles={result.tile_count} "
            f"{'DRIFT ' if result.drift_warning else ''}output={result.output}"
        )

    manifest = {
        "backend": args.backend,
        "api_url": args.api_url,
        "checkpoint": args.checkpoint,
        "settings_file": str(args.settings_file) if args.settings_file else None,
        "profile": settings.get("profile"),
        "input_dir": str(args.input_dir),
        "output_dir": str(args.output_dir),
        "scummvm_overrides": (
            str(args.scummvm_overrides) if args.scummvm_overrides else None
        ),
        "scale": args.scale,
        "scene_count": len(results),
        "results": [asdict(result) for result in results],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
