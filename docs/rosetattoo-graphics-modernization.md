# Rose Tattoo Graphics Modernization

This project starts with non-destructive extraction. Keep the original
`scummvm/` game data playable and write enhanced assets beside it.

## Extract Backgrounds And Prompt Metadata

```sh
python3 tools/extract_rosetattoo_assets.py --scenes 1 2 18
```

Outputs are written under `extracted/rosetattoo/scene_XX/`:

- `background.png`: the raw room backdrop decoded through the room palette
- `metadata.json`: room header, object records, image chunk names, and text
- `prompt.txt`: a Stable Diffusion prompt scaffold with pinned scene details

Run all scenes with:

```sh
python3 tools/extract_rosetattoo_assets.py
```

## Recommended Enhancement Loop

Use `background.png` as the image-to-image input and `prompt.txt` as the
positive prompt foundation. Keep denoise low enough that puzzle objects and
exit geometry stay in place. Enhanced outputs should go beside the originals,
for example:

```text
extracted/rosetattoo/scene_01/enhanced.png
```

The first engine-side prototype should load external enhanced backgrounds from
a mod directory rather than repacking `.RRM` files. Repacking would force the
art back into the original 8-bit resource constraints and risks damaging the
game data.

## Baseline Upscaling

Create deterministic 4x baseline outputs with Pillow:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --input-dir extracted/rosetattoo \
  --output-dir enhanced/rosetattoo \
  --scale 4 \
  --method lanczos \
  --scenes 1 18
```

Run all extracted scenes:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py --scale 4 --method lanczos
```

The runner writes:

- `background_4x_lanczos.png` per scene
- `report.json` with dimensions, blank-image checks, and source-delta metrics
- `contact_sheet.jpg` for quick visual QA

For model-based enhancement, use the external command hook:

```sh
python3 tools/upscale_rosetattoo_backgrounds.py \
  --method external \
  --scale 4 \
  --external-command 'my-enhancer --input {input} --prompt-file {prompt} --output {output} --scale {scale}'
```

The placeholders `{input}`, `{prompt}`, and `{output}` are shell-quoted by the
runner. The command must create the output image path.

## In-Game Validation

ScummVM already exposes Sherlock debugger commands that can jump scenes:

```text
scene <room>
showall
```

The command is registered in ScummVM's Sherlock debugger implementation. Open
the ScummVM debugger in-game with `Ctrl+Alt+D` on current ScummVM builds, then
enter a command such as:

```text
scene 18
```

This lets us jump to representative rooms and visually compare original versus
enhanced-background engine builds. `showall` exposes all map locations, which is
useful when validating scene availability and navigation.

## Current Limits

The extractor currently targets the highest-value first pass: base room
backgrounds and prompt text. It does not yet export foreground object sprites,
character walk cycles, cutscene animation frames, masks, or rebuilt room files.
