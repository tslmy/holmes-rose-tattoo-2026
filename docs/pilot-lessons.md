# Pilot lessons: index of the dated trees

Iteration 1 left ~25 GB of dated output trees under `enhanced/`, `mods/` and
`generated/` (all ignored by git). They stay on disk as a record of what was
tried; deleting any of them goes through the review queue
(`review/queue.md`), never a script. This page says what each one is and what
it showed, drawn from the docs and manifests named in each row. Where a row
says *(name only)*, no doc describes it and the purpose is inferred from its
name and manifest; treat that as a lead, not a finding.

Sizes are from `du -sh` on 2026-11-14.

## The two lines of work

1. **August 2026: diffusion redraws of backgrounds** (A1111 SDXL/ControlNet,
   then FLUX.1-schnell, Z-Image Turbo, an optional GPT Image pass) plus
   "lifelike" character-sheet redraws. Lesson: every model drifted geometry,
   moved or invented objects and garbled signage once strength was high
   enough to look different; only very low strength (FLUX `--steps 12
   --strength 0.25`, 3 real steps) kept text, and that barely changes the
   look. Character sheets could not stand in for per-frame animation.
   ([`reproducing.md`](reproducing.md) history, [`tools/README.md`](../tools/README.md)
   calibration notes, [`z-image-setup.md`](z-image-setup.md), dev blog.)
2. **October 2026: non-diffusion SR + engine work.** Real-ESRGAN 2x on every
   background, room sprite, cAnim and shared sprite, followed by a series of
   small pilots on scene 26 to settle alpha, palette and contour handling,
   ending in the assembled candidate
   `mods/rosetattoo-contour-candidate-20261031`.
   ([`sprite-remaster.md`](sprite-remaster.md), [`sprite-contour-audit.md`](sprite-contour-audit.md),
   [`contour-selection-plan.md`](contour-selection-plan.md),
   [`scene-start-smoke-test.md`](scene-start-smoke-test.md).)

Iteration 2's design answers both: diffusion is allowed to move things, but a
per-room warp absorbs the movement and object/writing gates check it
(`docs/remaster-plan.md`); de-dithering and plate-first redraw address the
noise and lighting mismatch the August runs suffered from.

## `generated/` (August diffusion and assembly experiments)

| Tree | Size | What it is / what it showed |
| --- | ---: | --- |
| `neural-redraws-cinematic-full` | 611M | A1111 SDXL, profile `photographic-cinematic`, all 80 scenes at 2x. Strong restyle; geometry and signage drift. Fed `playable-overrides-2x-v2..v6`. |
| `neural-redraws-faithful-full` | 705M | A1111 SDXL, profile `photographic-faithful`; manifest records 1 scene in its last run. Lower drift, little visible change. |
| `sdxl-controlnet` | 1.1M | Local SDXL Canny ControlNet spot tests (`tools/img2img/sdxl_controlnet.py`). *(name only)* |
| `sdxl-cuda-full-2x`, `sdxl-playable-backgrounds` | 150M each | SDXL ControlNet full 2x run (CUDA) and its playable copy; backgrounds for `mods/sdxl-hires-backgrounds`. Protect-mask inpainting kept walk zones/hotspots, but the remaster plan records it as rejected for geometry drift. |
| `flux-geometry-locked` | 134M | FLUX.1-schnell low-strength redraw with the hard resource/edge geometry lock (`flux_redraw_rosetattoo_backgrounds.py`). Kept geometry and text only at strength ≤0.25; ≥0.35 garbled signage and invented people/props. |
| `z-image-turbo` | 1.9M | Z-Image Turbo img2img spot tests. Low-strength visual candidate only: compositing the original mask back over its output visibly damaged the redraw ([`z-image-setup.md`](z-image-setup.md)). |
| `openai-image-pass` | 4K | Empty: the optional GPT Image benchmark was never run (needs a paid API key). |
| `character-redraws` | 35M | Identity/profile-driven character sheets (Holmes, Watson, Mycroft, Toby, Wiggins, generic NPC bodies). Profiles must stay distinct; a generic "Victorian detective" prompt loses identity. |
| `animation-redraws` | 1.5M | Pose-matched animation redraws composited from those sheets. A ten-pose sheet is not a substitute for each motion frame; opt-in only in the assembler. |
| `playable-overrides-2x-v2` … `-v6` | ~345M each | Successive playable assemblies: cinematic SDXL backgrounds + temporal ESRGAN sprites + lifelike sheet sprites (v2-v5) or animation redraws (v6). Exercised the assembler/runtime contract, not quality. |
| `upscale-benchmark-20261031-kGlUy5` | 36M | One-off Real-ESRGAN x4plus benchmark on this Mac (`report.md` inside): sprites (20 frames in 4.24 s on MPS, 1.6 GB peak), fonts, map crops, guarded full map, scene 5 tiled vs untiled. Untiled 1280x960 map exceeded MPS memory; overlapping tiles worked. |
| `*-audit-root-20261031` (4 dirs) | 0 | Empty roots left by audit runs. |

## `enhanced/` (SR outputs and October pilots)

| Tree | Size | What it is / what it showed |
| --- | ---: | --- |
| `sprites` | 454M | First ESRGAN pass over every `VGS.LIB` resource, items, fonts, map, RES##.VGS stills. Baseline for shared sprites. |
| `sprites-temporal` | 232M | Temporal residual-aggregation pass. Small or inconsistent stability gains on ten streams, some RGB edits at moving edges; **not applied globally**. |
| `sprites-lifelike*` (6 dirs: plain, -v2, -v4, -seeded, -seeded-v2, -seeded-v4) | 34-51M | Character-sheet redraw variants (seeded = before the temporal pass). Lesson as for `generated/character-redraws`. |
| `esrgan-hires-backgrounds` | 134M | Real-ESRGAN 2x of all 80 backgrounds: the background set every October pack uses. |
| `room-remaster-20261031`, `canimations-remaster-20261031`, `palette-walk-remaster-20261031`, `shared-remaster-20261031` | 1.9G, 2.8G, 1.4G, 255M | Full 2x soft-alpha ESRGAN of all room sprites (32,697 frames), cAnims (33,189), palette-qualified walks (18,380) and shared sprites. The baselines for the contour audit. |
| `shared-vgs-variants-agent-20261031` | 26M | The five resources whose `VGS.LIB` and `WALK.LIB` data differ, keyed by stream hash ([`vgs-variants.md`](vgs-variants.md)). |
| `*-contour-staging-20261031` (room, canimations, palette-walk, shared, shared-vgs) | 1.8G, 2.7G, 1.3G, 252M, 26M | `refine_rosetattoo_sprite_alpha.py --method scale4-box --radius 0.35 --thin-guard --preserve-support` over each baseline: the selected contour direction. Audits found no RGB/support/topology changes except one cAnim frame (`scene_044/canim_004`), whose whole resource falls back to the baseline. |
| `room-remaster-pilot-20261031` | 4.8M | Scene-26 room-sprite ESRGAN pilot (128 frames): first ordered in-engine replay; lamp, bartender, bar and patron drew in the right order. |
| `room-remaster-pilot-temporal-20261031`, `room-remaster-tiled-pilot-20261031` | 4.4M, 436K | Scene-26 temporal pass and tiled-inference variants of that pilot. Tiling: negligible difference; temporal: not adopted. *(name + sprite-remaster notes)* |
| `room-support-pilot-20261031`, `canimations-support-pilot-20261031`, `palette-watson26-support-pilot-20261031` | 4.8M, 980K, 7.3M | `--preserve-support` contour pilots for scene 26 (room, `26HEXIT` cAnim, Watson). Audit: all 241 frames kept RGB and support; flagged only for threshold crossings. |
| `canimations-pilot-20261031` | 1.0M | Scene-26 `26HEXIT` (18 frames) ESRGAN pilot: loaded in order and matched native `BEHIND` layering in a fixture. |
| `room-contour-pilot-20261031`, `canimations-contour-pilot-20261031`, `palette-watson26-contour-pilot-20261031`, `canim44-contour-pilot-20261031` | 0.9-7.3M | Contour-method pilots before `--preserve-support`; superseded by the support pilots. *(name only)* |
| `palette-watson26-runtime-20261031` | 7.5M | Watson frames for room 26's palette, the runtime baseline for the Watson audit; 56-index projection matched its sidecar in engine. |
| `palette-walk-alpha-pilot`, `-guard-pilot`, `-pilot-frame87` (all `-20261031`) | 80K each | Single-frame alpha / thin-guard checks on one walk frame (frame 87). A guarded 0.55-px feather was subtle and changed provenance coverage; not adopted. *(partly name only)* |

## `mods/` (assembled packs)

| Pack | Size | What it is / what it showed |
| --- | ---: | --- |
| `rosetattoo-contour-candidate-20261031` | 6.3G | **The iteration-1 result.** ESRGAN backgrounds + all five audited contour trees: 80 backgrounds, 3,640 resources, 87,656 frames, `scene_044/canim_004` on baseline alpha. All 80 scenes reached the start marker in the smoke test with haze fallback. Not a natural-entry playthrough. |
| `esrgan-hires-backgrounds` | 137M | Backgrounds-only 2x ESRGAN pack. |
| `esrgan-hires-playable` | 364M | ESRGAN backgrounds + temporal sprites + animation redraws (August assembler). |
| `sdxl-hires-backgrounds` | 382M | SDXL ControlNet backgrounds pack; rejected for drift. |
| `neural-hires-backgrounds-faithful-refined` | 180M | Faithful ControlNet renders where available, Lanczos fallback elsewhere. |
| `flux-geometry-locked` | 131M | FLUX geometry-locked backgrounds pack (no manifest). |
| `room-remaster-pilot-20261031` | 368M | First October pack on the August assembler. |
| `room-remaster-alpha-pilot-20261031` | 391M | + soft alpha (shared + scene-26 room sprites, 194 resources). Soft and binary alpha render differently at edges. |
| `room-remaster-canim-pilot-20261031` | 392M | + the `26HEXIT` cAnim. |
| `room-remaster-identity-pilot-20261031` | 419M | + VGS variants keyed by stream identity: wrong-name overrides fall back to native. |
| `room-remaster-palette-pilot-20261031` | 419M | + palette-qualified walks; used for the nine-scene smoke pilot. |
| `room-remaster-watson26-pilot-20261031` | 426M | + Watson for room 26's palette; 343 lookups hit across four fixture walks. |
| `room-remaster-feather-pilot-20261031`, `-guard-pilot-20261031` | 419M each | Feathered alpha and thin-guard variants; not adopted. *(name + sprite-remaster notes)* |
| `room-remaster-support-pilot-20261031` | 426M | `--preserve-support` contour trees in engine; this direction was selected. |
| `canim44-baseline-fallback-pilot-20261031` | 132M | Tests the whole-resource fallback for `scene_044/canim_004` (10 frames). |

## What carries into iteration 2

- Diffusion must never be trusted to keep geometry or writing by low strength
  alone: hence object/writing gates and the warp.
- Characters were the weakest part of every August attempt: hence casting
  sheets plus per-frame identity-conditioned edits, judged composited.
- The ESRGAN contour candidate is the fallback for any asset that fails a
  gate; keep `mods/rosetattoo-contour-candidate-20261031` until a better pack
  passes the smoke test.
- Tiled inference is safe for large images (map) on this Mac's memory.
- The 20261031 scene-26 fixtures (bartender, `26HEXIT`, Watson walk) are why
  scene 26 and `026/000` are in the golden set.
