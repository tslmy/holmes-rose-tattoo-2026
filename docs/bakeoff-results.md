# Phase 1 bake-off results

## Iteration 4: relative QA gates, ambience v2, extra seeds (2026-11-19)

- **Floor gate made relative.** Calibration on iteration 3: the VLM floor check ("is every cyan walk zone on
  ground?") failed the untouched 1996 original in 18 of 23 rooms, because walk zones overlap furniture. A
  failed floor check now counts only when the same check passes on the original (cached per room in
  `work/qa/scene_NNN/floor_original.json`). The objects gate tolerates one confident miss when at least 85%
  of objects are found and the VLM diff does not report that object removed. Re-running QA on the existing
  redraws (no new images) took redrawn rooms from **22 to 49 of 80**.
- **Checked by eye:** the 27 newly passing rooms (1, 7, 11, 12, 14, 19, 26, 30, 32, 34, 37, 38, 40, 45, 52,
  53, 56, 67, 80, 82, 83, 93-98) keep layout, furniture and floor; no missing or invented objects. Minor: a
  darker rectangle near the scissors in 93 and 94, and the paper props on the chemistry table change look.
- **Ambience v2 (delegated):** scaled animated objects are cut at their drawn box, and small-delta pixels
  (water shimmer, scene 35) ride on the redraw as a delta. Finding: the scaled objects in redrawn rooms (the
  scene 60 doorman, figures) are real cut-out sprites (background fraction 6-16%), not old-room patches, so
  they correctly stay old art; the dark doorway in 60 is the door-opening animation.
- **Shared static images:** a room image used by two layers at the same box (a sound trigger listed twice,
  e.g. `60SND`) was skipped as ambiguous and showed as an old-art square; it is now cut once (11 objects in
  8 rooms).
- **Extra seeds** (`L2-clean-s53/s11/s23/s29`) are planned only for the 31 rooms with no passing redraw.
  The first run found the image server (10.0.0.26:8000) down, and a run during the outage overwrote the
  original four seeds of those rooms with `unavailable` records, so all 8 seeds were redrawn (248 jobs, 9.5 h).
  **9 more rooms pass: 58 of 80 are redrawn** (3, 15, 23, 42, 50 on s53; 18, 28 on s29; 27 on s11; 49 on
  s23). 22 rooms keep the ESRGAN baseline. Checked by eye: all 9 backgrounds are faithful; minor changes are
  the painted Laocoön now white marble (28), the blue curtains grey (50) and the window in 49 now a street.
- **In-game** (pack `playable-overrides-2x-remaster-it4`, all 80 scenes load): 8 of the 9 new rooms look
  right. **Room 18 did not:** old-art streaks covered the new walls and steps. The cause is not its sprites
  (the capture was the same with every room override removed) but the engine's haze: in rooms 7, 8, 18, 53
  and 68 the original fogs the screen through a palette lookup the hires compositor cannot reproduce, so it
  shows the native 1996 pixels wherever the haze applies (`scummvm-src/engines/sherlock/README-haze-reference.md`).
  `assemble` now keeps the ESRGAN baseline for these five rooms (`HAZE_ROOMS`), where those pixels blend in;
  18 now looks coherent, and 7 and 53 (redrawn since iteration 2, with the same ghosting) look much the
  same either way, as the haze covers most of them. **55 of 80 rooms are redrawn.** The morgue sheet in 15
  is a 1996 cut-out sprite and shows its dither.

## Iteration 3: animated-object frames and people cleanup (2026-11-18)

Approved by the user after iteration 2, which still showed 1996-art rectangles around animated room
objects and many candidates rejected for added people.

- **Changes:** `assemble.ambience_sprites` rebuilds every frame of background-like animated room objects
  (lights, shadows, fans) at 2x from the chosen background: pixels equal to the plate take the redraw,
  brightness-only changes scale the redraw by the same ratio, other pixels keep the upscaled old art
  (302 objects written). Redraw variants `L2-clean-s47/s19/s31/s41` set `remove_people`: people the
  redraw added (detected with no counterpart in the input) are painted out by a second edit of the
  redraw, pasted back only inside their feathered boxes. Both delegated
  (`test_remaster_ambience.py`, `test_remaster_peopleclean.py`); review fixed a frame/box misalignment
  for placeholder frames and a cleaned-size mismatch.
- **First run, fixed and re-run:** the input-match check crashed whenever the input had a person
  detection (15 rooms), and the 0.3 detection cutoff missed most figures (they score 0.2-0.3); one
  cleanup edit also often left the figure. The cutoff is now 0.2 with up to two re-detected passes
  (`people_left` recorded). All 320 redraws re-ran.
- **Results:** 141 of about 240 checked redraws had added people; cleanup cleared them in 112 (36 in
  one pass, the rest in two). QA passed 37 of 320 candidates; the VLM gate failed
  100 (iteration 2: 218), floor 176, objects 132, obstacles 110, warp 15, text 10, order 8. **22 of 80
  rooms** now use a redraw (iteration 2: 14): 6, 9, 10, 13, 17, 31, 35, 43, 44, 46, 47, 48, 51, 55, 57,
  58, 59, 60, 69, 73, 74, 77; the other 58 keep the ESRGAN baseline.
- **Pack:** `generated/playable-overrides-2x-remaster-it3` (80 backgrounds, room and ambience
  overrides). Headless smoke test: 80/80 rooms render. In-game capture (background mode, no focus
  steal): 80/80. No added people and no doubled characters in the 22 redrawn rooms.
- **Left for iteration 4 (in-game):** old-art patches remain where an animated object's frames
  differ from the plate (scene 35 pools, scene 74 shed lettering, scene 59 block left of the stove),
  a rectangle seam in scene 69's left window, low-res patches in scenes 6 (rubble) and 60 (window),
  and an odd ceiling shape in scene 43.
- **Ops:** free disk ran under the 15 GB floor (mostly purgeable space and local snapshots); the run
  used a temporary 8 GB floor, restored afterwards. An app crash interrupted the run once; the runner
  recovered the job left running.

## Iteration 2: static-only plates and room-object overrides (2026-11-17)

Approved by the user after iteration 1's in-game findings, with the request that testing stop popping up
the game window.

- **Changes:** `[prepare] plate_layers = "static"` leaves animated room objects (people, walkers, fire)
  out of the plate that is redrawn, so the engine no longer draws them twice. `assemble` cuts a 2x
  replacement for every unambiguous single-frame room object from the room's own 2x background (726
  objects in 78 rooms), so the 1996 static art no longer sits on top of a redraw. Both delegated
  (`test_remaster_staticplate.py`, `test_remaster_roomsprites.py`).
- **Bug found and fixed:** `redraw_bg` reused a cached `input.png` whenever its size matched, so the
  first iteration-2 run redrew the old plate with its people while QA compared against the new empty
  plate: 1/80 passed, 71 rooms failed the VLM gate for "added" people. The input is now rebuilt every
  time (`test_remaster_redraw_input.py`). The style brief also stopped asking for "real-looking people",
  and the keep-figures instruction says the game adds its characters, so empty places stay empty.
- **Planner gotcha, hit again:** QA for the five trial scenes (4, 9, 57, 60, 69) was planned as up to
  date before their new redraws existed. Re-planning QA and assemble after redraw-bg fixed it.
- **Result:** 14/80 rooms redrawn (10, 31, 35, 43, 44, 46, 47, 51, 55, 58, 59, 73, 77, 81), a mostly new
  set; 66 keep the ESRGAN baseline. Failed gates over 320 candidates: VLM 218, floor 211, objects 160,
  obstacles 145, warp 27, order 18, text 12.
- **The model adds people on its own:** with a verified empty input and the explicit prompt, FLUX.2
  Klein 9B still puts people into 347 of the candidates (scene 4: a couple on the pavement in front
  of the police station). Prompting does not stop it; the next lever is detect-and-inpaint or masking.
- **Pack:** `generated/playable-overrides-2x-remaster-it2` (ignored): backgrounds, 726 room-object
  overrides, temporal sprites. Smoke test: 80/80 scenes render (`--sdl-retries 6` for the known SDL
  blit error).
- **In game** (all 80 rooms captured with the window in the background: `SDL_MAC_BACKGROUND_APP=1`, so
  no focus steal and no Dock icon). Doubled walkers (57, 60) and the ghost head (9) are gone, and the
  static objects blend into redraws. Remaining:
  - Animated ambience objects (lights 35LITE01-03, fan 44FAN, glows 44HLGHT, shadow 44SHADW) still draw
    1996 frames as visible rectangles over a redraw. Next fix: build their overrides from each frame's
    difference to the original background, applied to the redraw.
  - Characters are still the SR sprites, and the redrawn rooms are photographic while they are not.

## Iteration 1: every room, to a playable pack (2026-11-16)

The user approved one full pass on sensible defaults, then playing it to find what to improve.

- **Run:** all 80 rooms, 9B `L2-fix` at seeds 47, 19, 31 and 41, through geometry and the Phase 1c gates
  (about 6 h unattended). New stage `assemble` (delegated; test `tools/tests/test_remaster_assemble.py`)
  picks per room the most ambitious passing redraw (best score among equals), else the ESRGAN baseline,
  and warps the redraw back onto the original layout so the unmodified engine's hotspots, walk zones
  and animations still line up (valid fraction 0.97 to 1.0; the rest is filled from the baseline).
- **Result:** 14/80 rooms redrawn (4, 6, 9, 10, 14, 48, 50, 51, 55, 57, 59, 60, 69, 80); 66 keep the
  baseline. Gates that failed on at least one attempt in those 66: vlm 51, objects 51, floor 51,
  obstacles 45, warp 14, order 6, text 3.
- **Pack:** `generated/playable-overrides-2x-remaster-it1` (backgrounds from `work/assemble/backgrounds`,
  sprites from `enhanced/sprites-temporal` as in v6). Audit: 80/80 backgrounds valid. Scene-start smoke:
  80/80 render (18 needed retries for the known intermittent `SDL_BlitSurface` error).
- **Seen by eye:** scene 6's redraw drops the fire damage (soot, broken window) that the story shows; no
  gate asks about story state that the original shows. Scene 4's sign stays garbled in both.
- **Seen in the running game** (every room captured after load; `work/assemble/play/`):
  - **The pack ships `plate.png`, not the bare background.** The plate has the room's default-state
    objects composited in (people, chairs, busts, fire). The engine draws those objects again as sprites.
    In baseline rooms the copies line up, so this only shows when an object moves or is removed. In
    redrawn rooms the copies differ: scenes 57 and 60 show extra walkers, and scene 9 shows a ghost head
    behind the seated man.
  - **Old object art over the new rooms.** Static room objects are engine sprites, so the 1996 art
    (stove and bust in 59, window panes in 69, chairs and busts in 9) covers the redraw.
  - **Characters are the SR sprites**, so their style differs from the redrawn rooms (Phase 3, as planned).

## Phase 1c: new lighting allowed, and structure-conditioned redraws (2026-11-15)

The user approved two changes after Phase 1b: new lighting is allowed, and a
ControlNet route gets a try. People, obstacles, objects, warp, floor and text
gates are unchanged.

**Result: (a) alone, 1 of 7 rooms passes (scene 9 at `L2-fix-s11`). (b)
ControlNet: 0 of 7, and the images are worse than the original.** Scene 9 is
the only room off the ESRGAN baseline.

| Room | Phase 1b | (a) new light rule, best 9B variant | (b) ControlNet, best of 0.65/0.8/0.95 | Still on baseline because |
| --- | --- | --- | --- | --- |
| 1 | fail | fail: L2-fix-s11 (objects), L2/L2-fix (order) | fail: warp, objects, offsets, vlm | inverted or missing objects at L2; ControlNet loses the lamp light and the banner |
| 26 | fail | fail: L2-fix-s11 (vlm, floor) | fail: warp, objects, offsets, vlm, floor | added bar furniture, recomposition; floor gate flags the pool table every time |
| 18 | fail | fail: L2-mask (obstacles, vlm, floor) | fail: warp, offsets, floor, changed | invented people on the stairs; ControlNet removes the water and barely changes the rest |
| 4 | fail | fail: L2-fix-s11 / s23 (obstacles only) | fail: warp, objects, offsets | the lamp added in the archway overlaps a hotspot (obstacles gate unchanged) |
| 2 | fail | fail: L2-fix-s11 (obstacles, vlm, floor) | fail: warp, objects, offsets, obstacles, vlm | invented woman, man and dog |
| 9 | fail (light) | **pass: L2-fix-s11 (0.91), L2-fix-s23** | fail: warp, vlm, changed | **picked: L2-fix-s11** |
| 36 | fail | fail: L2-fix (obstacles, vlm, text) | fail: warp, objects, offsets, obstacles, vlm, text | invented people, rewritten signs; ControlNet fogs the street and turns the people into ghosts |

### (a) Light is score-only; a narrow fire gate remains

- `light` no longer gates. `new_light_sources` is still recorded in each
  `qa.json`.
- A new `fire` gate fails only fire that changes the story. The VLM diff
  question gained a `story_fire` field: flames anywhere other than inside a
  fireplace, stove, lamp, candle or torch.
- **Reliability.** I made six scene 4 edits on the server: flames from the
  upper windows, flames from the doorway, and a burning cart, two seeds each.
  Each was asked three times (temperature 0 and 0.7):
  - all 18 answers failed them;
  - six Phase 1b finals with lit fireplaces or lamps passed in all 18 answers
    (scene 9 x3, 26, 4, 36);
  - across the 70 re-judged Phase 1b and 1c results, the gate never fired.

  Caveat: the positives are blatant. A faint orange glow in a window is
  untested.
- Re-judged without regenerating (QA only). Scene 9 now passes. Scene 4 is held
  back only by its archway lamp, which the obstacles gate catches by design.

### (b) Z-Image Turbo + ControlNet Union 2.1, depth + HED

- **Where it ran:** on this Mac. The image server's API accepts only the three
  FLUX klein models and rejects extra fields, its source is on the other
  laptop, and SSH to it is closed.
  - The `zimage-controlnet` backend has `allow_local = true`. Every other local
    backend stays off.
  - The weights re-downloaded on the first job: about 23 GB with DepthPro.
    Free disk went from 60 to 37 GB.
  - Runs were one job at a time, 9 steps, `--low-ram`. Each took 250-290 s
    (755 s for the first, including the download). Peak memory was 22 GB.
- **The sweep:** depth and HED, both at 0.65, 0.8 and 0.95; text-to-image from
  the visual brief. These variants use base level L1: an identity warp plus
  the layer-offset gate.
- **By eye:**
  - The layout is pinned. 50/50 blends with the original show almost no
    doubled edges in any room at any strength, and no people are invented.
  - But the pictures are neither photoreal nor a sharper original. They are
    murkier: brown-grey, foggy, soft and low-detail.
    - Scene 36 dissolves into fog and its people become ghosts.
    - Scene 9 loses the bookcases and the green lamp, and its armchairs turn
      red.
    - Scene 1 loses "221B", the headline and the cobbles.
  - The VLM agrees: photoreal 2-4/10 (9B L2 scores about 9) and palette 2-5/10.
- **A measurement bug found and fixed.**
  - The first QA pass failed every room on warp and offsets, with a p95 of
    hundreds of px, and found 0 of 8 objects in scene 1.
  - Cause: SIFT found nothing in common between the flat 1996 render and the
    retextured image. The resulting nonsense homography (scale 0.017) then
    drove object prediction and the warp fit.
  - `geometry.homography_degenerate` now detects that case. Object positions
    are then predicted with the identity, and the warp is fitted on the object
    anchors alone; thresholds are unchanged. With it, scene 1's p95 dropped
    from 587 to 8-19 px.
  - Rooms whose objects the detector can't find in the murky images (26, 36)
    still measure badly. Those fail on objects and vlm regardless.
- **Verdict:** this configuration is not a route to photoreal rooms. It removes
  the invented-people problem only by replacing the picture with a worse one.
  Untried: canny-only or lower control, more steps, a tile/img2img hybrid, or
  a ControlNet on the server. Queued as `bakeoff-1c-controlnet`.

### Delegation (Phase 1c)

- Three tasks went to OpenCode on qwen3.8-27b: firegate, controlnet and
  degenerate. All passed on the first attempt (137 s, 388 s, 176 s), with
  non-square fixtures this time.
- **Review fix:** for `controlnet` it wrapped variants in a 40-line dict
  subclass. That satisfied two of my tests that contradicted each other: the
  1b test wanted an exact dict, the 1c test wanted new keys. The spec conflict
  is mine. I replaced the subclass with a plain dict and loosened the older
  test.
- **Local-model rule break:** for `firegate` it edited
  `test_remaster_newcontent.py`, my Phase 1b test, which still required the
  old `light` gate.
  - That test was outside its allowed files. The delegate guard did not stop it
    because test files are not tracked as outputs.
  - The change was right, so I kept it. Next time the guard should also flag
    edits to acceptance tests.

### Review queue changes (Phase 1c)

- **Settled:** `bakeoff-light-gate-lit-lamps`, replaced by the user's rule.
- **Refreshed:** `bakeoff-room-levels` (scene 9: L2-fix-s11).
- **New:**
  - `bakeoff-1c-controlnet`
  - `bakeoff-scene4-archway-lamp`
  - `image-server-controlnet-route`

### Seed lottery (2026-11-16)

The user approved a seed lottery: 9B at `L2-fix` (all three prompt extras) with 12 more seeds
(3, 5, 13, 17, 19, 29, 31, 37, 41, 43, 47, 53), making 15 seeds per room with 11 and 23. Same gates as (a).

| Scene | Seeds that pass | Most common failing gates |
|------:|:---------------:|---------------------------|
| 1  | 0/15 | objects, order, vlm |
| 2  | 0/15 | obstacles, vlm, floor, objects |
| 4  | 4/15 (19, 31, 41, 47) | obstacles (archway lamp) |
| 9  | 8/15 | — |
| 18 | 0/15 | vlm, floor, warp, obstacles |
| 26 | 0/15 | vlm, floor (walk zone under the pool table), objects |
| 36 | 0/15 | obstacles, vlm, objects |

- **More seeds rescue rooms that are close, not rooms that fail systematically.** Scene 4 now passes
  without the obstacles-gate change. Scenes 2, 18, 26 and 36 fail the same gates on every seed, so their
  causes are the prompt, the model or the gate, not luck.
- **Picks:** scenes 4 and 9 at `L2-fix-s47`; the other five keep the ESRGAN baseline.
- **For iteration 1:** 4 seeds per room catches most of what 15 do (scene 4 passes at 4/15, scene 9 at 8/15).

## Phase 1b: fixes and re-run (2026-11-15)

**Result: 0 of 7 golden rooms pass, down from 2 of 7. Every room stays on the
ESRGAN baseline.** The two Phase 1 passes (scenes 4 and 9 at 9B L2) now fail
gates that are new in 1b. Both failures are real: scene 9 gains table lamps and
a lit fire, and scene 4 gains a lit doorway with a lamp. No gate was loosened.

Re-run: `remote-flux2-klein-9b` only, 8 variants x 7 rooms = 56 redraws, plus
the sprite test on 4B and 9B (`[bakeoff.variants]` in `remaster.toml`).

| Room | Phase 1 (9B) | Phase 1b best variant | Gates still failing | Why it stays on baseline |
| --- | --- | --- | --- | --- |
| 1 | fail (inverted order) | L2-fix-s11, 0.82 | objects, light | the newsstand is restocked and the windows are lit; seeds swap which gate fails |
| 26 | fail (zoom) | L2-fix-s11, 0.90 | vlm, floor | a bar shelf and a chair are added and the dartboard is removed; the scene is still recomposed (warp p95 13-50 px at every variant); the floor gate flags the pool table in every variant |
| 18 | fail (people) | L2-mask, 0.78 | obstacles, vlm, light, floor | two to six people are still invented on the stairs, the water becomes pavement, and wall lights are added |
| 4 | **pass** | L2-fix-s11, 0.85 | obstacles, light | a lit door and a lamp are added in the archway; the sign problem is below |
| 2 | fail (people) | L2-fix-s11, 0.86 | obstacles, vlm, light, floor | a woman, a man and a dog are invented at every seed and with masking |
| 9 | **pass** | L2-fix-s23, 0.91 | light | table lamps and a lit fire are added; this was invisible to the Phase 1 gates |
| 36 | fail (people moved) | L2-fix, 0.75 | obstacles, vlm, light, text | four to six people are added, and shop signs are rewritten |

### What each fix did

1. **Invented people: neither fix works.**
   - The keep-figures instruction (`L2-fix`, three seeds) did not stop it.
   - Masking the people out (`L2-mask`: inpaint them out, strip them from the
     brief, paste them back relit) did not stop it either. 9B fills empty
     floor with new figures on its own.
   - At L2, prompt-level control is exhausted. The remaining routes are an
     edit restricted to a mask that leaves the floor and figure areas
     untouched, or another model. Queued as `bakeoff-1b-invented-content`.
2. **Invented light and fire: the gate works, the prompt fix does not.**
   - Removing "fires" from the style brief and adding the no-new-light
     instruction did not stop 9B adding lamps, fires and lit windows.
   - The new `light` gate fails them, as intended. It also flags street lamps
     that are in the original but are now drawn lit (scenes 1, 4 and 36).
     That is a partial false positive, but no room's result depends on it.
3. **Scene 4 sign: fixed where it can be, and a weakness in the gate found.**
   - Diagnosis: Phase 1 had a legible "METROPOLITAN POLICE" from 9B, and the
     protection step pasted the garbled ESRGAN baseline over it.
   - Fix: the geometry stage now asks the VLM to read each writing region in
     the original and in the redraw. It keeps the redraw only when the two
     match. In this run 9B's sign was itself garbled ("DOLE IT FLDR..."), so
     the baseline was pasted, which is correct.
   - The baseline is garbled too, though (ESRGAN on 320x200 lettering). The
     real fix is to re-typeset writing regions. Queued as
     `bakeoff-retypeset-signs`.
   - Weakness: the new QA `text` gate read "METROPOLITAN POLICE" from that
     garbled final. qwen3.8-27b fills in text from context, so the gate can
     pass unreadable signs. It does catch rewritten signs (scene 36). Queued as
     `bakeoff-text-gate-ocr`.
4. **Protected-patch colour: fixed.**
   - Pasted patches get a per-channel gain and offset fitted on a ring around
     each region, then are applied to the patch. The scene 4 sign blends into
     the relit wall.
   - Spec correction (mine): a least-squares slope shrinks towards zero when
     the redraw is misaligned. The gain is now the ratio of standard
     deviations, clipped to 0.5-2.
5. **Added obstacles: gate added.**
   - OWLv2 boxes in the final that have no match in the warped original and
     cover more than 20% of a walk zone or hotspot fail the `obstacles` gate.
   - It fires on the scene 9 lamps, the scene 4 wall lamp, the scene 2 table
     and the people in scenes 18 and 36.
6. **L0/L1: dropped.**
   - Sweeping strength 0.75 / 0.85 / 0.9 (the share of the input kept):
     - geometry holds at 0.9 (warp p95 0.4-2.6 px), but the picture barely
       changes (change 4-13, gate at 20);
     - at 0.75 only scene 36 reaches change 20, and there it drifts 5.4 px.
       Elsewhere p95 is 0.9-3.9 px at change 6-15.
   - 0.75 and 0.85 gave byte-identical images. At 8 steps the server appears
     to round strength to the same starting step.
   - No room passes, so there is no strength at which geometry holds and the
     picture still changes. Phase 2 uses L2 or the baseline only.
7. **Scene 26 zoom and scene 1 inversion: not fixed by prompt or seed.**
   - Scene 26 is still recomposed with the framing instruction at every seed:
     warp p95 is 13-50 px and stretch is 1.15-2.1.
   - Scene 1 swaps an order inversion for missing objects across seeds.
8. **Sprite flicker: gate added; all sprites fail it.**
   - The temporal warp error (DIS flow, forward-backward check, high-pass at
     sigma 2.5, loop pair included), redraw over baseline:

     | | 4B | 9B |
     | --- | --- | --- |
     | Holmes | 3.48 | 3.25 |
     | Watson | 3.96 | 3.86 |

   - The gate is 1.5, and sprite-consistency-research.md predicted about 3.5.
     Characters stay on de-dithered SR until Phase 3's keyframe and flow work.

### Delegation (Phase 1b)

- Six tasks went to OpenCode on qwen3.8-27b: variants, relight, textcheck,
  newcontent, maskpeople and twe. Opus wrote each spec and acceptance test.
- All six passed on the first attempt. Agent time ranged from 2 to 28 min; the
  text check and the flicker metric took longest.
- **Local-model failure:**
  - In `temporal_warp_error` it swapped rows and columns
    (`a[vx, vy]`, `b[vx, vy]`). That crashed on the real 384-wide frames.
    It passed the test only because my synthetic frames were square.
  - I fixed it by hand. The flaw shared by the test is my spec gap.
- **Opus spec fix:** the relight gain (item 4).
- **Stricter than asked:** the text match also requires equal length and a long
  common run. This is what correctly rejected the garbled sign in this run.

### Review queue changes

- **Settled:**
  - `bakeoff-l0l1-drift`: dropped;
  - `bakeoff-protected-paste`: the relight is in;
  - `bakeoff-scene4-fires`: scene 4 is on baseline, and the light gate
    catches fires.
- **Refreshed by the report:**
  - `bakeoff-room-levels`: all rooms on baseline;
  - `bakeoff-sprite-route`: SR baseline.
- **New:**
  - `bakeoff-1b-invented-content`
  - `bakeoff-retypeset-signs`
  - `bakeoff-text-gate-ocr`
  - `bakeoff-light-gate-lit-lamps`

## Phase 1 (original run)

Run 2026-11-15 on the golden set (scenes 1, 26, 18, 4, 2, 9, 36; Holmes and
Watson) through `uv run remaster run`, then `uv run remaster bakeoff-report`.
Images and the contact sheet stay local (`work/bakeoff/contact.html`, game
data). This doc records the numbers, the provisional picks and what did not
work. Every pick is queued in `review/queue.md` and used meanwhile.

## Provisional picks

| Decision | Pick | Review item |
| --- | --- | --- |
| Background backend | `remote-flux2-klein-9b` (FLUX.2 klein 9B on the image server) | `bakeoff-backend` |
| Settings | seed 7, ~1 MP (1152x864 for 640x480), 8 steps; L2 = reference edit with no `strength` | `bakeoff-settings` |
| Level per room | 4: L2, 9: L2; 1, 26, 18, 2, 36: ESRGAN baseline | `bakeoff-room-levels` |
| Sprite route | identity-conditioned redraw with `remote-flux2-klein-4b` | `bakeoff-sprite-route` |

9B is the only backend whose redraws both pass every gate and change the
picture: 2 of 21 results (scenes 4 and 9 at L2). Five rooms keep the
baseline for now. Each fails one or two gates at L2; the reasons are below,
and most of the failures are real: 9B adds people the original does not have.

The 4B wins for sprites: both characters pass flicker, identity drift and
pose, and it is the fastest (8.5-13 s per frame). 9B passes too, at 27-28 s
per frame.

## What the levels are

The image server takes `strength` (share of the input kept, mflux
semantics), `steps`, `guidance` and up to 8 reference `images`.

- **L0**: img2img at strength 0.6.
- **L1**: img2img at strength 0.45.
- **L2**: the free reference edit, with no strength.

All three use 8 steps. Geometry ships an identity warp for L0/L1, so their
drift must stay under 2 and 4 px p95. For L2 it ships the fitted room warp.

The first run had L0-L2 differing only by one prompt sentence, so it was
discarded (mean pixel difference between levels 2-14/255). Its 14 FLUX.3
L0/L1 files are still on disk; the report now takes job state from the
queue, so they show as failed.

## Per backend and level

Times are wall-clock seconds per request on the shared second laptop. The
27B LLM was answering QA on the same machine, so expect ±50%. Peak memory is
not measured for remote jobs. "Change" is the mean |shipped - original|
(0-255) at the original size.

| backend | level | results | passed | mean s | mean score | change | warp gate ok | median p95 drift px |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| flux2-klein-4b | L0 | 7 | 0 | 36 | 0.51 | 23 | 0 | 74 |
| flux2-klein-4b | L1 | 7 | 0 | 10 | 0.44 | 31 | 0 | 97 |
| flux2-klein-4b | L2 | 7 | 0 | 30 | 0.65 | 37 | 5 | 11 |
| flux2-klein-9b | L0 | 7 | 0 | 50 | 0.59 | 24 | 0 | 19 |
| flux2-klein-9b | L1 | 7 | 0 | 26 | 0.51 | 33 | 0 | 110 |
| flux2-klein-9b | L2 | 7 | **2** | 63 | 0.76 | 42 | 6 | 11 |
| flux3-image-klein | L0/L1 | 0 | - | - | - | - | - | - |
| flux3-image-klein | L2 | 7 | 0 | 57 | 0.86 | 15 | 7 | 2 |

**FLUX.3 L0/L1 never ran.** The server returns HTTP 422 because "strength is
supported only for FLUX.2 image-to-image requests". That was recorded as a
failure on the first attempt, not retried.

**FLUX.3 L2 is close to a no-op.** It returns the input almost unchanged
(change 8-22). That is why it passes the geometry gates and scores highest.
The VLM rates the original 3D renders about as photoreal as a real remaster
(8/10), so the photoreal score did not catch it. A new gate,
`bakeoff_min_change = 20`, now requires a redraw to change the picture.
Without it, FLUX.3 would have been the pick.

**L0/L1 do not preserve geometry on FLUX.2.** At strength 0.6, image-to-image
still moves and resizes furniture (scene 26: the billiards table grows, the
door moves). At 0.45 it adds people (a second barman). The warp gate is right
to fail all 28. Whether 0.75-0.9 keeps geometry while still remastering is
open (`bakeoff-l0l1-drift`).

**Local runs, before diffusion moved to the image server.** FLUX.2 klein 9B
in mflux took 144 s and 23.2 GB peak per image on this Mac. The local
Qwen-Image 2.1 and Z-Image + ControlNet candidates were not run. Their weights
were deleted to free disk, and `[image].local_generation = false` keeps
diffusion off this Mac (`bakeoff-deleted-backends`). The optional paid GPT
Image comparison was not run.

## Why the five baseline rooms failed (9B L2)

| scene | gates failed | what it is |
| ---: | --- | --- |
| 1 | order | One left-right inversion. The VLM also lists an added lantern, books and a menu board (score only) |
| 26 | objects, floor | The model zoomed in about 1.2x. The billiards table is not re-detected under the new framing, and the floor check doubts a walk zone that now runs under the corner table |
| 18 | vlm, floor | The model added six people on the stairs and lit sconces on the walls |
| 2 | objects, vlm, floor | Five objects not re-detected (fireplace, bedroom door, chair among them). The model added a seated man, a woman and a dog |
| 36 | warp, vlm | Recomposed: people moved and new ones added (p95 drift 75 px, stretch 1.66 > 1.6) |

Adding people is 9B's most common failure at L2. The brief and the level
sentence say to keep every figure, so Phase 2 should try a negative-people
instruction, or mask people out of the edit, before giving up on those rooms.

Full per-row results are at the end.

## Checked by eye, not caught by the gates

**Scene 4, 9B L2: invented fires.** Flames appear behind the
police-station windows. The VLM gate fails only on removed objects or added
people, so it passed. Queued as `bakeoff-scene4-fires`. Phase 2 QA should ask
about new light sources and fire.

**Grey protected patches.** Writing and clue regions (≤ 8% of the room)
are pasted from the ESRGAN baseline with a feathered mask. When the redraw
relights the room, they stand out as flat grey rectangles. This shows on
FLUX.3 L2: scene 18 on the stairs and the obelisk base, and scene 2 on the
left wall. They need colour
matching to the redraw before Phase 2 (`bakeoff-protected-paste`).

**Static sprite passing.** Watson on FLUX.3 drew one frontal standing pose
in every frame and passed the pose gate (IoU 0.84). A minimum-motion check
now fails it (`sprite_min_flicker_ratio = 0.6`, flicker 0.44).

## The L2 room warp, honestly

The warp model is m(p) = s·(p + d(p)): a global scale times a smooth
displacement on a 40 px grid. It is fitted from SIFT + MAGSAC, DIS optical
flow and OWLv2 object anchors.

**Where it works.** When the model keeps the composition, the fitted warp
is invertible and hotspots land on their objects. On scene 9 (9B) the
hotspots sit on the paintings, busts, fireplace and seated man, and the walk
zones sit on the floor. On scenes 4 and 1 they line up as well. Across all
L2 results, 18 of 21 pass the warp gate (det > 0.05, stretch ≤ 1.6,
round-trip ≤ 0.5 px), with a median p95 drift of 11 px for FLUX.2.

**Where it fails.**
- **Recomposition is beyond a smooth warp.** 9B scene 36 (p95 75 px,
  stretch 1.66) and 4B scenes 18 and 9 failed. On 4B scene 9, matching found
  nothing usable, so the stretch is a sentinel.
- **Zoom or crop is only partly handled.** Scene 26's zoom is absorbed by
  the scale term, but a walk zone ends up under furniture.
- **Added objects go unnoticed.** The warp moves existing hotspots and has
  no idea that a new obstacle appeared, such as the side table and floor lamp
  9B added in scene 9. The object gate checks only objects that exist in the
  original.

**Not yet verified.** Walk-zone overlays were checked by eye only on scenes
26 and 9. The engine-side warp (Phase 4) has not been built, so no hotspot
has been clicked in-game.

## Sprite test

Each test redraws a cast sheet from frame 88, then walk frames 24-31 at
384x768 on grey. The cast sheet is passed as a second reference image.

Results from before the server accepted `images` are invalid as identity
tests. That server dropped the cast sheet silently; it is now recorded as
`references_dropped` when it happens. This run had the field.

| character | backend | pass | flicker vs baseline | DINOv2 drift excess | pose IoU | s/frame |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| holmes | 4b | yes | 1.05 | 0.074 | 0.93 | 13.0 |
| holmes | 9b | yes | 1.09 | -0.035 | 0.93 | 28.1 |
| holmes | flux3 | yes | 1.19 | 0.019 | 0.89 | 38.5 |
| watson | 4b | yes | 0.98 | -0.060 | 0.90 | 8.5 |
| watson | 9b | yes | 1.10 | -0.012 | 0.90 | 27.1 |
| watson | flux3 | **no** | 0.44 (static) | -0.167 | 0.84 | 42.4 |

**What looks good.** With the cast sheet reaching the model, coat
silhouettes, the deerstalker and Watson's striped scarf stay recognisably
the same across the walk. That is better than the earlier unconditioned
strips.

**What does not.** The fold and stripe detail the user pointed out still
shifts between frames. The flicker gate (≤ 1.5x the baseline's
frame-to-frame change) is too loose to catch it. The sprite-consistency
research (session notes, `sprite-consistency-research.md`) measured a
temporal warp error about 4x its floor for these redraws. It recommends
keyframes plus flow propagation and that metric as a gate, for Phase 3.

**Faces** stay small and soft at sprite scale. The face-detail research
(`face-detail-research.md`) recommends a crop, upscale to 768, regenerate
and blend-back stage, with a traits prompt or a face reference passed
through `images`. Not built in Phase 1.

## Delegation

Most Phase 1 engineering was done by Opus before the instruction to
delegate (2026-11-15). That covers the stages, the warp, the QA gates, the
report and the disk-check fix; it was not redone to delegate it. After the
instruction:

- **Delegated to OpenCode on qwen3.8-27b (second laptop)**, as `delegate`
  queue jobs with an Opus-written spec and acceptance test:
  - `test-bakeoff-report`: 120 lines, 157 s, passed on attempt 1.
  - `test-brief-fallback`: 133 lines, 544 s, passed on attempt 1. It also
    found and documented that a MagicMock client hangs `write_json`.

  Review changed one thing: the report test put its fixture images in the
  redraw folder rather than the geometry folder. My spec did not say which
  folder, so this is a spec gap, not a model failure.
- **Run by local LLMs, no cloud tokens:** all 7 briefs (10-33 s each) and
  every VLM QA review.
- **Local-model failures recorded:** none this phase.
- **Rough share:** about 10% of Phase 1 engineering by lines delegated,
  plus 100% of briefs and QA judgement. Phase 2 starts delegation-first.

## Pending decisions queued

- `bakeoff-backend`, `bakeoff-settings`, `bakeoff-room-levels`,
  `bakeoff-sprite-route`: the picks above.
- `bakeoff-l0l1-drift`: sweep strength 0.75/0.85/0.9 on 9B, or drop L0/L1.
- `bakeoff-protected-paste`: colour-match pasted patches (Phase 2 fix).
- `bakeoff-scene4-fires`: keep scene 4 at L2, or set it to baseline.
- `bakeoff-deleted-backends`: local candidates were not run.
- `download-detector-dino`: OWLv2 and DINOv2-small downloaded (0.68 GB).

## Per-result tables

Generated by `remaster bakeoff-report` (`work/bakeoff/results.md`).

| scene | backend | level | pass | score | change | s | GB | warp p95 px | stretch | found | gates failed |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | `remote-flux2-klein-4b` | L0 | no | 0.368 | 25.6 | 57.77 | - | 530.86 | 1000000.0 | 0.0 | warp, objects |
| 1 | `remote-flux2-klein-4b` | L1 | no | 0.606 | 34.7 | 9.61 | - | 28.34 | 1.481 | 0.643 | warp, objects, offsets |
| 1 | `remote-flux2-klein-4b` | L2 | no | 0.805 | 51.6 | 20.55 | - | 11.16 | 1.094 | 0.857 | objects, vlm |
| 1 | `remote-flux2-klein-9b` | L0 | no | 0.637 | 27.7 | 46.16 | - | 32.22 | 1.361 | 0.714 | warp, objects |
| 1 | `remote-flux2-klein-9b` | L1 | no | 0.601 | 35.8 | 25.19 | - | 55.88 | 1.791 | 0.571 | warp, objects, offsets |
| 1 | `remote-flux2-klein-9b` | L2 | no | 0.685 | 58.5 | 63.29 | - | 9.46 | 1.128 | 1.0 | order |
| 1 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 1 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 1 | `remote-flux3-image-klein` | L2 | no | 0.916 | 12.3 | 58.31 | - | 1.47 | 1.023 | 0.929 | objects, changed |
| 26 | `remote-flux2-klein-4b` | L0 | no | 0.666 | 20.1 | 34.21 | - | 22.64 | 1.199 | 0.778 | warp, objects, vlm |
| 26 | `remote-flux2-klein-4b` | L1 | no | 0.454 | 30.6 | 9.3 | - | 88.3 | 1.845 | 0.333 | warp, objects, offsets, vlm, floor |
| 26 | `remote-flux2-klein-4b` | L2 | no | 0.804 | 32.4 | 37.76 | - | 7.42 | 1.085 | 0.778 | objects, floor |
| 26 | `remote-flux2-klein-9b` | L0 | no | 0.642 | 17.8 | 53.0 | - | 13.75 | 1.129 | 0.667 | warp, objects, floor, changed |
| 26 | `remote-flux2-klein-9b` | L1 | no | 0.587 | 29.5 | 25.48 | - | 124.37 | 2.045 | 0.556 | warp, objects, offsets, vlm, floor |
| 26 | `remote-flux2-klein-9b` | L2 | no | 0.877 | 38.7 | 71.95 | - | 22.35 | 1.182 | 0.889 | objects, floor |
| 26 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 26 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 26 | `remote-flux3-image-klein` | L2 | no | 0.842 | 22.6 | 68.88 | - | 1.03 | 1.009 | 0.778 | objects |
| 18 | `remote-flux2-klein-4b` | L0 | no | 0.425 | 13.1 | 33.23 | - | 102.64 | 2.245 | 0.6 | warp, vlm, floor, changed |
| 18 | `remote-flux2-klein-4b` | L1 | no | 0.38 | 17.3 | 13.1 | - | 117.6 | 2.785 | 0.4 | warp, objects, offsets, vlm, changed |
| 18 | `remote-flux2-klein-4b` | L2 | no | 0.631 | 21.3 | 39.18 | - | 78.53 | 1.733 | 1.0 | warp, vlm, floor |
| 18 | `remote-flux2-klein-9b` | L0 | no | 0.507 | 11.7 | 54.44 | - | 19.03 | 1.224 | 0.8 | warp, vlm, floor, changed |
| 18 | `remote-flux2-klein-9b` | L1 | no | 0.577 | 19.2 | 28.44 | - | 106.73 | 2.464 | 1.0 | warp, offsets, vlm, floor, changed |
| 18 | `remote-flux2-klein-9b` | L2 | no | 0.671 | 24.9 | 70.0 | - | 59.85 | 1.568 | 0.8 | vlm, floor |
| 18 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 18 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 18 | `remote-flux3-image-klein` | L2 | no | 0.751 | 18.9 | 65.37 | - | 2.8 | 1.024 | 0.8 | floor, changed |
| 4 | `remote-flux2-klein-4b` | L0 | no | 0.595 | 26.8 | 6.45 | - | 6.0 | 1.086 | 0.75 | warp, objects, vlm |
| 4 | `remote-flux2-klein-4b` | L1 | no | 0.554 | 36.5 | 7.66 | - | 21.54 | 1.228 | 0.625 | warp, objects, offsets, vlm |
| 4 | `remote-flux2-klein-4b` | L2 | no | 0.643 | 44.8 | 16.37 | - | 4.52 | 1.045 | 0.875 | objects, order, vlm |
| 4 | `remote-flux2-klein-9b` | L0 | no | 0.673 | 29.7 | 22.28 | - | 7.94 | 1.103 | 0.875 | warp, objects, vlm |
| 4 | `remote-flux2-klein-9b` | L1 | no | 0.589 | 39.7 | 21.36 | - | 19.91 | 1.233 | 0.625 | warp, objects, offsets, vlm |
| 4 | `remote-flux2-klein-9b` | L2 | yes | 0.87 | 49.1 | 43.91 | - | 9.88 | 1.186 | 1.0 |  |
| 4 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 4 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 4 | `remote-flux3-image-klein` | L2 | no | 0.944 | 12.1 | 27.9 | - | 1.91 | 1.02 | 1.0 | changed |
| 2 | `remote-flux2-klein-4b` | L0 | no | 0.484 | 25.6 | 32.78 | - | 74.16 | 3.13 | 0.381 | warp, objects, vlm, floor |
| 2 | `remote-flux2-klein-4b` | L1 | no | 0.401 | 30.3 | 10.91 | - | 561.61 | 50223.208 | 0.238 | warp, objects, offsets, vlm, floor |
| 2 | `remote-flux2-klein-4b` | L2 | no | 0.728 | 27.0 | 34.25 | - | 12.11 | 1.159 | 0.762 | objects, vlm, floor |
| 2 | `remote-flux2-klein-9b` | L0 | no | 0.627 | 24.7 | 54.0 | - | 35.85 | 1.547 | 0.714 | warp, objects, vlm, floor |
| 2 | `remote-flux2-klein-9b` | L1 | no | 0.491 | 30.7 | 24.36 | - | 129.64 | 175501.087 | 0.429 | warp, objects, offsets, vlm, floor |
| 2 | `remote-flux2-klein-9b` | L2 | no | 0.776 | 30.1 | 63.09 | - | 10.83 | 1.123 | 0.714 | objects, vlm, floor |
| 2 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 2 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 2 | `remote-flux3-image-klein` | L2 | no | 0.922 | 11.7 | 61.79 | - | 2.77 | 1.028 | 0.952 | floor, changed |
| 9 | `remote-flux2-klein-4b` | L0 | no | 0.629 | 14.4 | 45.87 | - | 27.23 | 1.36 | 0.667 | warp, objects, vlm, changed |
| 9 | `remote-flux2-klein-4b` | L1 | no | 0.345 | 19.8 | 11.02 | - | 343.33 | 1000000.0 | 0.0 | warp, objects, offsets, vlm, floor, changed |
| 9 | `remote-flux2-klein-4b` | L2 | no | 0.357 | 31.7 | 33.0 | - | 485.19 | 1000000.0 | 0.0 | warp, objects |
| 9 | `remote-flux2-klein-9b` | L0 | no | 0.585 | 15.0 | 56.82 | - | 7.98 | 1.152 | 0.667 | warp, objects, vlm, changed |
| 9 | `remote-flux2-klein-9b` | L1 | no | 0.358 | 21.7 | 28.88 | - | 411.98 | 1000000.0 | 0.0 | warp, objects, offsets, vlm, floor |
| 9 | `remote-flux2-klein-9b` | L2 | yes | 0.863 | 35.3 | 64.06 | - | 8.63 | 1.085 | 1.0 |  |
| 9 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 9 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 9 | `remote-flux3-image-klein` | L2 | no | 0.919 | 8.1 | 57.22 | - | 1.75 | 1.017 | 1.0 | changed |
| 36 | `remote-flux2-klein-4b` | L0 | no | 0.422 | 37.6 | 42.25 | - | 101.48 | 2.349 | 0.7 | warp, order, vlm |
| 36 | `remote-flux2-klein-4b` | L1 | no | 0.321 | 45.5 | 11.04 | - | 97.15 | 5.874 | 0.4 | warp, objects, order, offsets, vlm, floor |
| 36 | `remote-flux2-klein-4b` | L2 | no | 0.612 | 49.8 | 30.94 | - | 9.57 | 1.133 | 0.8 | order, vlm |
| 36 | `remote-flux2-klein-9b` | L0 | no | 0.435 | 38.2 | 62.43 | - | 155.08 | 12.062 | 0.7 | warp, order, vlm |
| 36 | `remote-flux2-klein-9b` | L1 | no | 0.39 | 54.4 | 26.93 | - | 109.57 | 4.055 | 0.5 | warp, objects, order, offsets |
| 36 | `remote-flux2-klein-9b` | L2 | no | 0.596 | 55.5 | 62.33 | - | 75.44 | 1.66 | 0.7 | warp, vlm |
| 36 | `remote-flux3-image-klein` | L0 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 36 | `remote-flux3-image-klein` | L1 | failed | | | | | | | | Value error, strength is supported only for FLUX.2 image-to-image requests |
| 36 | `remote-flux3-image-klein` | L2 | no | 0.722 | 19.1 | 60.67 | - | 3.77 | 1.02 | 0.6 | objects, changed |

| character | backend | status | pass | flicker x | drift excess | pose IoU | s/image | GB |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| holmes | `remote-flux2-klein-4b` | ok | True | 1.05 | 0.0741 | 0.926 | 13.0 | None |
| holmes | `remote-flux2-klein-9b` | ok | True | 1.094 | -0.0352 | 0.926 | 28.1 | None |
| holmes | `remote-flux3-image-klein` | ok | True | 1.189 | 0.0191 | 0.888 | 38.5 | None |
| watson | `remote-flux2-klein-4b` | ok | True | 0.981 | -0.0596 | 0.896 | 8.5 | None |
| watson | `remote-flux2-klein-9b` | ok | True | 1.099 | -0.0123 | 0.895 | 27.1 | None |
| watson | `remote-flux3-image-klein` | ok | False | 0.438 | -0.167 | 0.842 | 42.4 | None |
