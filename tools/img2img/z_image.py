#!/usr/bin/env python3
"""Native MLX Z-Image Turbo img2img renderer for Rose Tattoo."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import ROOT, prompt_for, restore_geometry, save_scene, scaled_source, scene_dirs


DEFAULT_MODEL = "filipstrand/Z-Image-Turbo-mflux-4bit"


def load_model(model_path: str, quantize: int | None):
    from mflux.models.common.config import ModelConfig
    from mflux.models.z_image import ZImage

    return ZImage(
        model_config=ModelConfig.z_image_turbo(),
        model_path=model_path,
        quantize=quantize,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "z-image-turbo")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--quantize", type=int, choices=(4, 8))
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--strength", type=float, default=0.45)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    model = load_model(args.model, args.quantize)
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        output = args.output_dir / scene_dir.name / f"background_zimage@{args.scale:g}x.png"
        if args.skip_existing and output.exists():
            print(f"{scene_dir.name}: exists")
            continue
        original, init = scaled_source(scene_dir, args.scale)
        started = time.monotonic()
        init_path = args.output_dir / ".inputs" / f"{scene_dir.name}@{args.scale:g}x.png"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        init.save(init_path)
        generated = model.generate_image(
            seed=args.seed,
            prompt=prompt_for(scene_dir),
            image_path=init_path,
            image_strength=args.strength,
            num_inference_steps=args.steps,
            width=init.width,
            height=init.height,
            guidance=0.0,
        )
        image = restore_geometry(generated.image.resize(init.size), scene_dir, original)
        path = save_scene(args.output_dir, "zimage", scene_dir, image, {**vars(args), "backend": "z-image-turbo-mflux"}, args.scale)
        print(f"{scene_dir.name}: {path} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
