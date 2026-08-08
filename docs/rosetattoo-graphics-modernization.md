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

## Current Limits

The extractor currently targets the highest-value first pass: base room
backgrounds and prompt text. It does not yet export foreground object sprites,
character walk cycles, cutscene animation frames, masks, or rebuilt room files.
