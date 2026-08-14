#!/usr/bin/env python3
"""CUDA-native Z-Image Union-ControlNet scene renderer.

This runner uses the official VideoX-Fun Python library rather than a web UI.
It makes the source scene a Canny control image and preserves every extracted
walk-zone/hotspot region through Z-Image's inpainting mask.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PIL import Image, ImageFilter

from common import ROOT, freedom_mask, prompt_for, save_scene, scaled_source, scene_dirs


DEFAULT_MODEL = "Tongyi-MAI/Z-Image-Turbo"
DEFAULT_CONTROLNET = "Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors"


def add_videox_to_path(videox_root: Path) -> None:
    resolved = str(videox_root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def load_pipeline(videox_root: Path, model: str, controlnet: Path):
    import torch
    from diffusers import FlowMatchEulerDiscreteScheduler
    from omegaconf import OmegaConf

    add_videox_to_path(videox_root)
    from videox_fun.models import AutoencoderKL, AutoTokenizer, Qwen3ForCausalLM, ZImageControlTransformer2DModel
    from videox_fun.pipeline import ZImageControlPipeline

    config_name = "z_image_control_2.1_lite.yaml" if "-lite-" in controlnet.name else "z_image_control_2.1.yaml"
    config = OmegaConf.load(videox_root / "config" / "z_image" / config_name)
    dtype = torch.bfloat16
    transformer = ZImageControlTransformer2DModel.from_pretrained(
        model,
        subfolder="transformer",
        low_cpu_mem_usage=True,
        torch_dtype=dtype,
        transformer_additional_kwargs=OmegaConf.to_container(config["transformer_additional_kwargs"]),
    ).to(dtype)
    from safetensors.torch import load_file

    missing, unexpected = transformer.load_state_dict(load_file(controlnet), strict=False)
    print(f"ControlNet loaded: {len(missing)} missing, {len(unexpected)} unexpected keys")
    vae = AutoencoderKL.from_pretrained(model, subfolder="vae").to(dtype)
    tokenizer = AutoTokenizer.from_pretrained(model, subfolder="tokenizer")
    text_encoder = Qwen3ForCausalLM.from_pretrained(
        model, subfolder="text_encoder", torch_dtype=dtype, low_cpu_mem_usage=True
    )
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(model, subfolder="scheduler")
    pipeline = ZImageControlPipeline(
        vae=vae,
        tokenizer=tokenizer,
        text_encoder=text_encoder,
        transformer=transformer,
        scheduler=scheduler,
    )
    # The 6B base and Union-ControlNet do not fit beside the text encoder on a
    # 12 GB 4070 Ti. VideoX-Fun's native offload keeps the program CUDA-only.
    pipeline.enable_model_cpu_offload(device="cuda")
    return pipeline


def canny_control(image: Image.Image) -> Image.Image:
    """Create a clean Canny guide without inheriting palette dithering."""
    import cv2
    import numpy as np

    rgb = np.asarray(image.filter(ImageFilter.GaussianBlur(1.5)).convert("RGB"))
    edges = cv2.Canny(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), 60, 160)
    return Image.fromarray(edges).convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "z-image-controlnet-cuda")
    parser.add_argument("--videox-root", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--controlnet", type=Path, required=True)
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--control-scale", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    pipeline = load_pipeline(args.videox_root, args.model, args.controlnet)
    import torch
    from videox_fun.utils.utils import get_image_latent
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        output = args.output_dir / scene_dir.name / f"background_zimage_controlnet_cuda@{args.scale:g}x.png"
        if args.skip_existing and output.exists():
            print(f"{scene_dir.name}: exists")
            continue
        _, source = scaled_source(scene_dir, args.scale, blur_radius=0.0)
        control = canny_control(source)
        mask = freedom_mask(scene_dir, source.size, feather=4.0)
        started = time.monotonic()
        with torch.no_grad():
            result = pipeline(
                prompt=prompt_for(scene_dir),
                negative_prompt="people, readable text, changed camera angle, changed architecture",
                height=source.height,
                width=source.width,
                generator=torch.Generator(device="cuda").manual_seed(args.seed),
                guidance_scale=0.0,
                image=get_image_latent(source, sample_size=[source.height, source.width])[:, :, 0],
                mask_image=get_image_latent(mask, sample_size=[source.height, source.width])[:, :1, 0],
                control_image=get_image_latent(control, sample_size=[source.height, source.width])[:, :, 0],
                num_inference_steps=args.steps,
                control_context_scale=args.control_scale,
            ).images[0]
        path = save_scene(
            args.output_dir,
            "zimage_controlnet_cuda",
            scene_dir,
            result,
            {**vars(args), "backend": "z-image-turbo-fun-union-2.1-2602", "control": "canny", "mask": "resource-derived"},
            args.scale,
        )
        print(f"{scene_dir.name}: {path} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
