# Local ESRGAN (spandrel) setup

`tools/upscale_rosetattoo_sprites.py` upscales cursors, inventory/item
icons, character walk cycles, and the overhead map with a real (non-
generative) ESRGAN-family super-resolution model, run entirely locally and
in-process via the [`spandrel`](https://github.com/chaiNNer-org/spandrel)
model-loading library and PyTorch (Apple `mps`, CUDA, or CPU) — no server,
no Automatic1111/Forge WebUI install required.

## Why not the FLUX pipeline, and why not Automatic1111

- **Not FLUX/generative redraw**: cursors, item icons, and walk-cycle frames
  are small, silhouette/hotspot-critical elements (often under 32x32px)
  where a diffusion model's tendency to invent new detail or drift geometry
  would shift a click hotspot or a character's hand position — see
  `tools/upscale_rosetattoo_sprites.py`'s module docstring. A real
  super-resolution model instead reconstructs plausible high-frequency
  detail from the *existing* pixels without redrawing content.
- **Not Automatic1111/Forge**: this project used to proxy ESRGAN through an
  Automatic1111/Forge WebUI's `/sdapi/v1/extra-single-image` REST endpoint,
  which meant running and keeping a whole separate WebUI server process
  alive just to call one non-generative operation. `spandrel` loads the
  same family of `.pth` checkpoints directly (no legacy `basicsr` dependency
  or its `torchvision` compatibility issues), so this is now a plain local
  function call.

## 1. Install Python dependencies

This repo's `pyproject.toml` has an `esrgan` optional dependency group
(`torch`, `spandrel`):

```sh
uv sync --extra esrgan
```

## 2. Download a model weight

[`RealESRGAN_x4plus`](https://github.com/xinntao/Real-ESRGAN) (BSD-3-Clause
licensed) is the default and works well for this game's painterly,
photographic-leaning sprite art:

```sh
mkdir -p models/esrgan
curl -L -o models/esrgan/RealESRGAN_x4plus.pth \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
```

This is a single ~64MB file — `models/` is gitignored, so this is a local
cache, not tracked in the repo. Any other ESRGAN/RealESRGAN-family `.pth`
checkpoint works too (e.g.
[`RealESRGAN_x4plus_anime_6B`](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth)
for flatter, more cel-shaded art) — pass it via `--model-path`.

## 3. Run

```sh
uv run python3 tools/upscale_rosetattoo_sprites.py \
  --resources rmouse_vgs omouse_vgs watson_vgs --scale 2
```

`spandrel` auto-detects the checkpoint's architecture from its state dict,
runs inference on the requested `--device` (default `mps`), then the result
is resized to the requested `--scale` if it differs from the model's native
scale (`RealESRGAN_x4plus` is natively 4x). Generation is fast — well under
a second per frame on an M2 Max — so upscaling an entire sprite set takes
seconds, not the minutes-to-hours the neural background redraw pipeline
needs.
