# Rose Tattoo post-load scene-start smoke test

This test asks whether the **requested scene's initial frame was presented**
after loading a copied save. It does not establish natural reachability,
correct story flags, animation or visual quality, interaction, later stability,
or full playability. The post-load hook keeps the copied save's state and
position; scene 7 in particular must use this hook, not the fresh-start hook.
The engine can fall back to native artwork if an override is missing or
unusable, so a rendered marker is not proof that every pack asset was used.

## Engine requirement

Build the ScummVM branch `terra/scene-smoke-engine`, commit
`36349bc30acfc8aab96290a615f9a7c7fb9a94dd`, which is based on the combined
`codex/edge-focused-sprites` tip (including `codex/haze-reference`). For example,
in that separate worktree:

```sh
./configure --disable-all-engines --enable-engine=sherlock --disable-debug --enable-release
make -j8
```

The marker is silent unless both `SCUMMVM_SHERLOCK_TATTOO_SCENE_SMOKE=1`
and `SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE=N` are set. It requires a real
loaded save, the requested room resource to exist, and the current scene to
match `N`. It is emitted only after the scene transition, `screen.update()`, and
any initial entrance animation return.
An unpatched binary, a different scene, an invalid save, or an earlier failure
must not be treated as a successful render.

## Run

The event-driven pipe reader is intended for POSIX hosts; the local run below
was on macOS with SDL2. Other backends and platforms need separate validation.
Use the pilot first. Replace all placeholder paths with absolute paths and
choose a **new** output directory under an existing parent directory. The
existing save and existing pack are inputs only; no setup requires editing or
copying files into either. For a future final pack, change `--pack` to that
pack's directory and repeat both runs; a result for the pilot does not validate
the future pack.

```sh
python3 tools/smoke_rosetattoo_scenes.py \
  --scummvm /absolute/path/to/patched/scummvm \
  --data-dir /absolute/path/to/local/game-data \
  --save /absolute/path/to/existing/rosetattoo.000 \
  --pack /absolute/path/to/pilot-or-final-pack \
  --output-dir /absolute/existing/parent/new-pilot-run \
  --pilot --timeout 30 --sdl-retries 4

# Only after examining the pilot results, use a different new output directory:
python3 tools/smoke_rosetattoo_scenes.py \
  --scummvm /absolute/path/to/patched/scummvm \
  --data-dir /absolute/path/to/local/game-data \
  --save /absolute/path/to/existing/rosetattoo.000 \
  --pack /absolute/path/to/pilot-or-final-pack \
  --output-dir /absolute/existing/parent/new-all-scenes-run \
  --all --timeout 30 --sdl-retries 4
```

`--pilot` is 1, 2, 7, 18, 26, 36, 53, 68, 91. `--all` discovers the actual
`RESnn.RRM` scene resources rather than assuming a contiguous range; this game's
local data has 80. `--scenes 7 26` selects individual resources. The program
returns nonzero unless every selected scene has a `rendered` result.

Each attempt has its own new directory, save copy as `rosetattoo.000`, config,
home, working directory, logs, and screenshot path. The source save is read
once into memory and copied fresh for each attempt; autosaving is disabled and
the driver's output path cannot be inside the data, pack, or save directory.
The driver does not modify inputs, although it is not an OS-level read-only
sandbox for an arbitrary or malicious binary. It fixes the random seed, sets
SDL dummy video/audio and software rendering, uses the `surfacesdl` backend,
RGBA32 2x, palette-safe sprites and edge filtering, and removes inherited
ScummVM/SDL/XDG settings. It stops the child at the exact marker, or at a
bounded per-attempt timeout, using event-driven stdout, not elapsed-time
screenshots or process survival as evidence.

`report.json` records the executable and source-save hashes, pack manifest
hash if present, settings, selected scene IDs, completion flag, per-scene
status, and each completed attempt's log and exit code. Check `completed` and compare
the selected IDs with the results if a run was interrupted. The manifest hash
is **not** a checksum of the whole pack. A present
background filename likewise does not verify decoding or use. Results:

* `rendered`: the exact requested marker was observed. The process is stopped
  immediately afterwards, so its termination code is not a game crash and no
  post-marker gameplay stability is measured.
* `timeout`: no matching marker or exit before the deadline.
* `exited_without_marker`: exited zero before the marker; not success.
* `crash_or_error`: exited nonzero before success; inspect the logs for the
  actual cause rather than assuming it is a crash.
* `sdl_blit_error`: the child exited with the specific SDL error
  `SDL_BlitSurface failed: Parameter 'src' is invalid!` before success.
  This occurred intermittently under this machine's dummy backend, including
  on scenes that also passed on another attempt. The cause was not established.
  `--sdl-retries` (default 0, maximum 10) retries **only** that exact error,
  with a fresh save and config; all failures remain in the report. Other errors
  and timeouts are never automatically retried.

Driver tests, using fake child processes and temporary files:

```sh
python3 -m unittest tools.tests.test_scene_smoke_driver -v
```

## Local results

On 2026-10-31 the nine-scene pilot used
`mods/room-remaster-palette-pilot-20261031` (manifest SHA-256
`68d793608ca6186d7189ece0ef92f8c446df26d2e24f16ab579b477a6bfb2bb3`)
and a copy of `validation/room-remaster-test-saves/rosetattoo.000` (SHA-256
`1694e405733dfc885c16d7d2bff187fb5e7f558f627192a0c1f5f108df3fccd7`). All
nine emitted their matching marker; two required a retry after the SDL error.
The subsequent full run emitted matching markers for all 80 scene resources.
It recorded 22 SDL blit-error attempts across 17 scenes before those scenes
rendered on retries; no final scene result was a timeout or other error. The
binary's SHA-256 was
`16f63931429ce2cec25c4d8347cc6c81c694fe63eab0c87ff830f331b661e4dd`.

The full per-attempt logs and reports are temporary, at
`/private/var/folders/kr/hnndgkzd7wddlz37gv3py84r0000gn/T/opencode/rosetattoo-scene-smoke-pilot-final/`
and
`/private/var/folders/kr/hnndgkzd7wddlz37gv3py84r0000gn/T/opencode/rosetattoo-scene-smoke-all-final/`.
Real-game negative checks also produced no marker with the opt-in variable
absent, with scene 36's RRM omitted from an isolated game-data directory, and
with an empty isolated save directory (scene 36 only; no user save was touched).
