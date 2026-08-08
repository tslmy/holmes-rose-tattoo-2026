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
contains derived game artwork and extracted game text.

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

## ScummVM Strategy

The clean default is to keep this as a game-modernization/modding repository and
treat ScummVM as an external dependency:

- Use the installed ScummVM runtime for playing the original game.
- Use a local `scummvm-src/` checkout for reference or experiments.
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
