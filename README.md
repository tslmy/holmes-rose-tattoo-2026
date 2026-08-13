# High-resolution mod for [_The Lost Files of Sherlock Holmes: The Case of the Rose Tattoo_][crt]

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

To celebrate the 30th anniversary of this brilliant DOS game, I decided to
enhance the visuals of it, by replacing the original art assets with AI-
enhanced images. (Love it or hate it, AI does make such a comprehensive
overhaul a one-person job.)

[crt]: https://en.wikipedia.org/wiki/The_Lost_Files_of_Sherlock_Holmes:_The_Case_of_the_Rose_Tattoo

## What

I can't ship the asset pack itself, as it may be considered [derivative work]
[drv] under copyright law. Instead, this git repo ships scripts that
**generate** the mod. Those scripts:

[drv]: https://en.wikipedia.org/wiki/Derivative_work

1. extract its original 1996 backgrounds, sprites, cursors, and fonts,
2. upscale/redraw them into higher resolution, and
3. patch [ScummVM][svm] to render them in-game at full resolution,

without touching walkable geometry, hotspots, or puzzle-relevant object
placement. The gameplay is kept intact.

> [!NOTE]
> Yes, this project is based on **[ScummVM][svm], not the original binaries**.
If you haven't heard, _ScummVM_ is a collection of complete rewrites of old
game engines, so that they can run natively on modern devices. To play a game
like _Rose Tattoo_, just bring your legally-obtained copy of the game assets.
_Consider donating to them this holiday season._

[svm]: https://www.scummvm.org/

### More technically...

The original game renders at 640x480 with 256-color palettized art. We redraw
the original assets into detailed, true-color, photorealistic-but-faithful
renderings. This is achieved via a [FLUX.1][flux] image-to-image pipeline,
run locally with Hugging Face `diffusers` on Apple's Metal (`mps`) backend.

Since geometries (object boundaries, hotspots that respond to mouse events,
walkable regions for playable characters) are important to a point-and-click
RPG, the redraw uses a low img2img denoise strength against the original
background as the init image, which keeps composition, walk zones, and small
embedded text (signage, door numbers) intact rather than hallucinating a new
scene. (See [`docs/reproducing.md`][rpd] for the calibration history behind
that choice.)

[rpd]: docs/reproducing.md#history-and-why-the-pipeline-looks-like-this

For certain assets (cursors and the map of London), we upscale with a
**non-diffusion** super-resolution model. This reduces the risk of
hallucinating away small, gameplay-critical silhouettes.

Besides upscaling/redrawing images, this mod also enhances text. It renders
tooltip/UI text with a real vector TrueType font, instead of the original's
blocky ~10px bitmap glyphs.

Finally, this repo tracks a ScummVM fork (via a Git submodule) that, among
other things, gives it a true-color compositor that overlays the game's
native sprites/UI on top of the high-resolution background, scaled mouse
input, and several hires-specific rendering fixes (journal, map, tooltip,
cursor).

[flux]: https://blackforestlabs.ai/announcing-black-forest-labs/

## Setup

Python tooling is managed with [`uv`](https://github.com/astral-sh/uv). Install
uv (`brew install uv`), then sync the project's virtual environment:

```sh
uv sync
```

Run any tool via `uv run`, e.g.:

```sh
uv run python3 tools/extract_rosetattoo_assets.py --data-dir scummvm --scenes 1 2 18
```

## Usage

Follow these steps:

1. **[Reproduce the full pipeline from scratch](docs/reproducing.md)** —
   the complete, ordered, end-to-end guide: extract assets, generate
   photoreal backgrounds, upscale sprites, patch and build ScummVM, and
   play. Start here.
2. **[Set up the FLUX.1 + diffusers pipeline](docs/flux-setup.md)** —
   model downloads (GGUF-quantized transformer + text encoders/VAE), one-time
   `uv sync --extra flux` setup, and calibration notes. Needed before step 1's
   neural redraw stage.
3. **[Tools reference](tools/README.md)** — flag-by-flag documentation for
   every script under `tools/`: extraction, upscaling, neural redraw,
   candidate review, sprite/font/map handling, and in-game validation.
4. **[ScummVM Strategy](#scummvm-strategy)** (below) — how the patched
   engine is tracked as a Git submodule and how to build it.

## ScummVM Strategy

This repo treats ScummVM as an external dependency, built from a personal
fork tracked as a Git submodule at `scummvm-src/`:

- Fork: <https://github.com/tslmy/scummvm>
- Branch: `rosetattoo-hires-mod` (based on upstream ScummVM's `master`)

Engine development has grown past a handful of one-off changes into a
substantial set of hires-rendering features/fixes (true-color compositor,
scaled mouse input, AI-upscaled sprite/cursor/map overrides, hires
TrueType tooltip text, and various occlusion/ghosting bugfixes) touching a
shared set of core files (`screen.cpp`/`.h` in particular). Keeping those
as real commits on the fork branch - rather than a pile of hand-maintained
`.patch` files - means every change gets normal Git history, is trivially
buildable, and stays rebase/merge-friendly as upstream ScummVM evolves.

Clone this repo with `--recurse-submodules`, or initialize the submodule
afterward:

```sh
git submodule update --init --recursive
```

Then build it:

```sh
cd scummvm-src && ./configure && make -j$(nproc)
```

`tools/run_rosetattoo_validation.py` and friends just point at the built
`scummvm-src/scummvm` binary via `--scummvm`.

## Safety

Do not commit:

- original `RES*.RRM`, `SPEECH*.LIB`, `TALK.LIB`, `WALK.LIB`, or DOS install
  files
- extracted room PNGs or prompt files generated from copyrighted game data
- FLUX-redrawn outputs derived from the game assets
