# Reproducing this pipeline from scratch

End-to-end steps to independently reproduce this project's hires
AI-upscaled ScummVM mod pack, from a stock game install to a playable
patched ScummVM binary. See [`tools/README.md`](../tools/README.md) for
detailed flag-by-flag documentation of each command below, and the root
[README's ScummVM Strategy section](../README.md#scummvm-strategy) for how
the patched engine is tracked as a Git submodule.

## Prerequisites

- A legally-owned copy of *The Lost Files of Sherlock Holmes: The Case of
  the Rose Tattoo* game data, placed under an ignored local directory (e.g.
  `scummvm/`). This repository never tracks original game files or data
  derived from them (see the root README's Safety section).
- Python 3.10+, with [`uv`](https://github.com/astral-sh/uv) (recommended)
  or a plain venv with Pillow installed.
- The `scummvm-src/` Git submodule, initialized (`git submodule update
  --init --recursive`), for building the patched engine.
- The FLUX.1 + diffusers pipeline set up per
  [`docs/flux-setup.md`](flux-setup.md) — required for the neural
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

Set up the FLUX.1 + diffusers pipeline per
[`docs/flux-setup.md`](flux-setup.md) (one-time `uv sync --extra flux` plus
model downloads), then run the full batch:

```sh
uv run python3 tools/flux_redraw_rosetattoo_backgrounds.py \
  --scale 2 \
  --steps 12 \
  --strength 0.25 \
  --skip-existing \
  --output-dir generated/flux-redraws \
  --scummvm-overrides mods/flux-hires-backgrounds
```

Omit `--scenes` to process every extracted scene. `--skip-existing` makes
this safely resumable if interrupted partway through an 80-scene batch
(expect several hours on Apple Silicon). Spot-check output quality with the
generated contact sheet (`validation/contact-sheets/flux-redraws.jpg`)
before trusting a full run.

## Step 3 — Upscale cursors and the map

```sh
uv run python3 tools/upscale_rosetattoo_sprites.py --resources rmouse_vgs omouse_vgs --scale 2
uv run python3 tools/upscale_rosetattoo_sprites.py --resources map_vgs mapicons_vgs --scale 2
```

Also upscale the character/NPC walk-cycle sprites the ScummVM fork's
character/object hires-sprite override support (Step 4) looks for at
runtime - both the player's own coat/hat states and the generic body-type
sprite sheets Rose Tattoo reuses across many different one-off NPCs (see
[`tools/README.md`](../tools/README.md#cursors-items-character-and-font-sprites)
for the full resource list and why extracting these generic sheets is
higher-leverage than named characters):

```sh
uv run python3 tools/extract_rosetattoo_sprites.py --palette-scene 1 --resources \
  WATSON.VGS WIGGINS.VGS MYCROFT.VGS TOBY.VGS TUX.VGS \
  SVGAWALK.VGS NOHAT.VGS COATWALK.VGS \
  CTDOWNRI.VGS CTRIGHT.VGS CTUPRIGH.VGS HTDOWNRG.VGS HTRIGHT.VGS HTUPRIGH.VGS \
  JTDOWNRG.VGS JTRIGHT.VGS JTUPRIGH.VGS TDOWNRG.VGS TRIGHT.VGS TUPRIGHT.VGS \
  3TDOWNRG.VGS 3TRIGHT.VGS 3TUPRIGH.VGS GREEN.VGS \
  GTDOWNRG.VGS GTRIGHT.VGS GTSDOWNR.VGS GTSRIGHT.VGS GTSUPRIG.VGS GTUPRIGH.VGS \
  ITDOWNRG.VGS ITRIGHT.VGS ITUPRIGH.VGS QTDOWNRG.VGS QTRIGHT.VGS QTUPRIG.VGS \
  TWDOWNRG.VGS TWRIGHT.VGS TWUPRIGH.VGS
uv run python3 tools/upscale_rosetattoo_sprites.py --scale 2 --resources \
  watson_vgs wiggins_vgs mycroft_vgs toby_vgs tux_vgs \
  svgawalk_vgs nohat_vgs coatwalk_vgs \
  ctdownri_vgs ctright_vgs ctuprigh_vgs htdownrg_vgs htright_vgs htuprigh_vgs \
  jtdownrg_vgs jtright_vgs jtuprigh_vgs tdownrg_vgs tright_vgs tupright_vgs \
  3tdownrg_vgs 3tright_vgs 3tuprigh_vgs green_vgs \
  gtdownrg_vgs gtright_vgs gtsdownr_vgs gtsright_vgs gtsuprig_vgs gtuprigh_vgs \
  itdownrg_vgs itright_vgs ituprigh_vgs qtdownrg_vgs qtright_vgs qtuprig_vgs \
  twdownrg_vgs twright_vgs twuprigh_vgs
uv run python3 tools/upscale_rosetattoo_sprites.py --resources item01_vgs item02_vgs ... item84_vgs --scale 2
```

Copy the results into the same mod directory as step 2's
`--scummvm-overrides` (they read from the same `sprites/` subtree the
background overrides use):

```sh
cp -R enhanced/sprites/*_vgs mods/flux-hires-backgrounds/sprites/
```

## Step 4 — Build ScummVM

The patched engine lives at `scummvm-src/`, tracked as a Git submodule
pointing at the `rosetattoo-hires-mod` branch of a personal fork
(<https://github.com/tslmy/scummvm>) - see the root
[README's ScummVM Strategy section](../README.md#scummvm-strategy) for why.

```sh
git submodule update --init --recursive
cd scummvm-src
./configure --enable-freetype2
make -j$(sysctl -n hw.ncpu 2>/dev/null || nproc)
cd ..
```

`--enable-freetype2` is required for the hires TrueType tooltip-text
feature either way; `configure` will otherwise silently compile without it
and tooltip text falls back to upscaled bitmap glyphs. Also drop a TTF font
file at `<mod-directory>/fonts/hires_font.ttf` (any legible
serif/period-appropriate font works; this repository doesn't ship one for
licensing reasons).

## Step 5 — Play

```sh
python3 tools/run_rosetattoo_validation.py \
    --scummvm scummvm-src/scummvm \
    --asset-overrides mods/flux-hires-backgrounds \
    --hires-scale 2 \
    --hires-format rgba32 \
    --save-dir "$HOME/Library/Application Support/ScummVM/Savegames" \
    --save-slot 1
```

`--hires-scale` must match the `--scale` used in step 2/3. `rgba32` gives
true-color output; use `clut8` to compare against the original
palette-mapped rendering.

## History and why the pipeline looks like this

- **FLUX.1 + diffusers replaced the earlier Stable Diffusion/Automatic1111
  pipeline.** An SDXL + ControlNet + Automatic1111-API pipeline was the
  original approach, but FLUX.1-schnell (run locally via `diffusers` on
  Apple's `mps` backend) produces comparable or better photorealism without
  needing a WebUI server, a ControlNet checkpoint, or a two-pass
  base-redraw-plus-masked-decorative-pass design to hold structure — a
  single low-strength img2img pass against the original background as the
  init image is enough to keep geometry, walk zones, and small embedded
  text (signage, door numbers) intact. See
  [`tools/flux_redraw_rosetattoo_backgrounds.py`](../tools/flux_redraw_rosetattoo_backgrounds.py)
  and [`docs/flux-setup.md`](flux-setup.md) for the current pipeline, and
  its module docstring/`tools/README.md` for the strength/steps calibration
  that was found by visual inspection (`--steps 12 --strength 0.25`, the
  point past which higher strengths reliably garble small text and start
  inventing extra people/props).
- **Every scene gets an LLM-generated prompt brief, no manual overrides.**
  An earlier iteration hand-curated verbatim prompt text for a handful of
  fragile scenes while leaving the rest to raw extracted text, which meant
  different scenes silently went through different prompt pipelines. All
  scenes now go through the same `polish_with_ollama()` path with uniform
  sanitization, and the resulting briefs are backend-agnostic (used by the
  FLUX pipeline the same way they were used by the earlier SD pipeline).
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
