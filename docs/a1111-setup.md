# Automatic1111 / Forge WebUI setup

The neural redraw pipeline (`tools/neural_redraw_rosetattoo_backgrounds.py`,
`tools/polish_rosetattoo_prompts.py` for prompts, and the sprite/font
upscalers) talks to a Stable Diffusion WebUI instance's `/sdapi/v1/...` REST
API. This works against either the original
[AUTOMATIC1111/stable-diffusion-webui](https://github.com/AUTOMATIC1111/stable-diffusion-webui)
or [lllyasviel/stable-diffusion-webui-forge](https://github.com/lllyasviel/stable-diffusion-webui-forge)
(Forge is what this project's own local install uses; the API surface is
identical for the purposes of this tooling). ComfyUI is **not** supported —
its workflow-graph JSON API is a different shape from the `/sdapi/v1/...`
calls this pipeline makes.

## 1. Install

Clone whichever of the two above you prefer, follow its own
platform-specific install instructions (creates a Python venv and installs
torch/xformers/etc.), then launch once normally to let it finish first-run
setup.

## 2. Install the ControlNet extension

The redraw pipeline requires the
[`sd-webui-controlnet`](https://github.com/Mikubill/sd-webui-controlnet)
extension (Forge ships it bundled; for vanilla A1111 install it via the
WebUI's Extensions tab or by cloning it into `extensions/`). Restart the
WebUI after installing.

**Watch out for two conflicting launch flags**: `--disable-extra-extensions`
disables ControlNet too (it isn't exempted), while loading *every* installed
extension can crash API startup on unrelated broken plugins. This project
includes a minimal Forge settings file
(`profiles/forge/controlnet-api.json`) that explicitly disables known-broken
extras (`adetailer`, various OpenPose/segment-anything editors,
`a1111-lycoris`) while leaving ControlNet enabled:

```sh
cd ../stable-diffusion-webui-forge
./webui.sh \
  --api \
  --listen \
  --port 7861 \
  --skip-version-check \
  --no-gradio-queue \
  --ui-settings-file /path/to/shcrt/profiles/forge/controlnet-api.json
```

On Windows (vanilla A1111), set `COMMANDLINE_ARGS` in `webui-user.bat` to
`--listen --api --port 7861 --xformers` and launch `webui-user.bat`, or
`python -u launch.py --listen --api --port 7861 --xformers` directly. For a
reliable *persistent* background service on Windows (survives
logout/reboot, unlike backgrounding via PowerShell `Start-Process` which was
unreliable in testing — the process would silently exit within seconds),
register it as a **Windows Scheduled Task** running
`<venv>\Scripts\python.exe -u launch.py --listen --api --port 7861 --xformers --skip-install`
in the webui's install directory, configured to run interactively as the
logged-in user with highest privileges.

Always verify with `--api` actually took effect and ControlNet loaded by
hitting `http://<host>:<port>/controlnet/model_list` — it should list your
installed ControlNet checkpoints.

## 3. Download models

- **Checkpoint(s)**: the tracked production profile
  (`profiles/neural/photographic-faithful.json`) uses
  `dreamshaperXL_v21TurboDPMSDE.safetensors`
  ([DreamShaper XL Turbo](https://civitai.com/models/112902/dreamshaper-xl)).
  The alternate `photographic-cinematic.json` profile uses
  `Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors`
  ([Juggernaut XL](https://civitai.com/models/133005/juggernaut-xl)), a
  non-Turbo photography-tuned checkpoint that renders richer material
  texture/warmer lighting at the cost of ~2x more steps. Place either under
  `models/Stable-diffusion/`.
- **ControlNet model**: use
  [`xinsir/controlnet-canny-sdxl-1.0`](https://huggingface.co/xinsir/controlnet-canny-sdxl-1.0)
  (`diffusion_pytorch_model_V2.safetensors`), **not** the official
  `diffusers/controlnet-canny-sdxl-1.0` checkpoint — the latter has a
  documented community issue producing genuinely reduced dynamic
  range/contrast ("washed out/gray") output regardless of
  denoising/CFG/seed, confirmed during this project's own calibration.
  Place it under `models/ControlNet/` (any filename); the WebUI derives its
  own `[hash]`-suffixed identifier, which you then set as
  `controlnet_model` in the profile JSON / `--controlnet-model` CLI flag
  (check `/controlnet/model_list` for the exact string Forge assigned).

## 4. Verify the API end-to-end

```sh
python3 tools/neural_redraw_rosetattoo_backgrounds.py \
  --api-url http://127.0.0.1:7861 \
  --wait \
  --settings-file profiles/neural/photographic-faithful.json \
  --scenes 1 \
  --scale 2 \
  --output-dir /tmp/a1111-smoketest \
  --scummvm-overrides /tmp/a1111-smoketest-overrides
```

If this produces a `background@2x.png` for scene 1 without errors, the
backend is correctly configured end-to-end (checkpoint + ControlNet model +
API all reachable).

## 5. Optional: a second GPU-backed endpoint

The tooling accepts any reachable `--api-url`, so a second machine on the
same LAN running its own WebUI instance works as a parallel generation
endpoint with zero code changes — just point `--api-url` at its
`http://<lan-ip>:<port>`. This project has run production batches split
across a local Mac (Forge, port 7860) and a remote Windows gaming PC
(vanilla A1111, port 7861) simultaneously this way. Copy/`scp` the same
checkpoint and ControlNet model files to keep output comparable across
endpoints, and verify checkpoint-switching works via
`POST /sdapi/v1/options` if you need to alternate checkpoints on one
instance rather than running two.

## Known API quirks encountered

- **`sampler_name` vs. `scheduler`**: some WebUI versions split the sampler
  name and its scheduler (e.g. `"DPM++ 2M Karras"`) into two separate API
  fields, and a bare minimal `/sdapi/v1/txt2img` smoke-test call with only
  `sampler_name` set to a combined "X Karras" string can fail. In practice
  the full `img2img` + ControlNet-script code path this pipeline actually
  uses has tolerated combined sampler-name strings fine in testing, but if
  you see a `sampler_name`-related `400`/`422` error, try splitting the
  scheduler out into a dedicated `scheduler` field.
- **`edges@2x.png` isn't a real edge map when `--edge-source canny`
  (default) is used** — the tool hands the *unfiltered* init image straight
  to the API and lets ControlNet's own `canny` preprocessor run
  server-side, so the saved `edges@2x.png` is literally a copy of
  `init@2x.png`. To inspect the actual detected edges, call ControlNet's
  own `/controlnet/detect` endpoint directly, or use `--edge-source
  walk-zones`/`hotspots`/`combined` (see `tools/README.md`) which *do* save
  the real boundary image actually used, since no server-side preprocessor
  runs for those modes.
