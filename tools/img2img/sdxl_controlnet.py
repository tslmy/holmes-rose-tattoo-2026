#!/usr/bin/env python3
"""Native Python SDXL Canny-ControlNet inpaint renderer for Rose Tattoo."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import ROOT, canny_like, freedom_mask, prompt_for, restore_geometry, save_scene, scaled_source, scene_dirs


DEFAULT_FORGE = Path("/Users/lmy/Projects/stable-diffusion-webui-forge/models")


def load_pipeline(checkpoint: Path, controlnet: Path, device: str):
    import torch
    from diffusers import ControlNetModel, StableDiffusionXLControlNetInpaintPipeline

    # SDXL VAE decoding can produce NaNs in fp16 on MPS; use full precision
    # locally and retain fp16 only for CUDA's dedicated VRAM path.
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    control = ControlNetModel.from_single_file(str(controlnet), torch_dtype=dtype)
    pipe = StableDiffusionXLControlNetInpaintPipeline.from_single_file(
        str(checkpoint), controlnet=control, torch_dtype=dtype, use_safetensors=True
    )
    if device.startswith("cuda"):
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    pipe.enable_attention_slicing()
    return pipe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "sdxl-controlnet")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_FORGE / "Stable-diffusion" / "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors")
    parser.add_argument("--controlnet", type=Path, default=DEFAULT_FORGE / "ControlNet" / "controlnet-canny-sdxl-1.0-fp16.safetensors")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--strength", type=float, default=0.45)
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--control-scale", type=float, default=0.85)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    if not args.checkpoint.exists() or not args.controlnet.exists():
        raise SystemExit("Missing SDXL checkpoint or Canny ControlNet; pass --checkpoint/--controlnet explicitly.")
    pipe = load_pipeline(args.checkpoint, args.controlnet, args.device)
    import torch
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        output = args.output_dir / scene_dir.name / f"background_sdxl@{args.scale:g}x.png"
        if args.skip_existing and output.exists():
            print(f"{scene_dir.name}: exists")
            continue
        original, init = scaled_source(scene_dir, args.scale)
        started = time.monotonic()
        image = pipe(
            prompt=prompt_for(scene_dir), negative_prompt="text, letters, people, changed perspective, extra objects",
            image=init, mask_image=freedom_mask(scene_dir, init.size), control_image=canny_like(init),
            strength=args.strength, num_inference_steps=args.steps, guidance_scale=5.5,
            controlnet_conditioning_scale=args.control_scale,
            generator=torch.Generator(device="cpu").manual_seed(args.seed),
            height=init.height, width=init.width,
        ).images[0]
        image = restore_geometry(image.resize(init.size), scene_dir, original)
        path = save_scene(args.output_dir, "sdxl", scene_dir, image, {**vars(args), "backend": "sdxl-controlnet"}, args.scale)
        print(f"{scene_dir.name}: {path} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
