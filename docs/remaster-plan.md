# Rose Tattoo remaster: iteration 2 plan

Written 2026-11-14. This supersedes the "what's next" notes scattered across
the other docs. It is a plan, not a record of work done.

## Where iteration 1 left off

Already working (keep, do not rewrite):

- **Extraction** of backgrounds, room sprites, cAnims, shared/walk sprites with
  palette projections, plus hotspot / walk-zone / protect masks per scene.
- **ScummVM fork** (`scummvm-src`, branch `rosetattoo-hires-mod`): true-colour
  hires compositor with ordered sprite replay, soft alpha, palette-qualified
  walk sprites, TTF text for tooltips/dialogs/journal, click queue, map pacing.
- **Audits**: extraction, pack, contour/topology, 80-scene start smoke test.
- **Candidate pack** `mods/rosetattoo-contour-candidate-20261031`: 2x
  Real-ESRGAN, 80 backgrounds, 3,640 sprite resources, 87,656 frame paths.

Gaps against the goal:

| Goal | Status |
| --- | --- |
| Redrawn, richer-colour art | Only ESRGAN 2x shipped. FLUX / SDXL / Z-Image redraws exist for backgrounds only and were rejected for geometry drift. Sprites were never redrawn. |
| Smooth sprite edges | Geometric contour refinement at 2x; stair-stepping still visible. |
| Masks follow new art | Alpha is pinned to the old silhouette. Nothing regenerates it from new art. |
| Smoother animation | Investigated only (`reproducing.md`), nothing built. |
| Modern font | Works, but font path is fixed, config is env-vars only, five haze rooms fall back to bitmap text. |
| One-command pipeline | ~35 separate scripts, dated ad-hoc output directories, no single entry point or config. |
| Local-agent delegation | Prompt polishing targets Ollama; LM Studio / OpenCode are not wired in. |

Repo hygiene issues found:

- The fork's pushed `rosetattoo-hires-mod` is ~20 commits behind the local
  submodule checkout, so nobody else can reproduce the current engine.
- Uncommitted work from earlier agent sessions (`terra/*`, `codex/*`) is in the
  workspace. Leave it for its owners; do not fold it into new branches.
- `enhanced/` + `mods/` hold ~25 GB of dated pilots; 84 GB free. They are
  kept as a record of what was tried.

## Facts that shape the design

- **Hotspot hit-testing is rectangle-based**, not per-pixel
  (`TattooScene::findBgShape` uses `getNewBounds().contains(pt)`). Walk zones
  come from room data. So gameplay geometry depends on each frame's *canvas
  size and offset*, not its alpha. Alpha only affects compositing/occlusion.
- Logic ticks at 30 Hz; animations hold frames via `_sequences` bytecode
  (roughly 10 fps of distinct art). Frame indices are logic-visible.
- Machine: M2 Max, 32 GB. LM Studio serves
  `mimo-v2.6-distill-qwen-9b-heretic`, which is **text-only** and loaded with
  an 8k context. `opencode` is not on `PATH`. No diffusion weights are cached.
- **Reference look**: `ChatGPT-conversation.pdf` and the local reference
  image folder show the target: each scene re-rendered as a photograph of a
  physical set, with real-looking people, real materials and practical
  lighting. Those images also recompose freely (camera, crop, furniture and
  figures all move), which a playable room cannot do. One correction in that
  conversation matters as a rule: keep the original's vibrant palette rather
  than letting realism desaturate it.
- mflux (already the `zimage` extra) now covers FLUX.2 klein 4B/9B edit,
  Qwen-Image-Edit, and Z-Image ControlNet on MLX. That makes a server-style
  harness (ComfyUI, Draw Things) unnecessary: backends stay plain Python
  behind `tools/img2img/common.py`.

## Design decisions

1. **Logic space and picture space are separate, joined by a per-room
   warp.** The game keeps running in its original 640x480 coordinates:
   hotspots, walk zones, scripted positions and saves are untouched. Each
   redrawn room ships a smooth, invertible mapping from original coordinates
   to the new picture. The engine draws sprites at warped positions (with the
   warp's local scale) and maps the mouse back through the inverse before any
   hit test. So hotspot boxes never need hand-redrawing: they follow the
   picture automatically, and the artist model may shift, resize and
   re-proportion things. The warp is estimated offline by dense matching
   between original and redraw, anchored by the room's own named objects
   found in the new image with an open-vocabulary detector.
   What it cannot express: objects swapping order, appearing, vanishing, or a
   camera move large enough to fold the mapping. Those fail the gate.
   Three freedom levels are tried in order of ambition, per room:
   **L2** warp-mapped relayout, **L1** outlines move within a few pixels
   (identity warp plus alpha/offset sidecar), **L0** layout locked.
2. **Art direction: photoreal.** Each room should read as a photograph of a
   built Victorian set, matching the ChatGPT references in materials, light
   and people. Objects and any writing must survive; camera and proportions
   may drift as far as the warp allows. Staying near the original palette's
   vibrancy is a preference used to rank candidates, not a gate.
3. **Present at 4x (2560x1920).** Backgrounds, UI and text are authored at 4x.
   Sprites start at 2x and are upgraded per resource; the compositor must
   accept mixed `@2x` / `@4x` assets.
4. **De-dither first.** 256-colour ordered dither is noise that both SR and
   diffusion amplify. A deterministic de-dither pass feeds every later stage.
5. **Plate-first redraw.** Composite each room's default-state foreground
   sprites onto its background, redraw the plate once, then cut static
   foreground layers back out of the redrawn plate. Lighting and texture match
   by construction.
6. **People are cast once, then rendered per frame.** For each character,
   generate and approve a photoreal identity sheet from the original sprite
   (face, costume, a few angles). Every frame is then an edit conditioned on
   the original frame (pose and silhouette) plus that sheet, with fixed seed
   and settings per resource. Sequences get a flicker and identity-drift
   metric and fall back whole to the SR baseline when they fail.
7. **One neutral-lit master per walk resource, graded per room.** Instead of
   redrawing 496 palette projections, redraw once and derive each room's
   colour grade from the palette projection already extracted. This also
   keeps photoreal figures sitting in each room's light.
8. **Masks are regenerated from the new art.** Alpha comes from segmenting
   the redrawn image, seeded by the original mask. Static foreground layers
   are re-segmented from the redrawn plate. Results go into the room's
   geometry sidecar next to the warp, and occlusion order is re-checked in
   engine.
9. **Smoothness comes from the renderer plus offline in-betweens.** Logic
   stays at 30 Hz; presentation goes to display rate, drawing one animation
   step behind so the next frame is known. Positions are lerped. In-between
   images are looked up by `(frame A, frame B, k)`; if missing, cross-fade;
   if that looks wrong for a pair, hold.
10. **Nothing waits for a human.** Work that needs approval (casting sheets,
   backend choice, per-room freedom level, deleting anything) goes into a
   review queue: `review/queue.md` plus an HTML sheet of candidates, each
   with the default that is being used meanwhile. Downstream work proceeds on
   the provisional default and is tagged with it, so an overturned decision
   re-queues exactly the affected assets. Jobs run from a persistent queue
   under `caffeinate`, resumable after sleep, crash or reboot.
11. **Cloud tokens only for engine C++ and pipeline design.** Every batch job
   is a plain resumable Python process. The local LLM writes prompt briefs
   and judges QA; it does not write engine code.

## Target pipeline

One entry point, `uv run remaster <stage> [--scenes ...]`, driven by a
`remaster.toml` (game data path, LLM base URL + model names, image backend
per asset class, scales, thresholds). Stages are resumable, content-hashed,
and write to stable directory names with a manifest. Existing scripts become
the stage implementations.

| Stage | Does | Mostly new? |
| --- | --- | --- |
| `extract` | Existing extractors + extraction audit | no |
| `prepare` | De-dither, build plates, structure/edge control images | yes |
| `brief` | Per-scene prompt briefs via the OpenAI-compatible LLM endpoint | port from Ollama |
| `redraw-bg` | Photoreal re-render of each plate, conditioned on depth and line structure from the original, then tiled detail pass to 4x | yes (backends exist) |
| `cast` | Identity sheets per character, for human approval | yes |
| `redraw-sprites` | Cut-outs from plates; identity-conditioned sequence redraw; per-room grade | yes |
| `geometry` | Estimate the room warp, segment new art, write alpha and the geometry sidecar, check invertibility | yes |
| `interpolate` | Offline in-between frames for animated resources | yes |
| `qa` | Deterministic gates, VLM review, HTML contact sheets, review-queue entries | partly |
| `run` | Queue runner: executes pending stage jobs unattended | yes |
| `assemble` | Existing assembler + pack audit | no |
| `smoke` | Existing scene-start smoke test + fixtures | no |

QA gates (a failure re-queues with changed settings, then falls back):

- Backgrounds: every hotspot object found in the new image, in the same
  left-right and depth order; warp is invertible with bounded stretch;
  walk-zone floor still reads as floor after warping; writing and clue
  regions are warped in from the SR baseline rather than redrawn; VLM asked
  "what was added or removed?". Palette closeness is a ranking score only.
- Sprites: canvas identity, alpha/offset caps, pose agreement with the
  original frame, identity drift against the cast sheet, sequence flicker.
- In-betweens: ghosting/double-edge detector per pair.

## Phases

Each phase ends with something reviewable. GPU batches run unattended; engine
work proceeds in parallel.

**Phase 0 - Foundations.**
`remaster` CLI skeleton and `remaster.toml`; LM Studio client replacing the
Ollama path; locate/install the OpenCode CLI; pull image weights through
mflux; choose text and vision models for briefs and QA (LM Studio or Ollama,
whichever serves the better model that fits); queue runner and review queue;
write `docs/pilot-lessons.md` indexing the old dated pilot trees and what each
showed; pick a golden set
(about six scenes covering street, interior, a haze room, a clue with
writing, a scrolling room, a dark room; plus Holmes, Watson, two cAnims,
cursors, items). Push the fork branch so the submodule pointer is real.
*Done when:* `remaster extract prepare` runs clean on the golden set.

**Phase 1 - Bake-off on the golden set.**
Find how much freedom the warp buys. Run each golden room at L0, L1 and L2. Compare FLUX.2
klein edit, Qwen-Image-Edit and Z-Image + ControlNet at several strengths on
plates, and the identity-conditioned route on Holmes and Watson, against the
ESRGAN baseline and the ChatGPT references. The existing GPT Image script can
supply an optional paid comparison for plates only (80 images in total).
Record time per image and gate results. Produce one HTML contact sheet.
*Done when:* a provisional backend, settings and freedom level are chosen by
gate scores and queued for your confirmation. Later phases start on them.

**Phase 2 - Backgrounds.** All 80 plates, QA loop, human pass on the sheet.

**Phase 3 - Sprites.** Begins with a research task for local agents: survey
sprite-sheet / character-animation LoRAs and pose-conditioned models, and
whether any run on this Mac, then add the promising ones to a sprite
bake-off. Static cut-outs from plates; cast the main characters (sheets
queued for approval, work continues on the top-ranked sheet); redraw hero walk sheets, then the long tail of cAnims and
generic NPC sheets; write geometry sidecars; derive per-room grades.

**Phase 4 - Engine (parallel with 2-3).** Room warp for drawing and mouse
input, with a debug overlay of warped hotspots and walk zones; alpha/offset
sidecars; per-room sprite grading; mixed-scale assets and
4x output;
haze/mask effects reproduced in true colour so rooms 7, 8, 18, 53, 68 stop
falling back; palette fades applied to cached true-colour art; env vars
become ScummVM game options; font chosen in options, defaulting to a system
font, covering all remaining bitmap text.

**Phase 5 - Animation.** Renderer interpolation first (position lerp and
cross-fade need no assets), then offline in-betweens (RIFE-class model on
premultiplied RGBA, alpha recovered by rendering over two backgrounds).

**Phase 6 - Validation and release.** Natural-entry playthrough, updated
`reproducing.md` reduced to a handful of commands, blog post revision.

## Local delegation

| Worker | Used for |
| --- | --- |
| Python batch jobs | All extraction, generation, interpolation, audits |
| LM Studio text model | Scene briefs, summarising QA reports into re-queue decisions |
| LM Studio vision model (to add) | Original-vs-redraw review, sign/text checks |
| OpenCode + local model | Unattended triage loop over failed QA items; small script edits with tests. Needs context raised to >= 32k |
| Opus | Engine C++, pipeline skeleton, gate design, phase reviews |

`lms unload` the LLM while a diffusion batch runs; 32 GB does not hold both
comfortably at 4x.

## Risks

- **The warp is the new idea and is untested.** Dense matching between a
  dithered 1996 render and a photoreal image may be unreliable; walk paths
  can look wrong if floor perspective changes; scripted animations that
  touch scenery (opening a door) need tight local alignment. L1 and L0
  remain as per-room fallbacks, and the debug overlay makes failures visible.
- **Photoreal people are the hardest part.** Actors are ~100 px tall, there
  are tens of thousands of frames, and a face that changes between frames is
  worse than a blurry one. If identity-conditioned edits flicker, options in
  order are: whatever the LoRA research turns up, stronger temporal
  stabilisation, a per-character LoRA trained here (slow), then keeping characters on de-dithered SR over photoreal rooms.
- **Style mismatch.** Photoreal rooms with SR characters, or the reverse,
  may look worse than either alone. Judge them composited, never apart.
- **The earlier diffusion runs failed on exactly this** (moved objects,
  rewritten signs). The difference now is protected writing, object-level
  gates and the warp absorbing movement instead of forbidding it.
- **In-betweens on sparse, fast motion ghost.** Hence per-pair fallback.
- **Model choices above are candidates**, not verified on this machine. Model
  availability and memory fit must be confirmed in Phase 0.
- **Scale.** ~70k unique frames; a 4x pack is several times the current
  11 GB against 84 GB free. The runner checks free space before each batch
  and queues a request rather than deleting old pilots itself.

## Decisions taken

1. Art direction: photoreal, per the ChatGPT references, with freedom to
   re-proportion rooms as far as the warp allows (2026-11-14). Palette
   vibrancy is a preference.
2. Output: 4x backgrounds and text; sprites 2x first, upgraded later.
3. Hardware: this Mac only.
4. Old dated pilot trees stay on disk, untracked, as a record of what was
   tried. Deleting any of them only if space runs short, via the review queue.
5. Attendance is minimal: approvals are queued and never block parallel work.
6. Local LLM choice is open (LM Studio or Ollama).
