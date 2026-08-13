# Tools

Python pipeline for extracting, upscaling, and validating Rose Tattoo graphics.
Every command below also works with a plain `python3` as long as Pillow is
installed in the active interpreter; `uv run python3 ...` is the recommended,
reproducible way to get there (see the root [README](../README.md#setup)).

## Background pipeline

### 1. Extract room backgrounds and prompt metadata

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
boundary-reference rasters `walk_zones_mask.png`, `hotspots_mask.png`,
`structure_control.png` (both overlaid), and `protect_mask.png` (solid-filled
union of walk zones + hotspots, used by the liberal-art masked pass below).

To enumerate story-flag configurations without running ScummVM:

```sh
python3 tools/generate_rosetattoo_state_masks.py \
  --input-dir extracted/rosetattoo
```

This writes `scene_NNN/state_masks.json` and per-configuration masks under
`scene_NNN/states/`. It uses the same two-required-flag/all-must-pass rule,
including negative flag literals, as `Scene::checkSceneFlags()` in the engine.
The masks are useful for image
editing experiments, but the flag values in a player's save game still need to
be selected by the caller; the ordinary union mask remains the safe default.

An optional cloud benchmark can submit every scene to GPT Image in one
resumable pass:

```sh
uv sync --extra openai
OPENAI_API_KEY=... uv run python3 tools/openai_image_rosetattoo_pass.py \
  --input-dir extracted/rosetattoo \
  --output-dir generated/openai-image-pass
```

It uses `protect_mask.png` as an inpainting mask and writes request metadata
and hashes beside each output. ChatGPT Plus is not API access; this backend
requires a separately billed API key.

### 2. Baseline (non-neural) upscaling

Create deterministic enhanced outputs with Pillow's Lanczos resampler:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --input-dir extracted/rosetattoo \
  --output-dir enhanced/rosetattoo \
  --scale 4 \
  --method lanczos
```

The upscaler can also call an external model runner with `--method external`
and an `--external-command` template.

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

Build a full playable 2x background override pack in one shot:

```sh
python3 tools/build_playable_rosetattoo_hires.py
```

This uses existing extracted assets when available, otherwise extracts from
`scummvm/`, upscales every discovered room background with Lanczos, and
writes a patched-ScummVM-ready mod pack to `mods/hires-backgrounds/`. The
generated launch manifest defaults to the RGBA32 high-resolution compositor
so enhanced backgrounds are no longer quantized back into the game's
256-color palette. Add `--validate --scummvm scummvm-src/scummvm
--scene-capture-after 1=8` for a quick in-game validation pass; the generated
`manifest.json` records scene count, enhancement method, output paths, pixel
format, and a launch command.

### 3. Candidate review sheets

```sh
python3 tools/generate_rosetattoo_candidates.py \
  --input-dir extracted/rosetattoo \
  --output-dir generated/candidates \
  --scenes 1 18 36 \
  --scale 2
```

Each scene gets a `review_sheet.jpg`, `review.json`, candidate images, and
per-candidate ScummVM override trees under `generated/candidates/overrides/`.
Add `--capture-scummvm --scummvm scummvm-src/scummvm --data-dir scummvm
--capture-after 4` to include live in-game composite captures in each review
sheet.

To compare finished output across several full mod/profile runs side by
side (e.g. deciding between two `mods/` directories built with different
neural profiles), use:

```sh
python3 tools/build_variant_contact_sheet.py --scenes 1 2 4 7 18 36
```

Rows are variant directories under `mods/` (plus the original extracted
background for reference); columns are the given scene numbers.

### 4. Neural photoreal redraw (FLUX.1 + diffusers, local/mps)

`tools/flux_redraw_rosetattoo_backgrounds.py` loads a GGUF-quantized
FLUX.1-schnell pipeline directly in-process with Hugging Face `diffusers`,
running on Apple's Metal (`mps`) backend — no server/API to run. See
[`docs/flux-setup.md`](../docs/flux-setup.md) for one-time model setup,
then:

```sh
python3 tools/flux_redraw_rosetattoo_backgrounds.py \
  --scenes 1 18 36 --scale 2 --steps 12 --strength 0.25 \
  --scummvm-overrides mods/flux-hires-backgrounds
```

It writes a two-column original-vs-redraw contact sheet (default
`validation/contact-sheets/flux-redraws.jpg`) for quick visual review, and
optionally a ScummVM-ready override pack (`background@Nx.png` + prompt/
metadata sidecars).

Useful flags:

- `--skip-existing` — resume an interrupted/long batch without regenerating
  finished scenes.
- `--strength` — img2img denoise strength; lower preserves more of the
  original geometry and embedded text. `--steps` is the *base* step count,
  from which `strength * steps` actual denoising steps are run (FLUX.1-
  schnell's distillation targets 1-4 *actual* steps, so a low strength needs
  a higher base `--steps` to still land in that range). `--steps 12
  --strength 0.25` (3 real steps) was found to be the best-quality point by
  visual inspection: strength ≥0.35 reliably garbles small embedded text
  (signage, door numbers) and starts inventing extra people/props not in
  the original scene.
- `--prompt-brief-dir` (default `profiles/neural/prompt-briefs`) — per-scene
  LLM-polished prompt briefs; see below for how to (re)generate them.

For LLM-polished prompts (recommended — every scene gets one, with no
per-scene manual overrides):

```sh
python3 tools/polish_rosetattoo_prompts.py \
  --provider ollama \
  --ollama-url http://127.0.0.1:11434 \
  --ollama-model qwen3.5:9b-mlx \
  --ollama-api generate \
  --input-dir extracted/rosetattoo \
  --output-dir generated/prompt-briefs
```

Ollama works well enough that LM Studio isn't needed; use a vision-capable
model with thinking disabled (otherwise the model can spend its whole
response budget on hidden reasoning and return no usable prompt text). The
resulting `visual_brief.txt` files are short, transformed noun-phrase lists,
not the game's original narrative prose, so they're checked into git under
`profiles/neural/prompt-briefs/scene_NNN/` — future runs don't need to
regenerate them (or have Ollama installed) unless a scene's extracted
metadata changes. `flux_redraw_rosetattoo_backgrounds.py --prompt-brief-dir`
defaults to this checked-in directory and only falls back to a scene's raw
`prompt.txt` if a brief is genuinely missing.

## Cursors, items, character, and font sprites

Everything that *moves* in Rose Tattoo (mouse cursors, inventory/interactive
item icons, and character walk-cycle sprites) is stored separately from room
backgrounds, in a proprietary VGS frame format packed inside the game's
`.LIB`/`.LIC` archives (`VGS.LIB`, `WALK.LIB`, `TALK.LIB`). Unlike room
backgrounds (one full 640x480 frame per `.RRM`), each of these resources is a
small sequence of individually-offset frames.

```sh
# Extract cursor frames (writes extracted/sprites/<resource>/frame_NNN.png + metadata.json)
python3 tools/extract_rosetattoo_sprites.py --resources RMOUSE.VGS OMOUSE.VGS

# Extract a character walk cycle - these have no palette of their own at
# runtime (they reuse whichever room palette is currently loaded), so borrow
# one from a specific room's export for correct-color output:
python3 tools/extract_rosetattoo_sprites.py --resources WATSON.VGS --palette-scene 1

# Upscale extracted frames via a local ESRGAN-family super-resolution model
# (spandrel + PyTorch, no server - see docs/esrgan-setup.md for the one-time
# `uv sync --extra esrgan` + model weight download). No ControlNet/redraw -
# these are small, silhouette/hotspot-critical elements where hallucinated
# new content would break gameplay recognizability:
python3 tools/upscale_rosetattoo_sprites.py --resources rmouse_vgs omouse_vgs watson_vgs --scale 2
```

Each frame's alpha channel is separated before upscaling (flattened onto a
neutral fill so ESRGAN doesn't invent detail in fully-transparent regions),
then resized and re-thresholded independently, keeping the crisp binary
cutout edges the engine itself always uses (no partial-alpha blending).
Output filenames are scale-qualified (`frame_NNN@Sx.png`), matching the
background pipeline's `background@Nx.png` convention.

Beyond the room cursor set and Watson, the same pipeline has also been run
over the player's own walk cycles across coat/hat states (`SVGAWALK.VGS`,
`NOHAT.VGS`, `COATWALK.VGS`, and the `CT*`/`HT*`/`JT*`/`TDOWNRG` directional
variants), the named NPCs (`MYCROFT.VGS`, `TOBY.VGS`, `TUX.VGS`,
`WIGGINS.VGS`), every generic reused NPC body-type walk cycle
(`3T*`/`GT*`/`GTS*`/`IT*`/`QT*`/`TW*`/`TRIGHT.VGS`/`TUPRIGHT.VGS`, and
`GREEN.VGS` - Rose Tattoo reuses these same handful of sprite sheets across
many different one-off/background NPCs scene to scene, so covering them has
much higher leverage than extracting every named character individually),
every inventory/interactive item icon (`ITEM01.VGS`-`ITEM84.VGS`), the dart
board minigame set (`DARTBD.VGS`, `DARTMAP.VGS`, `DARTS.VGS`,
`DARTSLFT.VGS`), the opium den and disguise sprite sheets (`OPIUM.VGS`,
`COAT3.VGS`), the pointing-hand cursor variants (`HAND1.VGS`, `HAND2.VGS`),
the small interface glyph set (`INTRFACE.VGS`), the loading-spinner frames
(`LOADING.VGS`, `LOADING0-2.VGS`), and the standalone full-frame stills
(`JOURNAL.VGS`, `PAPER.VGS`) - every resource in `VGS.LIB` is now extracted
and upscaled. The per-scene `RES##.VGS`/`RES##A.VGS`/`RES##B.VGS` resources
(42 full 640x480 static closeup/cutscene stills, not sprites) are likewise
fully extracted and upscaled, each with the numbered scene's own room
palette passed via `--palette-scene N` (most of these resources embed their
own VGA palette anyway, so the flag is a no-op for them, but is harmless to
pass). `TALK.LIB`'s ~1400 entries are **not** portrait art at all - despite
the name, every entry is a `.TLK` dialogue-script/branching-conversation
file (see `Talk::loadTalkFile()` in `talk.cpp`), not an image resource, so
there is nothing to extract there.

An engine-side runtime override is wired up for the room cursor set
(`Screen::loadRoseTattooHiresCursorOverride()` in the ScummVM fork), the
overhead map background (see below), and the live scene's walking
characters and bg-shape objects (`Screen::queueRoseTattooHiresSceneSprite()`,
wired into `TattooScene::drawAllShapes()`). Item/inventory icon overrides are
wired up in `widget_inventory.cpp`. Any character/bg-shape whose current
resource+frame has no matching override PNG on disk simply falls back to
the plain nearest-neighbor-upscaled native art for that shape - a quiet
degradation, not a crash - so extracting a missing resource with the
commands above and copying it into a production mod's `sprites/` directory
is always safe to do incrementally. Note that several of the now-extracted
resources (`RES##.VGS`, `JOURNAL.VGS`, `LOADING*.VGS`, `DART*.VGS`,
`HAND*.VGS`, `INTRFACE.VGS`, `PAPER.VGS`, `OPIUM.VGS`) currently have **no
engine-side consumer at all** for a hires override (unlike scene
backgrounds/cursor/map/scene-sprites) - they are extracted and upscaled so
the assets exist and are ready, but new engine wiring would be needed
before they'd visibly affect gameplay.

### Fonts (`FONT1.VGS`-`FONT8.VGS`)

The in-game bitmap fonts are extracted and upscaled the same way, but through
a dedicated `--mode font` path instead of the ESRGAN endpoint:

```sh
python3 tools/extract_rosetattoo_sprites.py --resources FONT1.VGS FONT2.VGS FONT3.VGS FONT4.VGS FONT5.VGS FONT6.VGS FONT7.VGS FONT8.VGS
python3 tools/upscale_rosetattoo_sprites.py --resources font1_vgs font2_vgs font3_vgs font4_vgs font5_vgs font6_vgs font7_vgs font8_vgs --mode font --scale 4
```

Font glyphs are tiny (often under 10x10px) monochrome stencils — the RGB
channel is always solid black, with the actual glyph shape living entirely in
alpha. `--mode font` does a fast, local, no-API Lanczos resize of just the
alpha channel, producing clean anti-aliased glyph edges instead of a blurry
photographic upscale. This is a tooling-only deliverable for the *bitmap*
glyphs; the engine's in-game tooltip/UI text instead uses a real vector font
at runtime (see `Screen::getRoseTattooHiresFont()` in the ScummVM fork's
`screen.cpp`, tracked as a Git submodule at `scummvm-src/` - see the root
[README's ScummVM Strategy section](../README.md#scummvm-strategy)).

### Overhead/travel map (`MAP.VGS`)

Unlike room backgrounds, `MAP.VGS` stores no palette of its own — the engine
loads a separate `MAP.PAL` resource for it — so extraction needs
`--palette-resource` instead of `--palette-scene`:

```sh
python3 tools/extract_rosetattoo_sprites.py --resources MAP.VGS --palette-resource MAP.PAL
python3 tools/extract_rosetattoo_sprites.py --resources MAPICONS.VGS --palette-resource MAP.PAL

# The map is a single large (1280x960 native) illustration full of fine
# engraving-style linework and ~30 clickable location pins, so - like
# cursors/items - it gets the same non-diffusion real-ESRGAN upscale, not a
# ControlNet redraw pass that could hallucinate away small geometry:
python3 tools/upscale_rosetattoo_sprites.py --resources map_vgs mapicons_vgs --scale 2
```

The engine looks for the result at `sprites/map_vgs/frame_000@<scale>x.png`
under `$SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES`, so copying
`enhanced/sprites/map_vgs/` into the production mod's `sprites/` directory is
enough to pick it up. Location-pin icons (`MAPICONS.VGS`) are also baked into
the map's hires background via `Screen::paintRoseTattooHiresWorldSprite()`
in the ScummVM fork.

## Validation and playtesting

Launch ScummVM for scene-jump validation:

```sh
python3 tools/run_rosetattoo_validation.py --save-slot 1 --scenes 1 2 18
```

On macOS the helper auto-detects
`/Applications/ScummVM.app/Contents/MacOS/scummvm` when ScummVM is installed
as an app bundle.

For deterministic scene validation, launch a patched binary (built from the
`scummvm-src/` submodule, which already includes the
`SCUMMVM_SHERLOCK_TATTOO_START_SCENE` debug hook) with `--start-scene`:

```sh
python3 tools/run_rosetattoo_validation.py \
  --scummvm scummvm-src/scummvm \
  --start-scene 36 \
  --asset-overrides generated/overrides \
  --capture-after 3 \
  --capture-output validation/screenshots/desktop-scene-036.png
```

`--capture-after` captures the ScummVM window directly on macOS by default.
Use `--capture-mode screen` only when a full-desktop capture is useful.

The high-resolution renderer presents a true-color composite from
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

This keeps the original 640x480 game logic and sprite/UI composition, opens a
1280x960 (or larger, for higher `--hires-scale`) ScummVM backend, and
composites a true-color high-resolution room background underneath the
native moving layers. Use `--hires-format clut8` to compare against the
older palette-mapped compositor, or `--hires-format rgb565` as a lighter
high-color fallback.

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

For your own playtesting (not just scripted scene captures), the standard
launch command for a full high-resolution mod pack is:

```sh
python3 tools/run_rosetattoo_validation.py \
    --scummvm scummvm-src/scummvm \
    --asset-overrides mods/neural-hires-backgrounds-faithful \
    --hires-scale 2 \
    --hires-format rgba32 \
    --save-dir "/Users/lmy/Library/Application Support/ScummVM/Savegames" \
    --save-slot 1
```

## Calibration notes

- **FLUX img2img strength/steps**: `flux_redraw_rosetattoo_backgrounds.py`'s
  `--steps` is a *base* step count, from which `strength * steps` actual
  denoising steps run (FLUX.1-schnell's distillation targets 1-4 *actual*
  steps). `--steps 12 --strength 0.25` (3 real steps) was found by visual
  inspection (contact sheets under `validation/contact-sheets/`) to be the
  best-quality point: strength ≥0.35 reliably garbles small embedded text
  (signage, door numbers) and starts inventing extra people/props not in
  the original scene.
- Compare calibration sheets under `validation/contact-sheets/` before
  committing to a full ~80-scene run — it takes several hours on Apple
  Silicon.

See [`docs/reproducing.md`](../docs/reproducing.md) for the end-to-end,
from-scratch reproduction guide, and
[`docs/flux-setup.md`](../docs/flux-setup.md) for the FLUX.1 + diffusers
pipeline setup this project depends on.
