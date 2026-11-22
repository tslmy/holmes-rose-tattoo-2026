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

## CUDA Union-ControlNet (production candidate)

The Apple MFLUX Union runner is useful for experiments, but MFLUX is
Apple-MLX-only. The geometry-preserving CUDA candidate is
`tools/img2img/z_image_controlnet_cuda.py`, which wraps the official
VideoX-Fun Python library. It uses the current `lite-2602-8steps` Union checkpoint
with a clean Canny control image and a structural mask. It locks resource
regions plus a blurred source-edge band, so doorway/window/facade contours
remain anchored even when no game hotspot covers them; only material interiors
are repainted.

On the Windows RTX machine, clone the runtime and install its declared
dependencies once:

```powershell
git clone https://github.com/aigc-apps/VideoX-Fun.git D:\shcrt\VideoX-Fun
python -m pip install -r D:\shcrt\VideoX-Fun\requirements.txt
python -m pip install triton-windows==3.2.0.post21
```

Download the official weights into the ignored project model cache:

```powershell
python D:\shcrt\tools\img2img\download_zimage_cuda_models.py
```

Then run a deterministic two-times calibration scene:

```powershell
python D:\shcrt\tools\img2img\z_image_controlnet_cuda.py `
  --videox-root D:\shcrt\VideoX-Fun `
  --model D:\shcrt\models\z-image-cuda\Z-Image-Turbo `
  --controlnet D:\shcrt\models\z-image-cuda\controlnet\Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors `
  --scenes 1 --scale 2
```

Do not use the regular Z-Image runner to build a playable pack: it can change
door, storefront, and walkway geometry even at very low strength. Accept a
CUDA Union-ControlNet output only after visual and game-level validation.
