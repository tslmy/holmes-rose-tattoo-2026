#!/usr/bin/env python3
"""Native MLX Z-Image Turbo Union-ControlNet renderer for Rose Tattoo."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import ROOT, prompt_for, save_scene, scaled_source, scene_dirs


DEFAULT_MODEL = "filipstrand/Z-Image-Turbo-mflux-4bit"


def load_model(model_path: str, quantize: int | None):
    from mflux.models.common.config import ModelConfig
    from mflux.models.z_image.variants.controlnet.z_image_turbo_controlnet import ZImageTurboControlnet

    return ZImageTurboControlnet(
        model_config=ModelConfig.z_image_turbo_controlnet_union_2_1(),
        model_path=model_path,
        quantize=quantize,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "z-image-controlnet")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--quantize", type=int, choices=(4, 8))
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--control-scale", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    from mflux.models.z_image.variants.controlnet.control_types import ControlSpec, ControlType

    model = load_model(args.model, args.quantize)
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        output = args.output_dir / scene_dir.name / f"background_zimage_controlnet@{args.scale:g}x.png"
        if args.skip_existing and output.exists():
            print(f"{scene_dir.name}: exists")
            continue
        _, control = scaled_source(scene_dir, args.scale, blur_radius=0.0)
        control_path = args.output_dir / ".controls" / f"{scene_dir.name}@{args.scale:g}x.png"
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control.save(control_path)
        started = time.monotonic()
        generated = model.generate_image(
            seed=args.seed,
            prompt=prompt_for(scene_dir),
            controls=[
                ControlSpec(type=ControlType.canny, image_path=control_path, strength=1.0),
                ControlSpec(type=ControlType.mlsd, image_path=control_path, strength=0.55),
            ],
            num_inference_steps=args.steps,
            width=control.width,
            height=control.height,
            controlnet_strength=args.control_scale,
        )
        path = save_scene(
            args.output_dir,
            "zimage_controlnet",
            scene_dir,
            generated.image,
            {**vars(args), "backend": "z-image-turbo-union-controlnet-2.1", "controls": ["canny", "mlsd"]},
            args.scale,
        )
        print(f"{scene_dir.name}: {path} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
