#!/usr/bin/env python3
"""Redraw Rose Tattoo backgrounds with a local Stable Diffusion-compatible API."""

from __future__ import annotations

import argparse
import base64
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


def make_realism_prompt(scene_prompt: str, scene_name: str) -> str:
    return "\n".join(
        [
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
            "",
            scene_prompt.strip(),
        ]
    )


def make_edge_image(source: Image.Image, output_path: Path) -> None:
    gray = ImageOps.grayscale(source)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = ImageOps.autocontrast(edges)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    edges.convert("RGB").save(output_path)


def make_init_image(source: Image.Image, scale: int, output_path: Path) -> Image.Image:
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
                        "module": args.controlnet_module,
                        "model": args.controlnet_model,
                        "weight": args.controlnet_weight,
                        "resize_mode": "Just Resize",
                        "low_vram": False,
                        "processor_res": args.controlnet_processor_res,
                        "threshold_a": args.controlnet_threshold_a,
                        "threshold_b": args.controlnet_threshold_b,
                        "guidance_start": 0.0,
                        "guidance_end": args.controlnet_guidance_end,
                        "control_mode": "Balanced",
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
    prompt: str,
    seed: int,
    scene_output_dir: Path,
) -> tuple[Image.Image, int]:
    if init_image.width <= args.tile_width:
        return automatic1111_img2img(args, init_image, edge_path, prompt, seed), 1

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
                prompt,
                seed + idx if seed >= 0 else -1,
            )
            if output_tile.size != init_tile.size:
                output_tile = output_tile.resize(init_tile.size, Image.Resampling.LANCZOS)
            output_tile.save(tile_output_path)
            covered_right = paste_horizontal_tile(canvas, output_tile, x, covered_right)
    return canvas, len(positions)


def copy_sidecars(scene_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("prompt.txt", "metadata.json"):
        source = scene_dir / name
        if source.exists():
            shutil.copy2(source, destination / name)


def process_scene(scene_dir: Path, args: argparse.Namespace) -> RedrawResult:
    metadata = load_metadata(scene_dir)
    scene_id = int(scene_dir.name.split("_", 1)[1])
    scene_name = metadata.get("scene_name", f"Scene {scene_id:02d}")
    input_path = scene_dir / "background.png"
    prompt_path = scene_dir / "prompt.txt"
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not prompt_path.exists():
        raise FileNotFoundError(prompt_path)

    scene_output_dir = args.output_dir / f"scene_{scene_id:03d}"
    init_path = scene_output_dir / "control" / f"init@{args.scale}x.png"
    edge_path = scene_output_dir / "control" / f"edges@{args.scale}x.png"
    output_path = scene_output_dir / f"background@{args.scale}x.png"
    prompt_output_path = scene_output_dir / "neural_prompt.txt"
    result_path = scene_output_dir / "result.json"
    seed = args.seed + scene_id if args.seed >= 0 else -1

    with Image.open(input_path).convert("RGB") as source:
        init_image = make_init_image(source, args.scale, init_path)
        make_edge_image(init_image, edge_path)
        expected_size = init_image.size
        input_size = source.size

    prompt = make_realism_prompt(
        prompt_path.read_text(encoding="utf-8"),
        scene_name,
    )
    scene_output_dir.mkdir(parents=True, exist_ok=True)
    prompt_output_path.write_text(prompt + "\n", encoding="utf-8")
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
        override_path = copy_to_override(args, scene_dir, scene_id, output_path, prompt_output_path)
        return RedrawResult(
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
            denoising_strength=args.denoising_strength,
            steps=args.steps,
            cfg_scale=args.cfg_scale,
            tile_count=len(tile_positions(expected_size[0], args.tile_width, args.tile_overlap)),
            skipped=True,
            drift_warning=delta > args.warn_rms_delta,
            input_size=input_size,
            output_size=output_size,
            expected_size=expected_size,
            blank=blank,
            rms_delta_from_init=round(delta, 3),
        )

    if args.backend != "automatic1111":
        raise ValueError(f"Unsupported backend: {args.backend}")
    output_image, tile_count = redraw_image(
        args,
        init_image,
        edge_path,
        prompt,
        seed,
        scene_output_dir,
    )
    if output_image.size != expected_size:
        output_image = output_image.resize(expected_size, Image.Resampling.LANCZOS)
    output_image.save(output_path)

    blank = is_blank(output_image)
    if blank:
        raise ValueError(f"{output_path} appears blank")
    delta = rms_delta(output_image, init_image)

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
        denoising_strength=args.denoising_strength,
        steps=args.steps,
        cfg_scale=args.cfg_scale,
        tile_count=tile_count,
        skipped=False,
        drift_warning=delta > args.warn_rms_delta,
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
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=int, default=2)
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
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--cfg-scale", type=float, default=5.5)
    parser.add_argument("--sampler", default="DPM++ 2M SDE")
    parser.add_argument("--denoising-strength", type=float, default=0.38)
    parser.add_argument(
        "--negative-prompt",
        default=(
            "cartoon, anime, illustration, fantasy, futuristic, modern cars, "
            "electric lights, extra people, readable new text, new signs, new "
            "doors, wrong architecture, warped perspective, distorted geometry, "
            "low quality, blurry, oversharpened, watermark, signature"
        ),
    )
    parser.add_argument("--controlnet-model")
    parser.add_argument("--controlnet-module", default="canny")
    parser.add_argument("--controlnet-weight", type=float, default=0.75)
    parser.add_argument("--controlnet-processor-res", type=int, default=1024)
    parser.add_argument("--controlnet-threshold-a", type=float, default=100)
    parser.add_argument("--controlnet-threshold-b", type=float, default=200)
    parser.add_argument("--controlnet-guidance-end", type=float, default=0.75)
    args = parser.parse_args()

    if args.scale < 1:
        raise ValueError("--scale must be >= 1")
    if args.tile_width < 256:
        raise ValueError("--tile-width must be >= 256")
    if args.tile_overlap < 0 or args.tile_overlap >= args.tile_width:
        raise ValueError("--tile-overlap must be between 0 and --tile-width")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.wait:
        wait_for_api(args.api_url, args.wait_timeout)
    set_automatic1111_checkpoint(args)

    results: list[RedrawResult] = []
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        result = process_scene(scene_dir, args)
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
