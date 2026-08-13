# FLUX.1 + diffusers (Metal/mps) setup

`tools/flux_redraw_rosetattoo_backgrounds.py` is this project's neural
photoreal background redraw pipeline. It loads a
[FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)
pipeline directly, in-process, with Hugging Face `diffusers`, running on
Apple's Metal Performance Shaders (`mps`) backend, so nothing else needs to
be installed or kept running as a server.

## Why FLUX.1-schnell (not Dev), and why quantized

- **Schnell over Dev**: Schnell is Apache-2.0 licensed (Dev is a
  non-commercial research license) and is distilled for 1-4 step inference,
  which keeps each scene's generation time reasonable on a laptop GPU.
- **GGUF-quantized transformer**: the full bf16 FLUX transformer is ~24GB by
  itself. On a 32GB unified-memory Mac that leaves no headroom for the text
  encoders, VAE, OS, and other apps. This pipeline instead loads
  [city96/FLUX.1-schnell-gguf](https://huggingface.co/city96/FLUX.1-schnell-gguf)'s
  `Q4_K_S` quantization (~6.8GB) via `diffusers`' `GGUFQuantizationConfig`,
  dequantized on the fly to bf16 for compute. Other quant levels (`Q5_K_S`,
  `Q6_K`, `Q8_0`, up to `F16`) are available from the same repo if more
  memory/disk headroom is available and higher fidelity is wanted.
- **Full-precision text encoders/VAE**: the T5-XXL prompt encoder and VAE
  are comparatively small (~9.5GB and ~170MB) and disproportionately affect
  prompt-following and color accuracy, so they're kept at fp16 rather than
  quantized.

## 1. Install Python dependencies

This repo's `pyproject.toml` has a `flux` optional dependency group
(`torch`, `torchvision`, `diffusers`, `transformers`, `accelerate`,
`sentencepiece`, `protobuf`, `gguf`):

```sh
uv sync --extra flux
```

## 2. Download model components

`black-forest-labs/FLUX.1-schnell` on Hugging Face is gated behind a
click-through license agreement, which isn't practical to script headlessly.
This pipeline instead pulls the non-transformer components (text encoders,
tokenizers, VAE, scheduler, and the small `transformer/config.json` needed
to interpret the GGUF file) from
[`unsloth/FLUX.1-schnell`](https://huggingface.co/unsloth/FLUX.1-schnell), an
identical, non-gated mirror in `diffusers` format:

```sh
uv run python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='unsloth/FLUX.1-schnell',
    allow_patterns=[
        'model_index.json', 'scheduler/*', 'text_encoder/*', 'text_encoder_2/*',
        'tokenizer/*', 'tokenizer_2/*', 'vae/*', 'transformer/config.json',
    ],
    local_dir='models/flux-schnell',
)
"

uv run python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id='city96/FLUX.1-schnell-gguf',
    filename='flux1-schnell-Q4_K_S.gguf',
    local_dir='models/flux-schnell-gguf',
)
"
```

This downloads ~16GB total (the deliberately-skipped fp32/bf16 transformer
weights from the mirror would add another ~24GB). Both `models/` and
`generated/`/`mods/` are gitignored — this is a local cache, not tracked
game or generated art.

## 3. Run

```sh
uv run python3 tools/flux_redraw_rosetattoo_backgrounds.py \
  --scenes 1 18 36 --scale 2
```

The defaults (`--strength 0.60 --steps 24`) give FLUX useful **artistic
freedom** while a hard geometry lock keeps gameplay-critical boundaries fixed:
FLUX is free to
reimagine materials, decor, props, and lighting as richer, more cinematic
concept art. Small embedded text/signage is **not** preserved at this
strength and will typically render as garbled gibberish — the
`STYLE_PROMPT` asks the model to avoid inventing new readable text, but
doesn't attempt to preserve existing text.

This game still has to remain playable, though, and high-strength img2img
alone gives *no actual geometric guarantee*. The tool therefore uses two
layers of protection. `FluxInpaintPipeline` receives the inverse of each
scene's `protect_mask.png` for semantic conditioning. After generation, a
hard geometry lock restores only the boundaries of walk zones and all
resource-declared object states, plus a narrow edge map from the original
room and the explicit `structure_control.png` lines. This matters because a
hotspot record can be a 1x1 click anchor rather than the visible door or prop;
the union of all resource states and image boundaries covers both cases while
leaving interiors available for photoreal repainting. `--no-geometry-lock` is
available only for A/B experiments. `--protect-feather` (default `8.0`)
controls the soft inpaint conditioning transition; it does not weaken the
final hard lock.

Calibration notes from visual inspection
(`validation/contact-sheets/flux-strength-sweep.jpg`, comparing
strength=0.5/0.7/0.85 with steps=16/20/24 respectively, and
`validation/contact-sheets/flux-inpaint-protect-test.jpg`, confirming the
walk-zone/hotspot mask keeps protected regions ~3x closer to the original
by mean pixel difference than the freely-redrawn regions):

- `--strength` is the img2img denoise strength; `--steps` is the *base*
  step count from which `strength * steps` actual denoising steps are run.
  Higher strength needs proportionally more steps to converge cleanly —
  `strength=0.85, steps=24` (~20 real steps) was the best match for the
  "cinematic movie-set diorama" look the project is going for.
- `--denoise-radius` (default `1.0`) applies a light Gaussian pre-blur to
  the init image before upscaling. The original 256-color game art uses
  ordered dithering to fake smooth gradients (very visible on flat
  surfaces like ceilings); without this pre-blur FLUX tends to preserve
  that dithering noise instead of erasing it. Set to `0` to disable.
- `guidance_scale` is fixed at `0.0` in the script — FLUX.1-schnell is a
  CFG-distilled model and does not use classifier-free guidance.
- On Apple `mps`, generation is slow relative to SDXL/Forge (roughly
  25-30s/iteration on an M2 Max at `--scale 2`), so a full ~80-scene pack
  run takes several hours; use `--skip-existing` to resume an interrupted
  run, and `--scenes` to spot-check a handful first. See below for a much
  faster CUDA option if a discrete NVIDIA GPU is available.

Add `--scummvm-overrides mods/flux-hires-backgrounds` to also write a
ScummVM-ready `background@Nx.png` override pack per scene (see the main
[`tools/README.md`](../tools/README.md) and
`tools/run_rosetattoo_validation.py` for how to launch/validate a pack).

## 4. Optional: run on a discrete NVIDIA GPU instead of Apple `mps`

Pass `--device cuda` to run this same script on any machine with an
NVIDIA GPU and a CUDA-enabled PyTorch install (`pip install torch
torchvision --index-url https://download.pytorch.org/whl/cu124`, plus the
same `diffusers`/`transformers`/`accelerate`/`sentencepiece`/`protobuf`/
`gguf` packages as the `flux` extra). Copy `models/flux-schnell`,
`models/flux-schnell-gguf`, and `extracted/rosetattoo/` to the GPU
machine, then run the same command as above with `--device cuda`.

On `cuda`, `load_pipeline()` calls `enable_model_cpu_offload()` instead of
`pipe.to(device)`, keeping only the actively-running submodule resident
on the GPU. This matters on cards with less VRAM than the combined model
size (transformer + fp16 T5 text encoder + VAE, roughly 17GB total) —
without offloading, PyTorch silently spills into slow shared/system
memory instead of raising an out-of-memory error, which looks like it's
"working" (100% `nvidia-smi` utilization) while running far slower than
plain CPU/unified memory. With offloading enabled, a 12GB RTX 4070 Ti
runs at roughly **2-3s/iteration** — about 8-12x faster than an M2 Max on
`mps` — since the T5/VAE/transformer swap in and out of VRAM as needed
instead of all sitting resident at once.

For a long unattended run over SSH on Windows, launch via a Windows
Scheduled Task (`schtasks /Create ... && schtasks /Run ...`) pointed at a
`.bat` file that calls the full path to the Python interpreter (bare
`python` resolves to a non-functional Windows Store alias under Task
Scheduler's non-interactive session). This keeps the job running
independently of the SSH connection.
