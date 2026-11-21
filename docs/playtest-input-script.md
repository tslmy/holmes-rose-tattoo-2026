# Scripted headless play-tests

The scene smoke test (`SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE`) only checks
each room's first frame. Story animations, room transitions and the UI only
appear after you interact with the game. The patched engine can instead run
a play-test from a text script. It sends the script's mouse and keyboard
input as ordinary ScummVM events and writes the frame the window would show
as PNG files. It needs no window focus, screen recording or OS input, and it
runs under SDL's dummy video driver.

## Engine requirement

The engine branch `claude/sherlock-playtest-input` in `scummvm-src/` adds
this (`engines/sherlock/tattoo/tattoo_playtest.{h,cpp}`, with a hook at the
start of `Events::pollEvents()`). It is off unless
`SCUMMVM_SHERLOCK_TATTOO_INPUT_SCRIPT` is set. Build it as described in the
scene smoke-test notes:

```sh
./configure --disable-all-engines --enable-engine=sherlock --disable-debug --enable-release
make -j8
```

## Run

```sh
python3 tools/playtest_rosetattoo.py \
  --scummvm /absolute/path/to/patched/scummvm \
  --data-dir scummvm \
  --save validation/saves/rosetattoo.000 \
  --pack generated/playable-overrides-2x-remaster-it5 \
  --scene 3 \
  --script tools/playtest_scripts/room03_open_wardrobe.txt \
  --output-dir /new/output/dir
```

For each attempt, the runner creates a new directory under `--output-dir`
holding a copy of the save, a config file, `HOME`, `stdout.log` and `dumps/`.
It never writes to the save, data or pack. It uses the same renderer settings
as the smoke test: `surfacesdl`, RGBA32 at 2x, palette-safe sprites, the edge
filter and random seed 1. Other options:

* `--scene N` jumps to scene N after loading, using the post-load hook.
* `--window` opens a real window so you can watch.
* `--env KEY=VALUE` passes extra engine settings.
* `--sdl-retries` retries only the dummy backend's intermittent
  `SDL_BlitSurface` error (default 3).

The runner writes `report.json` and exits 0 only when the script reaches
`quit` (or its end) with no error.

The example plays the wardrobe animation `03WARD` (canim_005) in room 3 and
produces six dumps: room, verb menu, two mid-animation frames (cAnim frames 4
and 8), Holmes's description of the interior, and the frame after the text
is dismissed. Each headless run takes about 15 s.

## Script format

One command per line. `#` starts a comment. Coordinates are native **screen**
coordinates (640x480, before the 2x scale, relative to the current scroll).
The engine multiplies them by the hires scale before sending the events.

| Command | Effect |
| --- | --- |
| `wait <ms>` | Wait for engine time in milliseconds. |
| `waitframes <n>` | Wait for `n` game frames (30 per second). This is more repeatable than `wait`. |
| `move <x> <y>` | Move the mouse. |
| `click <x> <y>` | Left click: move, wait 2 frames, press, wait 2 frames, release, wait 2 frames. |
| `rclick <x> <y>` | Right click, timed like `click`. Over a hotspot it opens the verb menu. |
| `verb <name>` | Click the named entry (`Open`, `Look`, ...) in the open verb menu. Fails, listing the entries, if the menu is closed or lacks it. |
| `key <name>` | Press and release a key: `a`-`z`, `0`-`9`, `f1`-`f12`, `escape`, `return`, `space`, `tab`, `backspace`, arrows, `home`, `end`, `pageup`, `pagedown`. |
| `waitidle [timeout_ms]` | Wait until the top-level scene loop has run for 5 frames in a row in standard mode with the cursor visible, no pending scene change, no active cAnim, and Holmes not walking. That excludes talk, menus, text windows, journal and transitions. Default timeout 60000. |
| `waitscene <n> [timeout_ms]` | Wait until scene `n` is loaded and the main loop is accepting input. |
| `waitcanim [timeout_ms]` | Wait until a cAnim is playing. |
| `objects` | Log the visible hotspots with name, type and screen rectangle, to help you find click targets. |
| `dump <name>` | Write `<name>.png` (letters, digits, `-`, `_`, `.`) and a log line. Requires `SCUMMVM_SHERLOCK_TATTOO_DUMP_DIR`. |
| `log <text>` | Write a log line with the current state. |
| `quit` | Quit the game. |

Without `quit` the game keeps running after the last command. With `--window`
you can then take over.

If a wait times out or a command fails, the engine logs an `ERROR`, writes
`error_line<N>.png`, and quits. Parse errors are reported before the game
starts.

### Output

`dump` writes the presented hires composite (`Screen::_roseTattooHiresComposite`).
These are the same pixels `copyRectToScreen` sends to the window, but without
the mouse cursor, which the backend draws. Without a hires scale it writes the
native 8-bit screen instead (`"source": "native_screen"`).

Every event is printed to stdout as `SHERLOCK_PLAYTEST_<KIND> <json>` and
appended to `<dump dir>/playtest.jsonl`. The kinds are `START`, `OBJECTS`,
`VERB`, `DUMP`, `LOG`, `ERROR`, `QUIT` and `END`. The state block contains:

```json
{"millis": 9123, "frame": 212, "scene": 3, "go_to_scene": -1,
 "holmes": [259, 309], "holmes_walking": false,
 "canim": {"number": 5, "name": "03WARD", "frame": 4},
 "menu_mode": 0, "idle": false, "mouse": [240, 213], "scroll": [0, 0], "hires_scale": 2}
```

`holmes` is in scene coordinates. `canim` is `null` when no cAnim is
playing. `menu_mode` is the engine's `MenuMode` value (0 standard, 3 talk,
20 lab, 21 message).

## Limits

* Timing follows real engine time, as in normal play. In three runs of the
  example, the mid-animation dumps landed on the same cAnim frames. A busy
  machine could still shift a dump by a frame, so for frame-exact
  comparisons use `waitcanim` plus `waitframes`, and check the `canim.frame`
  logged with each dump.
* The dumps do not include the cursor.
* The dummy backend's intermittent `SDL_BlitSurface` startup error still
  occurs. The runner retries it.

## cAnim suite

`tools/playtest_canim_suite.py` runs one generated script per reachable cAnim trigger and compares packs:

```sh
uv run python tools/playtest_canim_suite.py --scummvm scummvm-src/scummvm --data-dir scummvm \
  --save validation/saves/rosetattoo.000 --survey-dir work/playtest/survey \
  --pack before=generated/playable-overrides-2x-remaster-it4 \
  --pack after=generated/playable-overrides-2x-remaster-it5 --output-dir work/playtest/canims-it5
```

It needs a survey run per room first (`waitidle`, `objects`, `quit`) under `<survey-dir>/room<N>/`. A
trigger is an object `uses` entry with a verb, no inventory target and a cAnim number (1-based in the room
file); hotspots off screen in scrolling rooms are skipped. Results: `results.json` (which cAnim actually
played per pack) and `sheet-NN.jpg` pages.

## UI suite: exits, map, inventory, journal, scrolling rooms

`tools/playtest_ui_suite.py` generates scripts for what the scene and cAnim checks miss and runs them
per pack (same `--survey-dir`, `--pack label=dir` and sheet output as the cAnim suite, plus `--jobs`):

```sh
uv run python tools/playtest_ui_suite.py --scummvm scummvm-src/scummvm --data-dir scummvm \
  --save validation/saves/rosetattoo.000 --survey-dir work/playtest/survey \
  --pack it5=generated/playable-overrides-2x-remaster-it5 --output-dir work/playtest/ui-it5
```

* **exit**: the exit (arrow) zones come from the room file's exit table (`Exit::load()`, not in the
  extracted metadata). The suite clicks a free floor point of the zone (the engine ignores exits under
  a hotspot), dumps Holmes walking out, then `waitscene <dest>` and the first, 15th and settled frames
  of the next room; exits to the map (scenes 90/100) dump the map. Zones fully covered by an object
  (`room30covr`, `Exitcover1`, ...) are story-gated and skipped.
* **door**: object verbs whose use names change scene (`*NN`), e.g. Door > Open; other points of the
  object are tried when a person or hotspot is under the click.
* **scroll**: rooms wider than 640 px; Holmes is walked to each end over free floor. The expected scroll
  is predicted from the engine's rule (it scrolls once Holmes passes screen x 480 / 80 and settles with
  him about 320 px from the edge), so the check accepts ends the free floor cannot reach.
* **map**: leave Baker Street for the map, scroll it with the mouse at the screen edges, pick 221B or
  Scotland Yard, dump the close-up and the arrival.
* **inventory**: right-click empty floor, `verb Inventory`, dump, click outside to close.
* **journal**: right-click Watson where the save has him (room 24), `verb Journal`, dump.

Rooms whose survey shows talk mode after the scene jump are skipped (they ignore all input).
`results.json` holds automatic checks per run (expected scene reached, scroll range, near-black dumps,
script errors); `sheet-NN.jpg` shows one row per test.

Result on it5 (`work/playtest/ui-findings.md`, run `work/playtest/ui-it5-r4/`): 39 of 54 runs pass.
Map, inventory, journal frame, 16 room-to-room exits and 12 map exits render cleanly. Bugs found are
engine-side and pack-independent: a segfault in `TattooPerson::setWalking` when a talk script walks an
NPC with no frame (rooms 36, 33), and room 57's exit into scene 80 needing a missing `Holm57a.tlk`. The
exits of rooms 18/68 never fire. 15 rooms open in a silent talk mode after the post-load jump.

Gaps in the script language this suite works around or cannot cover:

* `key` events are pushed with `pushEvent()`, whose artificial event source does not allow keymapper
  mapping, so keys bound to engine actions (F1 journal, Tab/F3 inventory, Home/End on the map,
  Escape to close windows) do nothing (verified: F1 and Tab left the room unchanged). An
  `action <name>` command (or sending keys through the mapped path) would fix this.
* `objects` lists room objects but not people, so Watson (journal) and NPCs standing on hotspots cannot
  be located. Listing people (index >= 1000) would fix it.
* `waitidle` does not check `menu_mode`: it returns while a talk window, a description box or a silent
  talk state is up, and it returns before the room has finished scrolling (the suite adds
  `waitframes 45` after walks in scrolling rooms).
* Nothing waits for the overhead map: while it is up `go_to_scene` stays 90/100 and the main loop
  does not run, so `waitscene`/`waitidle` never return. The suite samples it with `waitframes`.
* There is no walk-to-scene-coordinate or scroll command; scrolling is predicted, and is off by up to
  about 100 px in some rooms (room 7).
