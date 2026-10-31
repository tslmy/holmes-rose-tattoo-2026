# Scene-sprite remaster workflow

This is the non-diffusion workflow for the room foregrounds and scripted
animations, in addition to the pre-existing background and shared-sprite
pipeline. Generated game-derived assets belong in ignored local directories;
do not commit or distribute them without the appropriate rights.

## Extraction

Install the local ESRGAN dependencies and model as described in
[`esrgan-setup.md`](esrgan-setup.md). The following commands create new output
trees; choose unused names to preserve existing work:

```sh
python tools/extract_rosetattoo_room_sprites.py --data-dir scummvm \
  --output-dir extracted/room-sprites-new
python tools/extract_rosetattoo_canimations.py --data-dir scummvm \
  --output-dir extracted/canimations-new
```

The first tool exports room image chunks, keyed by scene and one-based room
image slot. The second exports streamed cAnims, keyed by scene and zero-based
cAnim index. Frame indices are zero-based in both. On the local PC game copy,
the extraction found 80 rooms, 2,175 room-image resources (32,697 frames),
and 767 nonempty cAnim streams (33,189 frames). Counts are an observation of
that copy, not a substitute for checking another installation's output.

Room and cAnim RGB values use the room's **initial** palette. Runtime palette
changes, fades, and other effects are not baked into the PNGs and require
separate runtime handling or validation.

After both extraction runs finish, verify every decoded frame against its RRM
stream (this can take time):

```sh
python tools/audit_rosetattoo_extraction.py \
  --room-source extracted/room-sprites-new \
  --canim-source extracted/canimations-new \
  --report validation/extraction-audit.json
```

## Upscale

Run the local model on each extracted tree. `--skip-existing` safely resumes
a matching interrupted run; changing the model, scale or alpha policy requires
a new output directory. Soft alpha requires a compatible runtime compositor.

```sh
python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/room-sprites-new \
  --output-dir enhanced/room-sprites-new \
  --scale 2 --alpha-mode soft --device mps --skip-existing
python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/canimations-new \
  --output-dir enhanced/canimations-new \
  --scale 2 --alpha-mode soft --device mps --skip-existing
```

First extract the shared resources described in the
[`tools reference`](../tools/README.md#cursors-items-character-and-font-sprites)
to `extracted/sprites`. Prepare a new source tree that repairs known missing
palettes and the map's opaque pixels, then upscale it:

```sh
python tools/prepare_rosetattoo_shared_sprites.py \
  --source-dir extracted/sprites --output-dir extracted/shared-sprites-new
python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/shared-sprites-new \
  --output-dir enhanced/shared-sprites-new \
  --scale 2 --mode auto --alpha-mode soft --device mps \
  --tile-size 256 --tile-pad 16 --skip-existing
```

The preparation step uses `DARTBD.PAL` for darts/hand resources and
`JOURNAL.PAL` for the journal. `OPIUM.VGS` uses room 53's palette; `COAT3.VGS`,
paper, interface and loading assets use room 1's palette. These room palettes
are snapshots, not a guarantee of matching every runtime palette. New
extraction metadata records the supplied fallback palette.

Auto mode selects alpha-only bitmap-font resizing; tiling limits GPU memory
for large frames such as the map. This does not imply that every shared
resource already has an engine-side override consumer.

## Assemble and audit

When every selected run has completed, assemble a *new* pack. Existing packs
are never overwritten by the assembler:

```sh
python tools/assemble_rosetattoo_modernized.py \
  --background-dir mods/esrgan-hires-backgrounds \
  --sprite-dir enhanced/shared-sprites-new \
  --sprite-dir enhanced/room-sprites-new \
  --sprite-dir enhanced/canimations-new \
  --output-dir mods/modernized-new --scale 2
python tools/audit_rosetattoo_pack.py --pack mods/modernized-new \
  --sprite-source extracted/shared-sprites-new \
  --sprite-source extracted/room-sprites-new \
  --sprite-source extracted/canimations-new \
  --report validation/modernized-new-audit.json
```

The audit compares file presence, format and dimensions with the supplied
extraction manifests. It does **not** prove that the extraction covers all
game resources or that the engine consumes each asset correctly. Validate in
game: include moving characters, foreground occlusion, scripted animations,
scrolling rooms, palette transitions, UI, inventory, map and save/load.

The existing temporal pass can stabilize high-frequency residuals across
similar frames, but arbitrary room image frame indices are not necessarily
playback order. Treat its output as experimental until tested against actual
animations; it does not change frame timing or game logic.
It accepts nested resources and `--skip-existing`; completed sequences are
reused only when their input files and temporal settings still match. Soft
alpha is preserved. Its output manifests can be used as sprite inputs to the
assembler once the entire selected run is complete.

## Local experiment notes

On the local M2 Max, a test of 95 Watson frames took 11.38 seconds on MPS;
20 character frames took 4.24 seconds on MPS versus 25.08 seconds on CPU.
Times vary with frame dimensions, other GPU work and model warm-up. An
untiled 1280x960 map exceeded the available MPS allocation; overlapping tiles
completed a map test in 11.56 seconds. A separate three-frame scene-26 lamp
test compared 128-pixel tiles with untiled inference: mean RGB difference on
visible pixels was approximately 0.13/255, with identical alpha masks. These
are spot checks, not guarantees against seams or animation flicker.

An initial full extraction audit decoded the source streams again and
matched all extracted frames for all 80 local PC room files. The audit checks
asset fidelity to the decoder; it does not replace engine-side playtesting.
