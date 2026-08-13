# Z-Image Turbo setup

`tools/img2img/z_image.py` is the native-Python, Apple-Silicon Z-Image Turbo
img2img backend. It does not require a WebUI. It uses MFLUX and its official
4-bit Z-Image Turbo conversion, which is small enough to coexist with this
project's FLUX and SDXL model caches on the Mac.

Download the model once into the ignored project cache:

```sh
uv sync --extra zimage
```

Run a representative scene on the local Mac:

```sh
uv run python3 tools/img2img/z_image.py --scenes 1 --scale 1
```

The first run downloads `filipstrand/Z-Image-Turbo-mflux-4bit` automatically
into the standard Hugging Face cache. The default output is
`generated/z-image-turbo/`. MFLUX's Z-Image img2img starts from the scaled
source with the requested strength. It is deliberately a low-strength visual
candidate, not the geometry-guaranteed production path: the regular Z-Image
img2img API has no mask input, and compositing rectangular resource bounds
back over its output visibly damages the redraw. Use SDXL ControlNet for a
geometry-locked production candidate. On the 32 GB M2 Max, start at
`--scale 1`; use a later deterministic upscaler for the 2x runtime asset. A
2x diffusion pass needs substantially more unified memory.

Z-Image Turbo Union ControlNet is available upstream through MFLUX, but it
requires separate model weights and is not used by this baseline until it has
passed the same geometry and visual validation. Do not use the regular
Z-Image runner to build a playable pack: it can change door, storefront, and
walkway geometry even at very low strength.
