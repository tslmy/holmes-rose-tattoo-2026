# Copilot instructions for Rose Tattoo Modernization Lab

## Project shape

This repository is a tooling workspace for modernizing and validating graphics for *The Lost Files of Sherlock Holmes: The Case of the Rose Tattoo*. The tracked code centers on a Python pipeline under `tools/` that:

1. extracts room backgrounds and prompt metadata from local game data,
2. upscales or redraws those backgrounds,
3. builds ScummVM-ready override packs, and
4. runs validation captures against a local ScummVM build.

Treat `scummvm-src/`, `scummvm/`, `extracted/`, `enhanced/`, `generated/`, `mods/`, and `validation/` as workspace/output areas rather than source-of-truth code.

## Commands

Run the Python tools directly with `python3`.

Extract assets:

```sh
python3 tools/extract_rosetattoo_assets.py --data-dir scummvm --scenes 1 2 18
python3 tools/extract_rosetattoo_assets.py --data-dir scummvm
```

Upscale extracted backgrounds:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --input-dir extracted/rosetattoo \
  --output-dir enhanced/rosetattoo \
  --scale 4 \
  --method lanczos
```

Build a playable high-resolution pack:

```sh
python3 tools/build_playable_rosetattoo_hires.py
```

Validate scenes:

```sh
python3 tools/run_rosetattoo_validation.py --save-slot 1 --scenes 1 2 18
python3 tools/batch_validate_rosetattoo.py \
  --scummvm scummvm-src/scummvm \
  --asset-overrides generated/overrides-2x \
  --hires-scale 2 \
  --hires-format rgba32 \
  --scenes 1 2 18 36 \
  --capture-after 4
```

Generate candidate sets or neural redraws:

```sh
python3 tools/generate_rosetattoo_candidates.py --input-dir extracted/rosetattoo --output-dir generated/candidates --scenes 1 18 36 --scale 2
python3 tools/neural_redraw_rosetattoo_backgrounds.py --api-url http://127.0.0.1:7860 --wait --scenes 18 --scale 2
python3 tools/polish_rosetattoo_prompts.py --provider ollama --ollama-url http://127.0.0.1:11434 --ollama-model qwen3.5:9b-mlx --ollama-api generate
```

There is no project-wide package manager or build system checked in here; use the scripts themselves as the primary entry points.

## High-level architecture

`tools/extract_rosetattoo_assets.py` reads Rose Tattoo room resources and writes per-scene folders with the raw background plus sidecars such as metadata and prompt text. The rest of the pipeline consumes those scene folders.

`tools/upscale_rosetattoo_backgrounds.py` is the core enhancer. It can use Pillow resamplers (`nearest`, `bilinear`, `bicubic`, `lanczos`) or an external command template, then writes enhanced outputs, report data, review sheets, and optional ScummVM override trees.

`tools/build_playable_rosetattoo_hires.py` orchestrates the full pipeline: extract if needed, upscale, optionally validate, and emit a manifest for a playable mod pack. Its default runtime format is `rgba32`.

`tools/run_rosetattoo_validation.py` launches ScummVM with Rose Tattoo-specific paths and optional environment variables for local validation patches. `tools/batch_validate_rosetattoo.py` wraps that launcher for multiple scenes and produces a report plus contact sheet.

`tools/generate_rosetattoo_candidates.py`, `tools/neural_redraw_rosetattoo_backgrounds.py`, and `tools/polish_rosetattoo_prompts.py` are review and generation helpers for candidate comparisons, API-driven redraws, and prompt brief generation.

## Key conventions

- Use `scene_###` directories for generated scene assets and matching `background.png`, `metadata.json`, and `prompt.txt` sidecars.
- Keep generated artifacts in ignored output trees; do not commit extracted game data or derived art.
- Prefer command-line flags already used by the scripts over introducing new workflow conventions.
- Validation tooling assumes ScummVM scene jumping and capture flows on macOS, with `window` capture as the default and `screen` capture only when needed.
- High-resolution validation and override generation use the local patch conventions exposed as `SCUMMVM_SHERLOCK_TATTOO_*` environment variables.
- When adjusting enhancement logic, preserve the output/report shapes written by the existing scripts so downstream validation and manifests continue to work.
- The repo’s ScummVM strategy is to keep the engine external and store only repeatable patches/scripts here unless that changes the workflow.
