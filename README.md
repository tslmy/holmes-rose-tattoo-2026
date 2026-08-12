# Rose Tattoo Modernization Lab

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Tools and ScummVM patches for modernizing the graphics of *The Lost Files of
Sherlock Holmes: The Case of the Rose Tattoo* — extracting its original 1996
room backgrounds, sprites, cursors, and fonts; upscaling and neural-redrawing
them into photorealistic high-resolution art; and patching a local ScummVM
build to render them in-game at full resolution, without touching walkable
geometry, hotspots, or puzzle-relevant object placement.

This repository intentionally does not track original game files, extracted
backgrounds, generated art, or a local ScummVM checkout. Keep those assets
local under ignored directories such as `scummvm/`, `extracted/`, `enhanced/`,
`generated/`, `mods/`, and `scummvm-src/`.

## What and why

The original game renders at 640x480 with 256-color palettized art. This
project keeps the original engine logic, puzzle design, and walkable/hotspot
geometry completely intact, but:

- redraws room backgrounds through a ControlNet-guided Stable Diffusion
  pipeline into detailed, true-color, photorealistic-but-faithful art
  (geometry-locked to the original composition — see
  [`docs/reproducing.md`](docs/reproducing.md#history-and-why-the-pipeline-looks-like-this)
  for why this doesn't just hallucinate a new scene);
- upscales cursors and the overhead travel map with a non-diffusion
  super-resolution model (no risk of hallucinating away small,
  gameplay-critical silhouettes);
- renders tooltip/UI text with a real vector TrueType font instead of the
  original's blocky ~10px bitmap glyphs; and
- patches a local ScummVM build with a true-color compositor that overlays
  the game's native sprites/UI on top of the high-resolution background,
  scaled mouse input, and several hires-specific rendering fixes (journal,
  map, tooltip, cursor).

Before/after screenshots aren't checked into this repository, since both the
original game screenshots and the neural-redrawn output are derived from
copyrighted game assets (see [Safety](#safety) below). Generate your own
comparison locally with `tools/generate_rosetattoo_candidates.py`, which
produces a side-by-side `review_sheet.jpg` per scene.

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

Every command in the docs below also works with a plain `python3` as long as
Pillow is installed in the active interpreter (`uv run` is just the
recommended, reproducible way to get there).

## Steps to follow

1. **[Reproduce the full pipeline from scratch](docs/reproducing.md)** —
   the complete, ordered, end-to-end guide: extract assets, generate
   photoreal backgrounds, upscale sprites, patch and build ScummVM, and
   play. Start here.
2. **[Set up the Stable Diffusion WebUI backend](docs/a1111-setup.md)** —
   Automatic1111/Forge install, ControlNet extension, model downloads, and
   known API quirks. Needed before step 1's neural redraw stage.
3. **[Tools reference](tools/README.md)** — flag-by-flag documentation for
   every script under `tools/`: extraction, upscaling, neural redraw,
   candidate review, sprite/font/map handling, and in-game validation.
4. **[ScummVM patches reference](patches/scummvm/README.md)** — what each
   patch under `patches/scummvm/` does, the order to apply them in, and the
   house style to follow when writing a new one.

## ScummVM Strategy

The clean default is to keep this as a game-modernization/modding repository
and treat ScummVM as an external dependency:

- Use the installed ScummVM runtime for playing the original game.
- Use a local `scummvm-src/` checkout for reference or experiments.
- Keep repeatable engine changes as patches under `patches/scummvm/`.
- Add ScummVM as a Git submodule only when this repo needs to build a patched
  engine as part of its normal workflow.

If external high-resolution background overrides need more than a patch set,
that can grow into either a larger patch/script collection kept here, or a
real ScummVM fork/submodule once engine development becomes central.

## Safety

Do not commit:

- original `RES*.RRM`, `SPEECH*.LIB`, `TALK.LIB`, `WALK.LIB`, or DOS install
  files
- extracted room PNGs or prompt files generated from copyrighted game data
- Stable Diffusion outputs derived from the game assets
