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
  --scenes 1 18 36 --scale 2 --steps 12 --strength 0.25
```

Calibration notes from visual inspection (`validation/contact-sheets/`):

- `--strength` is the img2img denoise strength; `--steps` is the *base*
  step count from which `strength * steps` actual denoising steps are run
  (FLUX.1-schnell's distillation targets 1-4 steps *total*, not 1-4 steps
  regardless of strength, so a low strength needs a higher base `--steps` to
  still get 2-3 actual steps — `steps=12, strength=0.25` yields 3 real
  steps, which was the best-quality point found: enough to add real
  photographic texture/lighting without garbling small embedded text like
  the "EXTRA" newsstand sign or door house numbers, which higher strengths
  (0.35+) reliably corrupt into gibberish and start inventing extra people
  and props not in the original scene).
- `guidance_scale` is fixed at `0.0` in the script — FLUX.1-schnell is a
  CFG-distilled model and does not use classifier-free guidance.
- Generation is slow relative to SDXL/Forge (roughly 30-100s/step on an M2
  Max depending on thermal state and image size at `--scale 2`), so a full
  ~80-scene pack run takes several hours; use `--skip-existing` to resume
  an interrupted run, and `--scenes` to spot-check a handful first.

Add `--scummvm-overrides mods/flux-hires-backgrounds` to also write a
ScummVM-ready `background@Nx.png` override pack per scene (see the main
[`tools/README.md`](../tools/README.md) and
`tools/run_rosetattoo_validation.py` for how to launch/validate a pack).
