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
- `comparison_sheet.jpg` with original/enhanced pairs

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

Use the local validation helper to prepare paths and launch ScummVM when an
executable is available:

```sh
python3 tools/run_rosetattoo_validation.py --scenes 1 2 18
```

If ScummVM is not on `PATH`, pass it explicitly:

```sh
python3 tools/run_rosetattoo_validation.py --scummvm /path/to/scummvm
```

The helper stores screenshots under `validation/screenshots/`, which is ignored
by the repository.

For automated validation, use a patched ScummVM binary instead of keyboard
automation. Apply `patches/scummvm/rosetattoo-start-scene-env.patch` to a local
ScummVM checkout, build it, then launch:

```sh
python3 tools/run_rosetattoo_validation.py \
  --scummvm scummvm-src/scummvm \
  --start-scene 36 \
  --asset-overrides generated/overrides \
  --capture-after 3 \
  --capture-output validation/screenshots/window-scene-036.png
```

The helper sets `SCUMMVM_SHERLOCK_TATTOO_START_SCENE`, which the patch reads
during Rose Tattoo initialization. This skips the prologue state and starts at
the requested room before ScummVM's main scene loop begins.
It also sets `SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES` when `--asset-overrides`
is passed. Background overrides are loaded from
`<override-dir>/scene_036/background.png`, remapped to the room palette, and must
currently match the room's native width and height.
On macOS, `--capture-after` captures only the ScummVM window by default via
`screencapture -l`, keeping validation images independent of whatever else is
on screen.

## Current Limits

The extractor currently targets the highest-value first pass: base room
backgrounds and prompt text. It does not yet export foreground object sprites,
character walk cycles, cutscene animation frames, masks, or rebuilt room files.

## Framerate Interpolation Feasibility

Investigated whether animated sprites (character walk cycles, cursor wait
animation, hotspot object animations) could be interpolated to a higher
framerate alongside the visual upscale. Findings from reading the engine
source (`events.cpp`, `objects.cpp`, `people.cpp`, `sherlock.cpp`):

- The whole game logic loop (`SherlockEngine::sceneLoop()` in `sherlock.cpp`)
  runs on a single fixed tick, gated by `Events::checkForNextFrameCounter()`.
  The tick rate is `GAME_FRAME_RATE` (30 in `events.h`), and is already
  user-toggleable to 2x (`Events::toggleSpeed()`) - so the engine has no
  inherent floor below 30fps, but nothing above 60fps either without further
  changes.
- Sprite/animation advance is **not** "always show the next frame every
  tick" - it is driven by a per-object bytecode-like `_sequences` array
  (`objects.cpp`), which encodes frame indices interleaved with explicit
  timing/pause commands, position deltas, sound triggers, and
  show/hide toggles. Frame *indices* referenced by this bytecode are the
  same indices baked into `.VGS` resources, hotspot bounding logic, and
  save-game state - they are not purely cosmetic.
- This means genuine motion interpolation (inserting new "in-between" sprite
  images, e.g. via optical flow tools like RIFE/FILM) would require either
  (a) doubling every `_sequences` bytecode program to reference new
  interpolated frame indices - risky, since these are hand-authored per
  animation and would need to be mechanically rewritten per resource without
  breaking embedded pause/sound/position commands - or (b) decoupling
  *rendering* rate from *logic* rate: keep the 30fps logic tick (so hotspot
  state, sound cues, and walk-path decisions are unchanged) but render an
  extra blended frame between every real logic tick for perceived smoothness.

### Recommended approach (not yet implemented)

Given the size of a full interpolation pipeline, the pragmatic path is (b):
a purely presentational cross-fade, not true motion interpolation:

1. Keep the 30fps logic tick untouched (no `_sequences`/hotspot/save changes).
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
re-injecting them into the `_sequences` bytecode for that resource -
this was intentionally not attempted in this session given its scope (it
touches game-logic-adjacent data, not just visuals, and each of the ~15+
character/object animation resources would need individual verification
that hotspot/interaction timing wasn't shifted).
