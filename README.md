# Rose Tattoo Modernization Lab

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Tools and notes for modernizing the graphics pipeline for *The Lost Files of
Sherlock Holmes: The Case of the Rose Tattoo*.

This repository intentionally does not track original game files, extracted
backgrounds, generated art, or a local ScummVM checkout. Keep those assets local
under ignored directories such as `scummvm/`, `extracted/`, and `scummvm-src/`.

## Setup

Python tooling is managed with [`uv`](https://github.com/astral-sh/uv). Install
uv, then sync the project's virtual environment (pins Pillow, the only
third-party dependency, per `pyproject.toml`/`uv.lock`):

```sh
uv sync
```

Run any tool via `uv run`, e.g.:

```sh
uv run python3 tools/extract_rosetattoo_assets.py --data-dir scummvm --scenes 1 2 18
```

Every command below also works with a plain `python3` as long as Pillow is
installed in the active interpreter (`uv run` is just the recommended,
reproducible way to get there).

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
  --controlnet-model 'controlnet-canny-sdxl-1.0-xinsir-v2 [ab7dc06d]' \
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
  --controlnet-model 'controlnet-canny-sdxl-1.0-xinsir-v2 [ab7dc06d]' \
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

The tracked production profile lives at
`profiles/neural/photographic-faithful.json`. It keeps ControlNet in a
prompt-favoring control mode, lowers denoise, increases tile overlap,
strengthens negative prompts against replaced props/architecture, and blends
a small amount of the upscaled original back into the neural result. Avoid
`controlnet_control_mode: "ControlNet is more important"` for now; on Forge/MPS
it produced very dark outputs despite preserving silhouettes. (An earlier
`photographic-balanced.json` profile - a middle setting between this and the
most aggressive calibration - was tried first but abandoned after day one in
favor of `photographic-faithful.json`, and has since been removed along with
its stale `mods/neural-hires-backgrounds-balanced*` output.)

For LLM-polished prompts, Ollama works well enough that LM Studio is not needed
yet. Use a vision-capable model such as `qwen3.5:9b-mlx` through Ollama's
`/api/generate` endpoint with thinking disabled; otherwise Qwen can spend the
whole response budget on hidden reasoning and return little usable prompt text.
The prompt-polisher treats the source image as authoritative and asks the model
for a compact, comma-separated visual inventory (35-80 words, no full
sentences) - deliberately not the raw extracted game text, both to keep
Stable Diffusion focused on static background detail and to keep the output
safely paraphrased rather than a verbatim reproduction of the game's writing.

**Every scene gets an LLM-generated brief, with no per-scene manual
overrides.** An earlier iteration of this pipeline hand-curated verbatim,
sentence-level "manual" overrides for a handful of fragile scenes while
leaving the rest to raw `prompt.txt`, which meant different scenes were
silently going through materially different prompt pipelines. All scenes now
go through the same `polish_with_ollama()` LLM path, and the sanitizer strips
crime-story/mood words and caps output length uniformly, so no per-scene
special-casing is needed anymore. The `--manual-brief-dir` escape hatch still
exists in the script for genuine emergencies, but the default workflow should
not need it:

```sh
python3 tools/polish_rosetattoo_prompts.py \
  --provider ollama \
  --ollama-url http://127.0.0.1:11434 \
  --ollama-model qwen3.5:9b-mlx \
  --ollama-api generate \
  --input-dir extracted/rosetattoo \
  --output-dir generated/prompt-briefs
```

The resulting `visual_brief.txt` files are short, transformed noun-phrase
lists (e.g. `red granite obelisk weathered hieroglyph inscriptions polished
granite stairs iron lampposts ...`), not the game's original narrative prose,
so they're checked into git under `profiles/neural/prompt-briefs/scene_NNN/`
for every scene - future runs of this pipeline don't need to regenerate them
(or have Ollama installed at all) unless a scene's extracted metadata
changes. `neural_redraw_rosetattoo_backgrounds.py --prompt-brief-dir` defaults
to this checked-in directory and only falls back to a scene's raw
`prompt.txt` if a brief is genuinely missing.

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

### Sharper, less dithered redraws (`--init-upscaler`)

By default the init image handed to the diffusion pass is now built with a
real super-resolution model (`--init-upscaler`, default `R-ESRGAN 4x+`, called
through Automatic1111's `/sdapi/v1/extra-single-image` endpoint) instead of a
naive Lanczos resize. This matters because the redraw pass runs at a moderate
denoising strength to stay faithful to game geometry - it mostly polishes
whatever the init image already looks like rather than repainting it from
scratch. A Lanczos resize just interpolates the source's *existing* pixels to
a bigger canvas, which bakes in the original 256-color palette's dithering
pattern and low-res softness right into the model's starting point, so the
output stays soft and dithered too. A real upscaler reconstructs plausible
high-frequency detail and cleans up dithering/palette banding instead of
preserving it, giving the diffusion pass a much sharper, cleaner base to work
from - verified on scene 1's "EXTRA" newsstand sign, which stayed crisp and
legible with the ESRGAN init versus turning into "LXTRA" with a Lanczos init.

Counterintuitively, pushing `--steps`/`--cfg-scale`/`--denoising-strength`
higher on top of the ESRGAN init made things *worse* in testing (more
hallucinated detail, further text drift) - the fix is the init image quality,
not the diffusion parameters. Pass `--init-upscaler lanczos` to restore the
old offline-only behavior for `--skip-existing` reruns or A/B comparisons
without a live API. Other installed Automatic1111 upscalers (`SwinIR 4x`,
`DAT x4`, etc. - see `/sdapi/v1/upscalers`) also work.

Now that the init image carries real reconstructed detail instead of
interpolated noise, `--scale` (default `2`) is also worth raising for a
final/hero pass: a scene 1 `--scale 4` run (same steps/cfg/denoise as above)
resolved noticeably sharper stone and wood grain than `--scale 2`, and even
rendered an interior candelabra silhouette through a window that was
completely lost at 2x. Higher scale costs more generation time (wide rooms
split into more overlapping tiles) and disk space, so `--scale 2` remains the
default for full-batch runs, but `--scale 4` is recommended when quality
matters more than turnaround time.

### ControlNet checkpoint choice (contrast/exposure collapse fix)

The photographic-redraw profiles use `controlnet-canny-sdxl-1.0-xinsir-v2`
(from [`xinsir/controlnet-canny-sdxl-1.0`](https://huggingface.co/xinsir/controlnet-canny-sdxl-1.0),
`diffusion_pytorch_model_V2.safetensors`), not the official
`diffusers/controlnet-canny-sdxl-1.0` checkpoint. A subset of scenes generated
with the official checkpoint exhibited a genuine loss of dynamic range/contrast
(not just underexposure): output stddev roughly half the source's and
luminance clipped well short of both black and white, regardless of
denoising/CFG/seed retries. This is a documented community issue with that
checkpoint. The `xinsir` checkpoint is a drop-in replacement (same `canny`
preprocessor workflow) trained on a larger, better-curated dataset and does
not exhibit the collapse - verified across multiple previously-affected scenes
with contact-sheet comparisons against source stddev/luminance range. Download
the file into Forge's `models/ControlNet/` directory (any filename) and update
`controlnet_model` in the relevant `profiles/neural/*.json` and/or
`--controlnet-model` CLI value to match Forge's `/controlnet/model_list`
identifier for it (Forge derives the `[hash]` suffix itself).

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

### Liberal-art masked pass (`--liberal-art`)

`extract_rosetattoo_assets.py` also writes `protect_mask.png` per scene: a
solid-filled (not outline) union of the room's walk zones and hotspot bounds,
white where pathfinding/clickable geometry must stay pixel-faithful and black
everywhere else. `neural_redraw_rosetattoo_backgrounds.py` uses this as
Automatic1111's native inpainting mask for a second pass, run automatically
after the base geometry-preserving redraw (enabled by default; pass
`--no-liberal-art` to disable it and keep only the base pass).

The base redraw stays constrained to the game's original geometry; this
second pass is deliberately allowed a higher `--liberal-art-denoise` (default
`0.4`) so it can invent tasteful extra detail - weathering, broken brick,
reliefs, grime, clutter - purely in the decorative background, while the
protected mask region is left byte-identical (verified on scene 18,
Cleopatra's Needle: mean RGB diff inside the protected region was ~0.05 vs.
~6 in the free region at denoise 0.4). `--liberal-art-margin` (default `12`
native pixels) dilates the protected region before inverting it, so
freeform generation can't creep up to the exact edge of critical geometry,
and `--liberal-art-mask-blur` (default `24`) softens the seam between the two
regions. Scenes without a `protect_mask.png` sidecar (i.e. rooms with no
parsed walk zones or hotspots, or extracted before this feature existed) are
left untouched by this pass. The pre-liberal-art image is kept alongside the
final output as `background_faithful@<scale>x.png` for comparison.

Denoise strength above ~0.5 was found to invent whole new structures (e.g. an
out-of-place iron gate) rather than just texture/detail on scene 18 - keep it
in the 0.35-0.45 range unless a scene specifically calls for a bigger change,
using `--settings-file` per-scene overrides.

For a resumable full-room pass, point `--output-dir` and `--scummvm-overrides`
at stable ignored directories and rerun with `--skip-existing` whenever the
local model runner is interrupted. The batch validator rejects blank captures,
which helps catch early macOS window-capture misses during unattended scene
checks.

### Cursors, items, and character sprites (`extract_rosetattoo_sprites.py` / `upscale_rosetattoo_sprites.py`)

Everything that *moves* in Rose Tattoo (mouse cursors, inventory/interactive
item icons, and character walk-cycle sprites) is stored separately from room
backgrounds, in a proprietary VGS frame format packed inside the game's
`.LIB`/`.LIC` archives (`VGS.LIB`, `WALK.LIB`, `TALK.LIB`). Unlike room
backgrounds (one full 640x480 frame per `.RRM`), each of these resources is a
small sequence of individually-offset frames - e.g. `RMOUSE.VGS` holds all 14
room cursor frames (arrow, magnifying glass, 3-frame wait animation, 8
directional exit arrows, and the "Exit" label cursor), while a character file
like `WATSON.VGS` holds ~95 walk-cycle frames.

```sh
# Extract cursor frames (writes extracted/sprites/<resource>/frame_NNN.png + metadata.json)
python3 tools/extract_rosetattoo_sprites.py --resources RMOUSE.VGS OMOUSE.VGS

# Extract a character walk cycle - these have no palette of their own at
# runtime (they reuse whichever room palette is currently loaded), so borrow
# one from a specific room's export for correct-color output:
python3 tools/extract_rosetattoo_sprites.py --resources WATSON.VGS --palette-scene 1

# Upscale extracted frames via the same non-diffusion ESRGAN-family endpoint
# used for the background pipeline's init-image step (no ControlNet/redraw -
# these are small, silhouette/hotspot-critical elements where hallucinated
# new content would break gameplay recognizability):
python3 tools/upscale_rosetattoo_sprites.py --resources rmouse_vgs omouse_vgs watson_vgs --scale 2
```

Each frame's alpha channel is separated before upscaling (flattened onto a
neutral fill so ESRGAN doesn't invent detail in fully-transparent regions),
then resized and re-thresholded independently, keeping the crisp binary
cutout edges the engine itself always uses (no partial-alpha blending).
Output filenames are scale-qualified (`frame_NNN@Sx.png`), matching the
background pipeline's `background@Nx.png` convention.

An initial ScummVM-side hookup exists for the room cursor set specifically
(`patches/scummvm/rosetattoo-hires-cursor-ai-override.patch`):
`Events::setCursor()` looks for
`$SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES/sprites/rmouse_vgs/frame_NNN@Sx.png`
(NNN = CursorId, which lines up 1:1 with RMOUSE.VGS's own frame order) and,
if found, draws it as a true-color RGBA cursor instead of nearest-neighbor
upscaling the original palettized pixels. Character/item/animated-sprite
runtime overrides are not yet wired into the engine - that would touch every
sprite-draw call site (`people.cpp`, `objects.cpp`) and is a larger,
higher-risk change than the cursor and background override paths.

#### Fonts (`FONT1.VGS`-`FONT8.VGS`)

The in-game bitmap fonts are extracted and upscaled the same way, but through
a dedicated `--mode font` path instead of the ESRGAN endpoint:

```sh
python3 tools/extract_rosetattoo_sprites.py --resources FONT1.VGS FONT2.VGS FONT3.VGS FONT4.VGS FONT5.VGS FONT6.VGS FONT7.VGS FONT8.VGS
python3 tools/upscale_rosetattoo_sprites.py --resources font1_vgs font2_vgs font3_vgs font4_vgs font5_vgs font6_vgs font7_vgs font8_vgs --mode font --scale 4
```

Font glyphs are tiny (often under 10x10px) monochrome stencils - the RGB
channel is always solid black, with the actual glyph shape living entirely in
alpha (the engine recolors glyphs via whichever color is active at draw time
in `Fonts::writeString()`). Running these through a photographic
super-resolution model would blur or hallucinate texture into strokes only
1-2px wide, so `--mode font` instead does a fast, local, no-API Lanczos
resize of just the alpha channel, producing clean anti-aliased glyph edges
(similar to how a hinted/vector font looks when rendered larger) rather than
a blurry photographic upscale. A contact-sheet comparison of `FONT1.VGS`'s
letters at 4x confirmed this: glyphs stayed crisp and fully legible with
smooth diagonal/curved edges, versus the blocky nearest-neighbor look of the
original bitmaps.

As with character/item sprites, this is a tooling-only deliverable for
now - the engine's text layout (`Fonts::writeString()`, dialog/journal/UI
positioning) draws glyphs at their native pixel size and would need every
call site's metrics scaled by `_roseTattooHiresScale` to actually render
these upscaled glyphs in-game. That engine-side wiring is left as future
work; the upscaled glyph assets are ready under `enhanced/sprites/font*_vgs/`
whenever that lands.

#### Hires TrueType tooltip/UI text (`patches/scummvm/rosetattoo-hires-font-ttf-override.patch`)

Rather than upscaling the bitmap glyphs above, the engine now supports
swapping in a real vector font for tooltip/hotspot-name text (e.g. hovering
over "Street Lamp") when running in hires mode. Drop a TTF file at
`$SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES/fonts/hires_font.ttf` and, if
FreeType2 support is compiled in (`USE_FREETYPE2`) and
`_roseTattooHiresScale > 1` with a non-CLUT8 hires format, `Screen` loads it
on demand (cached per pixel size in `_roseTattooHiresFonts`) and renders
crisp anti-aliased text into a persistent alpha-blended overlay
(`_roseTattooHiresTextLayer`) instead of upscaling the native ~10px bitmap
glyphs. This is a much better result for tooltip text than the Lanczos
alpha-channel approach used for in-scene bitmap fonts above, since tooltip
text is drawn fresh every frame rather than being baked into scene art.

This required fixing an intermittent bug where a second, blocky bitmap-font
"ghost" copy of the tooltip text would bleed through alongside the crisp TTF
text. Root cause: `WidgetTooltipBase::draw()` always blitted the tooltip's
native bitmap-rendered text (`screen.SHtransBlitFrom(_surface, ...)`)
directly onto the screen, relying on the hires text layer's
background-restore-then-blend mechanism to immediately paint over it -
which proved unreliable frame-to-frame. The fix is `Screen::usesRoseTattooHiresText()`,
which lets `WidgetTooltipBase::draw()` skip the redundant bitmap blit
entirely whenever hires TTF text will replace it, so there is nothing left
for the restore mechanism to (unreliably) hide. A related fix also stops
`WidgetTooltip::setText()` from queueing phantom hires-text draws via its
own internal `writeFancyString()` sizing calls (added `BaseSurface::clearHiresTextOrigin()`,
used instead of `setHiresTextOrigin()` there) - only the paired `draw()`/`erase()`
calls in `refreshHiresText()` should queue/clear hires text.

#### Additional character/item sprites

Beyond the cursor set and Watson, the same `extract_rosetattoo_sprites.py` /
`upscale_rosetattoo_sprites.py` pipeline has also been run over the rest of
the game's VGS-format character and item resources: the player's own walk
cycles across coat/hat states (`SVGAWALK.VGS`, `NOHAT.VGS`, `COATWALK.VGS`,
and the `CT*`/`HT*`/`JT*`/`TDOWNRG` directional variants), the named NPCs
(`MYCROFT.VGS`, `TOBY.VGS`, `TUX.VGS`, `WIGGINS.VGS`), and every inventory/
interactive item icon (`ITEM01.VGS`-`ITEM84.VGS`). Like Watson, these are
extracted and upscaled but not yet wired into any runtime override path -
same reasoning as above: hooking real hires art into `people.cpp`'s
walk-cycle/animation state machine (as opposed to the single well-defined
`Events::setCursor()` call site the cursor override uses) is a much larger,
higher-risk change than has been attempted so far. `TALK.LIB`'s ~1400
talking-head portrait frames and the per-scene `RES##.VGS` foreground sprite
overlays are not yet extracted at all.

#### Overhead/travel map (`MAP.VGS`)

The London overhead map screen bypasses `Scene::loadScene()` entirely (see
`TattooMap::show()`) and previously only got the black-screen fix from
`patches/scummvm/rosetattoo-hires-map-black-screen-fix.patch` - correct
scaling, but still the original blocky low-resolution artwork, unlike every
room background. `patches/scummvm/rosetattoo-hires-map-upscale-override.patch`
adds a real hires override for it, reusing the same decode/validation logic
as the per-scene background override (factored into a shared
`loadRoseTattooHiresBackgroundFromPath()` helper) with a fixed path instead
of a per-scene one.

Unlike room backgrounds, `MAP.VGS` stores no palette of its own - the engine
loads a separate `MAP.PAL` resource for it - so extraction needs
`--palette-resource` (a new flag alongside `--palette-scene`) instead:

```sh
python3 tools/extract_rosetattoo_sprites.py --resources MAP.VGS --palette-resource MAP.PAL
python3 tools/extract_rosetattoo_sprites.py --resources MAPICONS.VGS --palette-resource MAP.PAL

# The map is a single large (1280x960 native) illustration full of fine
# engraving-style linework and ~30 clickable location pins, so - like
# cursors/items - it gets the same non-diffusion real-ESRGAN upscale, not a
# ControlNet redraw pass that could hallucinate away small geometry:
python3 tools/upscale_rosetattoo_sprites.py --resources map_vgs mapicons_vgs --scale 2
```

The engine looks for the result at
`sprites/map_vgs/frame_000@<scale>x.png` under
`$SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES` (the same directory tree the
cursor override already reads from), so copying `enhanced/sprites/map_vgs/`
into the production mod's `sprites/` directory is enough to pick it up.
`MAPICONS.VGS`'s 33 location-pin frames are upscaled too but not yet wired
into a runtime override (the map only reads the background frame today).

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
