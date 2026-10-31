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
python tools/extract_rosetattoo_vgs_variants.py --data-dir scummvm \
  --palette-scene 1 --output-dir extracted/vgs-variants-new
python tools/extract_rosetattoo_palette_walks.py --data-dir scummvm \
  --output-dir extracted/palette-walks-new
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

The newer renderer uses the installed palette for native indexed fallback
pixels (including darts' separate palette). When the installed palette is
entirely black, the presented true-color frame is blackened without discarding
retained layers; a true-color cursor may remain visible. Intermediate fades
and arbitrary palette effects still do not recolor cached true-color art. A
native-only fallback is not yet safe because some text exists only in the
hires layer; palette effects and their restoration need further engine work.

Shared walk sprites can be palette-dependent even without an animated palette:
their runtime colors come from the current room, whereas a single extracted
PNG set uses one reference palette. The opt-in palette-safe renderer checks
the actual installed palette against a generated projection and falls back to
native art when none matches. It does not reconstruct arbitrary palette
effects after the original indices have been discarded. In addition,
scenes 7, 8, 18, 53 and 68 apply native haze/mask effects after drawing scene
sprites; those effects require dedicated validation or a safe native fallback.
File coverage is not evidence that these cases are visually correct.

Some shared resource names occur in both `VGS.LIB` and `WALK.LIB`, with
different frame data in this game copy. For five affected names, the updated
engine looks for a variant keyed by the actual decompressed stream and decode
mode; a missing matching variant falls back to native artwork rather than a
possibly wrong name-only override. See [VGS variants](vgs-variants.md).
The palette-qualified extraction and its sidecar format are described in
[palette-qualified walk sources](palette-walk-extraction.md). It covers
initial room palettes, not every possible dynamic palette state.

After both extraction runs finish, verify every decoded frame against its RRM
stream (this can take time):

```sh
python tools/audit_rosetattoo_extraction.py \
  --room-source extracted/room-sprites-new \
  --canim-source extracted/canimations-new \
  --report validation/extraction-audit.json
python tools/audit_rosetattoo_palette_walks.py --data-dir scummvm \
  --source-dir extracted/palette-walks-new
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
python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/vgs-variants-new \
  --output-dir enhanced/vgs-variants-new \
  --scale 2 --alpha-mode soft --device mps --skip-existing
python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/palette-walks-new \
  --output-dir enhanced/palette-walks-new \
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
  --sprite-dir enhanced/vgs-variants-new \
  --sprite-dir enhanced/palette-walks-new \
  --output-dir mods/modernized-new --scale 2
python tools/audit_rosetattoo_pack.py --pack mods/modernized-new \
  --sprite-source extracted/shared-sprites-new \
  --sprite-source extracted/room-sprites-new \
  --sprite-source extracted/canimations-new \
  --sprite-source extracted/vgs-variants-new \
  --sprite-source extracted/palette-walks-new \
  --verify-source \
  --report validation/modernized-new-audit.json
```

The audit compares file presence, format and dimensions with the supplied
extraction manifests; `--verify-source` also checks the pixel hash embedded in
newly generated PNGs. Declared resource sidecars are copied through upscaling
and assembly, then checked against the source bytes. Older outputs without
pixel hashes cannot pass `--verify-source`.
It does **not** prove that the extraction covers all
game resources or that the engine consumes each asset correctly. Validate in
game: include moving characters, foreground occlusion, scripted animations,
scrolling rooms, palette transitions, UI, inventory, map and save/load.

For a manual test with a patched `scummvm-src/scummvm` binary and a **copy**
of a save in slot 0, for example:

```sh
SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES="$PWD/mods/modernized-new" \
SCUMMVM_SHERLOCK_TATTOO_HIRES_SCALE=2 \
SCUMMVM_SHERLOCK_TATTOO_HIRES_FORMAT=rgba32 \
SCUMMVM_SHERLOCK_TATTOO_PALETTE_SAFE_SPRITES=1 \
./scummvm-src/scummvm --path="$PWD/scummvm" \
  --savepath="$PWD/validation/test-saves" --save-slot=0 \
  --themepath="$PWD/scummvm-src/gui/themes" \
  --extrapath="$PWD/scummvm-src/gui/themes" \
  --aspect-ratio --stretch-mode=pixel-perfect \
  --no-fullscreen --window-size=1280,960 rosetattoo
```

Create `validation/test-saves` and copy in a valid `rosetattoo.000` first;
ScummVM may write saves to the specified directory during play. The installed
game remains untouched. `rosetattoo` must be a configured ScummVM target.

The existing temporal pass can stabilize high-frequency residuals across
similar frames, but arbitrary room image frame indices are not necessarily
playback order. Treat its output as experimental until tested against actual
animations; it does not change frame timing or game logic.
It accepts nested resources and `--skip-existing`; completed sequences are
reused only when their input files and temporal settings still match. Soft
alpha is preserved. Its output manifests can be used as sprite inputs to the
assembler once the entire selected run is complete.

For edge-smoothing experiments without rerunning ESRGAN, the optional
`tools/refine_rosetattoo_sprite_alpha.py` rebuilds alpha from the native frame
and feathers it by `--radius` output pixels, while keeping RGB, offsets,
source hashes and palette sidecars intact. Bitmap-font alpha is left unchanged.
It writes a separate tree and
supports `--skip-existing`. The experimental `--thin-guard` protects some
one- and two-pixel strokes and gaps, but is not a topology guarantee. Test a
small representative set before running it on a whole pack: a larger radius
can soften or erase small details, and rendering at 2x still limits the
precision of a silhouette.

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
An isolated scene-26 pilot exercised the three lamp frames and 31 bartender
frames in a running engine; a matched capture showed the character, lamp,
bartender, bar and foreground patron in the expected order. This does not
cover all animation frames, palette transitions or game interactions.
Another isolated fixture invoked the real `26HEXIT` cAnim stream: all 18
frames loaded in order and matched the original `BEHIND` layering. A Watson
frame comparison confirmed that soft-alpha edges render differently from the
same RGB image with a thresholded mask. Neither fixture replaces a full
playthrough or verifies every character and animation.

A later scene-26 fixture loaded a real palette-qualified Watson frame. Its
resource identity and 56-index installed-palette projection matched the
sidecar, and an independent compositing check matched all 62,480 pixels in the
drawn rectangle. Missing, corrupt and mismatched-palette sidecars fell back to
native art, including after a prior cache hit. The pilot contained only one
qualified Watson frame; continuity, normal scene entry and palette effects
still need in-game testing.
