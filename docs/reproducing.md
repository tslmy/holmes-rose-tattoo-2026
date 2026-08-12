# Reproducing this pipeline from scratch

End-to-end steps to independently reproduce this project's hires
AI-upscaled ScummVM mod pack, from a stock game install to a playable
patched ScummVM binary. See [`tools/README.md`](../tools/README.md) for
detailed flag-by-flag documentation of each command below, and
[`patches/scummvm/README.md`](../patches/scummvm/README.md) for what each
engine patch does.

## Prerequisites

- A legally-owned copy of *The Lost Files of Sherlock Holmes: The Case of
  the Rose Tattoo* game data, placed under an ignored local directory (e.g.
  `scummvm/`). This repository never tracks original game files or data
  derived from them (see the root README's Safety section).
- Python 3.10+, with [`uv`](https://github.com/astral-sh/uv) (recommended)
  or a plain venv with Pillow installed.
- A ScummVM source checkout for building a patched engine (`git clone
  https://github.com/scummvm/scummvm scummvm-src`).
- A Stable Diffusion WebUI backend (AUTOMATIC1111 or Forge) with the
  ControlNet extension and the models described in
  [`docs/a1111-setup.md`](a1111-setup.md) — required for the neural
  background redraw step, optional if you only want the non-neural Lanczos
  baseline.
- Optional: [Ollama](https://ollama.com) with a vision-capable model (e.g.
  `qwen3.5:9b-mlx`) for LLM-polished prompts. The repo ships pre-generated
  prompt briefs under `profiles/neural/prompt-briefs/`, so this is only
  needed if you want to regenerate them for new/changed scenes.

## Step 1 — Extract game assets

```sh
uv sync
uv run python3 tools/extract_rosetattoo_assets.py --data-dir scummvm
uv run python3 tools/extract_rosetattoo_sprites.py --resources RMOUSE.VGS OMOUSE.VGS
uv run python3 tools/extract_rosetattoo_sprites.py --resources MAP.VGS --palette-resource MAP.PAL
uv run python3 tools/extract_rosetattoo_sprites.py --resources MAPICONS.VGS --palette-resource MAP.PAL
```

Produces `extracted/rosetattoo/scene_NNN/` (backgrounds, prompts, walk
zone/hotspot/protect masks) and `extracted/sprites/<resource>/` (cursor and
map frames).

## Step 2 — Generate neural photoreal backgrounds

Bring up the WebUI backend per [`docs/a1111-setup.md`](a1111-setup.md), then
run the full batch against the tracked production profile:

```sh
uv run python3 tools/neural_redraw_rosetattoo_backgrounds.py \
  --api-url http://127.0.0.1:7860 \
  --wait \
  --settings-file profiles/neural/photographic-faithful.json \
  --scale 2 \
  --skip-existing \
  --output-dir generated/neural-redraws-faithful \
  --scummvm-overrides mods/neural-hires-backgrounds-faithful
```

Omit `--scenes` to process every extracted scene. `--skip-existing` makes
this safely resumable if the backend is interrupted partway through an
80-scene batch (expect several hours on a single consumer GPU). Spot-check
output quality with the contact-sheet review tool
(`tools/generate_rosetattoo_candidates.py`) before trusting a full run.

## Step 3 — Upscale cursors and the map

```sh
uv run python3 tools/upscale_rosetattoo_sprites.py --resources rmouse_vgs omouse_vgs --scale 2
uv run python3 tools/upscale_rosetattoo_sprites.py --resources map_vgs mapicons_vgs --scale 2
```

Copy the results into the same mod directory as step 2's
`--scummvm-overrides` (they read from the same `sprites/` subtree the
background overrides use):

```sh
cp -R enhanced/sprites/rmouse_vgs enhanced/sprites/omouse_vgs enhanced/sprites/map_vgs \
  mods/neural-hires-backgrounds-faithful/sprites/
```

## Step 4 — Build and patch ScummVM

```sh
cd scummvm-src
git apply ../patches/scummvm/rosetattoo-start-scene-env.patch
git apply ../patches/scummvm/rosetattoo-fix-vertical-walk-delta-x.patch
git apply ../patches/scummvm/rosetattoo-hires-mouse-scale.patch
git apply ../patches/scummvm/rosetattoo-hires-cursor-fix.patch
git apply ../patches/scummvm/rosetattoo-hires-cursor-ai-override.patch
git apply ../patches/scummvm/rosetattoo-hires-map-black-screen-fix.patch
git apply ../patches/scummvm/rosetattoo-hires-map-upscale-override.patch
git apply ../patches/scummvm/rosetattoo-hires-font-ttf-override.patch
git apply ../patches/scummvm/rosetattoo-hires-tooltip-text-fix.patch
git apply ../patches/scummvm/rosetattoo-hires-map-icons.patch
git apply ../patches/scummvm/rosetattoo-hires-journal-glitch-fix.patch
./configure --enable-freetype2
make -j$(sysctl -n hw.ncpu 2>/dev/null || nproc)
cd ..
```

`--enable-freetype2` is required for the hires TrueType tooltip-text patch;
`configure` will otherwise silently compile without it and tooltip text
falls back to upscaled bitmap glyphs. Also drop a TTF font file at
`<mod-directory>/fonts/hires_font.ttf` (any legible serif/period-appropriate
font works; this repository doesn't ship one for licensing reasons).

Apply patches in the listed order — several are explicit follow-ups that
touch the same files as an earlier patch (see
[`patches/scummvm/README.md`](../patches/scummvm/README.md) for the full
dependency notes).

## Step 5 — Play

```sh
python3 tools/run_rosetattoo_validation.py \
    --scummvm scummvm-src/scummvm \
    --asset-overrides mods/neural-hires-backgrounds-faithful \
    --hires-scale 2 \
    --hires-format rgba32 \
    --save-dir "$HOME/Library/Application Support/ScummVM/Savegames" \
    --save-slot 1
```

`--hires-scale` must match the `--scale` used in step 2/3. `rgba32` gives
true-color output; use `clut8` to compare against the original
palette-mapped rendering.

## History and why the pipeline looks like this

- **One production profile, not two.** Earlier sessions ran two competing
  photoreal profiles (`photographic-faithful` and `photographic-cinematic`)
  in parallel while debugging a "gray/washed-out" output complaint. That
  turned out to stem from the *ControlNet checkpoint* choice (the official
  `diffusers/controlnet-canny-sdxl-1.0` has a documented contrast-collapse
  issue), not an inherent need for two profiles — once both profiles were
  switched to the `xinsir` ControlNet checkpoint, a single well-tuned
  profile (`photographic-faithful`) produces non-washed-out results in one
  pass. `photographic-cinematic` is kept as an alternate style (warmer,
  non-Turbo checkpoint, more steps) rather than a required second pass.
- **Every scene gets an LLM-generated prompt brief, no manual overrides.**
  An earlier iteration hand-curated verbatim prompt text for a handful of
  fragile scenes while leaving the rest to raw extracted text, which meant
  different scenes silently went through different prompt pipelines. All
  scenes now go through the same `polish_with_ollama()` path with uniform
  sanitization.
- **The liberal-art masked second pass is intentional, not a workaround.**
  It runs automatically after the geometry-preserving base redraw, using
  Automatic1111's native inpainting `mask` parameter against each scene's
  `protect_mask.png`, so decorative embellishment only ever touches pixels
  outside walk zones/hotspots. This is a deliberate two-stage design (base
  redraw + masked decorative pass), distinct from the abandoned
  two-*profile* approach above.
- **Character/animated sprites are extracted and upscaled but not yet
  engine-wired.** The cursor set and the overhead map background/icons have
  full runtime override support; walk-cycle sprites for Watson, the player,
  and NPCs are upscaled and sitting under `enhanced/sprites/`, but wiring
  them into `people.cpp`'s draw call sites is a larger, higher-risk change
  that hasn't been attempted yet (see `tools/README.md`).

## Investigated but not implemented: animation framerate interpolation

An earlier investigation looked at whether animated sprites (character walk
cycles, cursor wait animation, hotspot object animations) could be
interpolated to a higher framerate alongside the visual upscale. Findings
from reading the engine source (`events.cpp`, `objects.cpp`, `people.cpp`,
`sherlock.cpp`):

- The whole game logic loop (`SherlockEngine::sceneLoop()` in
  `sherlock.cpp`) runs on a single fixed tick, gated by
  `Events::checkForNextFrameCounter()`. The tick rate is `GAME_FRAME_RATE`
  (30 in `events.h`), and is already user-toggleable to 2x
  (`Events::toggleSpeed()`) — so the engine has no inherent floor below
  30fps, but nothing above 60fps either without further changes.
- Sprite/animation advance is **not** "always show the next frame every
  tick" — it is driven by a per-object bytecode-like `_sequences` array
  (`objects.cpp`), which encodes frame indices interleaved with explicit
  timing/pause commands, position deltas, sound triggers, and show/hide
  toggles. Frame *indices* referenced by this bytecode are the same indices
  baked into `.VGS` resources, hotspot bounding logic, and save-game state —
  they are not purely cosmetic.
- This means genuine motion interpolation (inserting new "in-between"
  sprite images, e.g. via optical flow tools like RIFE/FILM) would require
  either (a) doubling every `_sequences` bytecode program to reference new
  interpolated frame indices — risky, since these are hand-authored per
  animation and would need to be mechanically rewritten per resource without
  breaking embedded pause/sound/position commands — or (b) decoupling
  *rendering* rate from *logic* rate: keep the 30fps logic tick (so hotspot
  state, sound cues, and walk-path decisions are unchanged) but render an
  extra blended frame between every real logic tick for perceived
  smoothness.

**Recommended approach if this is picked up later** is (b), a purely
presentational cross-fade rather than true motion interpolation:

1. Keep the 30fps logic tick untouched (no `_sequences`/hotspot/save
   changes).
2. In the render path only, when between two logic ticks, alpha-blend the
   previously-drawn sprite frame and the next upcoming one (weighted by how
   far the renderer is between the two logic ticks) to produce one extra
   visual frame at 60fps output.
3. This needs no offline art generation and no changes to game data/saves,
   but only smooths animations that hold a frame for multiple render frames
   (i.e. anywhere the render loop already runs faster than the logic tick);
   it will not add genuinely new in-between poses to a walk cycle.

A higher-quality (but substantially larger) alternative would be an offline
per-resource pass with a model such as RIFE or FILM to synthesize real
in-between frames for each `.VGS` walk-cycle resource, then renumbering and
re-injecting them into the `_sequences` bytecode for that resource — this
was intentionally not attempted given its scope (it touches
game-logic-adjacent data, not just visuals, and each of the ~15+
character/object animation resources would need individual verification
that hotspot/interaction timing wasn't shifted).
