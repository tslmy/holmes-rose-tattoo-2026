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
renderings. This is achieved via a [Stable Diffusion][sdf] pipeline. 

Since geometries (object boundaries, hotspots that respond to mouse events,
walkable regions for playable characters) are important to a point-and-click
RPG, we keep the geometries locked with a "sidecar" neural network called
[ControlNet][ctn]. (See [`docs/reproducing.md`][rpd] for why this doesn't just
hallucinate a new scene.)

[rpd]: docs/reproducing.md#history-and-why-the-pipeline-looks-like-this

For certain assets (cursors and the map of London), we upscale with a
**non-diffusion** super-resolution model. This reduces the risk of
hallucinating away small, gameplay-critical silhouettes.

Besides upscaling/redrawing images, this mod also enhances text. It renders
tooltip/UI text with a real vector TrueType font, instead of the original's
blocky ~10px bitmap glyphs.

Finally, this repo contains patches to ScummVM that, among other things, give
it a true-color compositor that overlays the game's native sprites/UI on top
of the high-resolution background, scaled mouse input, and several
hires-specific rendering fixes (journal, map, tooltip, cursor).

[ctn]: https://arxiv.org/abs/2302.05543
[sdf]: https://en.wikipedia.org/wiki/Stable_Diffusion

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
