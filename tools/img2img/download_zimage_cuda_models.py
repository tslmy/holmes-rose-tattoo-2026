#!/usr/bin/env python3
"""Download the official Z-Image Turbo and current Union-ControlNet weights."""

from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download


def main() -> None:
    root = Path(__file__).resolve().parents[2] / "models" / "z-image-cuda"
    base = root / "Z-Image-Turbo"
    controls = root / "controlnet"
    print(f"Downloading base model to {base}", flush=True)
    snapshot_download("Tongyi-MAI/Z-Image-Turbo", local_dir=base)
    print(f"Downloading Union-ControlNet to {controls}", flush=True)
    hf_hub_download(
        "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1",
        "Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors",
        local_dir=controls,
    )
    print("Z-Image CUDA model download complete", flush=True)


if __name__ == "__main__":
    main()
