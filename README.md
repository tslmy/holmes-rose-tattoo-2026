# Rose Tattoo Modernization Lab

Tools and notes for modernizing the graphics pipeline for *The Lost Files of
Sherlock Holmes: The Case of the Rose Tattoo*.

This repository intentionally does not track original game files, extracted
backgrounds, generated art, or a local ScummVM checkout. Keep those assets local
under ignored directories such as `scummvm/`, `extracted/`, and `scummvm-src/`.

## Current Workflow

Extract room backgrounds and prompt metadata from a local ScummVM-ready game
directory:

```sh
python3 tools/extract_rosetattoo_assets.py --data-dir scummvm --scenes 1 2 18
```

Run all available room resources:

```sh
python3 tools/extract_rosetattoo_assets.py --data-dir scummvm
```

Outputs are written to `extracted/rosetattoo/`, which is ignored because it
contains derived game artwork and extracted game text. Each `scene_NN/`
directory includes `background.png`, `metadata.json` (object bounds, walk
zones, hotspots, description text, and prompt terms), `prompt.txt`, and
boundary-reference rasters `walk_zones_mask.png`, `hotspots_mask.png`, and
`structure_control.png` (both overlaid) for ControlNet-guided neural redraws.

Create baseline enhanced outputs:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --input-dir extracted/rosetattoo \
  --output-dir enhanced/rosetattoo \
  --scale 4 \
  --method lanczos
```

The upscaler can also call an external model runner with `--method external` and
an `--external-command` template.

Create ScummVM-ready high-resolution background overrides:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --input-dir extracted/rosetattoo \
  --output-dir enhanced/rosetattoo-2x \
  --scenes 2 18 36 \
  --scale 2 \
  --method lanczos \
  --scummvm-overrides generated/overrides-2x
```

This writes normal enhancement outputs under `enhanced/` and copies validated
runtime assets to `generated/overrides-2x/scene_NNN/background@2x.png`, along
with prompt and metadata sidecars.

Build a full playable 2x background override pack:

```sh
python3 tools/build_playable_rosetattoo_hires.py
```

This uses existing extracted assets when available, otherwise extracts from
`scummvm/`, upscales every discovered room background with Lanczos, and writes a
patched-ScummVM-ready mod pack to `mods/hires-backgrounds/`. The generated
launch manifest defaults to the RGBA32 high-resolution compositor so enhanced
backgrounds are no longer quantized back into the game's 256-color palette.

To also capture a quick in-game validation pass:

```sh
python3 tools/build_playable_rosetattoo_hires.py \
  --validate \
  --scummvm scummvm-src/scummvm \
  --scene-capture-after 1=8
```

The generated `mods/hires-backgrounds/manifest.json` records the scene count,
enhancement method, output paths, pixel format, and a launch command for the
local patched ScummVM build.

Generate reviewable candidate sets:

```sh
python3 tools/generate_rosetattoo_candidates.py \
  --input-dir extracted/rosetattoo \
  --output-dir generated/candidates \
  --scenes 1 18 36 \
  --scale 2
```

Each scene gets a `review_sheet.jpg`, `review.json`, candidate images, and
per-candidate ScummVM override trees under `generated/candidates/overrides/`.
To include live in-game composite captures in each review sheet, add:

```sh
python3 tools/generate_rosetattoo_candidates.py \
  --input-dir extracted/rosetattoo \
  --output-dir generated/candidates-ingame \
  --scenes 36 \
  --scale 2 \
  --candidates lanczos \
  --capture-scummvm \
  --scummvm scummvm-src/scummvm \
  --data-dir scummvm \
  --capture-after 4
```

Run a local neural photoreal redraw pilot through an Automatic1111/Forge API:

```sh
python3 tools/neural_redraw_rosetattoo_backgrounds.py \
  --api-url http://127.0.0.1:7860 \
  --wait \
  --checkpoint dreamshaperXL_v21TurboDPMSDE.safetensors \
  --scenes 18 \
  --scale 2 \
  --denoising-strength 0.42 \
  --controlnet-model 'controlnet-canny-sdxl-1.0-fp16 [7b2ce256]' \
  --scummvm-overrides mods/neural-hires-backgrounds
```

For the photographic-redraw target, use a stronger calibration pass before
launching a full batch. The goal is not just low-drift cleanup; it should look
like a plausible period photograph while preserving exits, walkable geometry,
and puzzle-relevant props:

```sh
python3 tools/neural_redraw_rosetattoo_backgrounds.py \
  --api-url http://127.0.0.1:7860 \
  --wait \
  --checkpoint Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors \
  --scenes 2 18 36 \
  --scale 2 \
  --steps 14 \
  --cfg-scale 4.2 \
  --denoising-strength 0.56 \
  --controlnet-model 'controlnet-canny-sdxl-1.0-fp16 [7b2ce256]' \
  --controlnet-weight 0.55 \
  --controlnet-guidance-end 0.5 \
  --tile-width 1536 \
  --tile-overlap 256 \
  --output-dir generated/neural-redraws-juggernaut-calibration \
  --scummvm-overrides mods/neural-hires-backgrounds-juggernaut-calibration
```

Compare calibration sheets under `validation/contact-sheets/` before committing
to a full run. RMS drift is only a tripwire, not a quality score: conservative
settings around `7-17` may keep rooms recognizable, while stronger photographic
settings can improve atmosphere or destroy object identity depending on the
scene.

The tracked balanced production profile lives at
`profiles/neural/photographic-balanced.json`. It uses a middle setting rather
than the most aggressive calibration: lower denoise, stronger ControlNet, and
special tighter overrides for scenes with masks or heavy fog/water overlays.

When the model invents or loses too much detail, switch to
`profiles/neural/photographic-faithful.json` for calibration. That profile keeps
ControlNet in balanced mode, lowers denoise, increases tile overlap, strengthens
negative prompts against replaced props/architecture, and blends a small amount
of the upscaled original back into the neural result. Avoid
`controlnet_control_mode: "ControlNet is more important"` for now; on Forge/MPS
it produced very dark outputs despite preserving silhouettes.

For LLM-polished prompts, Ollama works well enough that LM Studio is not needed
yet. Use a vision-capable model such as `qwen3.5:9b-mlx` through Ollama's
`/api/generate` endpoint with thinking disabled; otherwise Qwen can spend the
whole response budget on hidden reasoning and return little usable prompt text.
The prompt-polisher treats the source image as authoritative, asks the model for
a compact visual inventory, and writes ignored prompt sidecars under
`generated/`:

```sh
python3 tools/polish_rosetattoo_prompts.py \
  --provider ollama \
  --ollama-url http://127.0.0.1:11434 \
  --ollama-model qwen3.5:9b-mlx \
  --ollama-api generate \
  --manual-brief-dir profiles/neural/prompt-brief-overrides \
  --input-dir extracted/rosetattoo \
  --output-dir generated/prompt-briefs-ollama-qwen9-inventory-v4
```

Dense object inventories can still mislead Stable Diffusion on fragile scenes.
Tracked sparse overrides live in `profiles/neural/prompt-brief-overrides/` and
are copied verbatim into the generated cache. They are deliberately small:
ControlNet and the source image should carry most composition detail, while the
brief only pins a few puzzle-relevant scene facts.

When launching Forge for this pipeline, make sure the ControlNet extension is
enabled. The normal `--disable-extra-extensions` shortcut disables ControlNet,
while loading every local extension can crash API initialization on unrelated
plugins. This project includes a minimal Forge settings file that disables known
broken extras while keeping ControlNet active:

```sh
cd ../stable-diffusion-webui-forge
./webui.sh \
  --api \
  --listen \
  --port 7861 \
  --skip-version-check \
  --no-gradio-queue \
  --ui-settings-file /Users/lmy/Projects/shcrt/profiles/forge/controlnet-api.json
```

```sh
python3 tools/neural_redraw_rosetattoo_backgrounds.py \
  --api-url http://127.0.0.1:7861 \
  --wait \
  --settings-file profiles/neural/photographic-faithful.json \
  --prompt-brief-dir generated/prompt-briefs-ollama-qwen9-inventory-v4 \
  --scenes 4 18 \
  --scale 2 \
  --seed 108000 \
  --output-dir generated/neural-redraws-faithful-ollama-inventory-v4-calibration \
  --scummvm-overrides mods/neural-hires-backgrounds-faithful-ollama-inventory-v4-calibration
```

The neural redraw tool creates a 2x init image, an edge-control image, a
resource-pinned realism prompt, and a generated `background@2x.png` for each
scene. Outputs remain ignored under `generated/` and `mods/`; the tracked code
only stores the repeatable pipeline. Use `--skip-existing` to resume long
batches. Wide scrolling rooms are split into overlapping horizontal tiles by
default so panoramic backgrounds do not need to be generated in one enormous
diffusion request.

### ControlNet structural guidance source (`--edge-source`)

By default (`--edge-source canny`) the ControlNet control image is the
upscaled background handed to Automatic1111's own `canny` preprocessor. This
constrains the redraw to the room's *pixel-level* edges, which can be overly
strict about fine painted texture, dithering, and brush-stroke noise rather
than just architecture and geometry.

`extract_rosetattoo_assets.py` also extracts genuine game-semantic boundary
data per scene: `walk_zones_mask.png` (the room's rectangular walkable-floor
areas, matching the engine's own pathfinding zones) and `hotspots_mask.png`
(clickable/examinable object bounds). Pass `--edge-source walk-zones`,
`--edge-source hotspots`, or `--edge-source combined` (both overlaid, from
`structure_control.png`) to use these instead - the ControlNet preprocessor is
skipped (module `none`) since the mask is already the final boundary image.
This tends to preserve navigable geometry and interactive silhouettes without
dictating brush-stroke detail, letting the model repaint texture more freely.

Older `extracted/rosetattoo/scene_*` directories predating this feature won't
have the mask files; the tool warns and falls back to `canny` for those
scenes. Re-run `extract_rosetattoo_assets.py` to regenerate them.

For a resumable full-room pass, point `--output-dir` and `--scummvm-overrides`
at stable ignored directories and rerun with `--skip-existing` whenever the
local model runner is interrupted. The batch validator rejects blank captures,
which helps catch early macOS window-capture misses during unattended scene
checks.

Launch ScummVM for scene-jump validation:

```sh
python3 tools/run_rosetattoo_validation.py --save-slot 1 --scenes 1 2 18
```

On macOS the helper auto-detects `/Applications/ScummVM.app/Contents/MacOS/scummvm`
when ScummVM is installed as an app bundle.

For deterministic scene validation, apply the local ScummVM patch and launch a
patched binary with `--start-scene`:

```sh
git apply patches/scummvm/rosetattoo-start-scene-env.patch
python3 tools/run_rosetattoo_validation.py \
  --scummvm scummvm-src/scummvm \
  --start-scene 36 \
  --asset-overrides generated/overrides \
  --capture-after 3 \
  --capture-output validation/screenshots/desktop-scene-036.png
```

`--capture-after` captures the ScummVM window directly on macOS by default. Use
`--capture-mode screen` only when a full-desktop capture is useful.
External room backgrounds are loaded from
`<override-dir>/scene_036/background.png` and must currently match the native
room dimensions.

The experimental high-resolution renderer can also present a 2x background from
`<override-dir>/scene_036/background@2x.png`:

```sh
python3 tools/run_rosetattoo_validation.py \
  --scummvm scummvm-src/scummvm \
  --start-scene 36 \
  --asset-overrides generated/overrides \
  --hires-scale 2 \
  --hires-format rgba32 \
  --capture-after 5 \
  --capture-output validation/screenshots/window-hires-scene-036.png
```

This first prototype keeps the original 640x480 game logic and sprite/UI
composition, opens a 1280x960 ScummVM backend, and composites a true-color
high-resolution room background underneath the native moving layers. Use
`--hires-format clut8` to compare against the older palette-mapped compositor,
or `--hires-format rgb565` as a lighter high-color fallback.

Batch-capture several scenes and build a contact sheet:

```sh
python3 tools/batch_validate_rosetattoo.py \
  --scummvm scummvm-src/scummvm \
  --asset-overrides generated/overrides-2x \
  --hires-scale 2 \
  --hires-format rgba32 \
  --scenes 1 2 18 36 \
  --capture-after 4 \
  --scene-capture-after 1=8 \
  --output-dir validation/screenshots/batch-hires-2x
```

The batch helper records successful screenshots and failures in
`validation/screenshots/batch-hires-2x/report.json`; failed scene launches do
not prevent later scenes from being attempted unless `--fail-fast` is passed.

Inspect high-resolution renderer layers with `--hires-debug`:

```sh
python3 tools/run_rosetattoo_validation.py \
  --scummvm scummvm-src/scummvm \
  --start-scene 36 \
  --asset-overrides generated/overrides-2x \
  --hires-scale 2 \
  --hires-debug mask \
  --capture-after 4 \
  --capture-output validation/screenshots/scene-036-mask.png
```

Debug modes are `composite` (default), `background`, `mask`, and `native`.
`mask` highlights the native pixels currently being drawn over the high-res
background; this is the fastest way to find overlay or palette-animation
mistakes.

## ScummVM Strategy

The clean default is to keep this as a game-modernization/modding repository and
treat ScummVM as an external dependency:

- Use the installed ScummVM runtime for playing the original game.
- Use a local `scummvm-src/` checkout for reference or experiments.
- Keep repeatable engine changes as patches under `patches/scummvm/`.
- Add ScummVM as a Git submodule only when this repo needs to build a patched
  engine as part of its normal workflow.

If we implement external high-resolution background overrides, that can live in
either:

- a small patch set against a ScummVM checkout, kept here as patches/scripts; or
- a real ScummVM fork/submodule once engine development becomes central.

## Safety

Do not commit:

- original `RES*.RRM`, `SPEECH*.LIB`, `TALK.LIB`, `WALK.LIB`, or DOS install
  files
- extracted room PNGs or prompt files generated from copyrighted game data
- Stable Diffusion outputs derived from the game assets
