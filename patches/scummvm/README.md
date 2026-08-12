# ScummVM patches

Repeatable engine-side patches against a ScummVM checkout. Apply with
`git apply patches/scummvm/<name>.patch` from inside a `scummvm-src/`
checkout, or see [`docs/reproducing.md`](../../docs/reproducing.md) for the
full recommended apply order.

These same changes are also available as real commits on the
`rosetattoo-hires-mod` branch of <https://github.com/tslmy/scummvm> (a
personal ScummVM fork) — see the root
[README's ScummVM Strategy section](../../README.md#scummvm-strategy) for
why a submodule hasn't been adopted yet and how to clone that branch
directly instead of applying patches one by one.

Patches are listed roughly in the order they build on each other. Several
later patches are explicit follow-ups/supersessions of earlier ones (noted
below and cross-referenced inside the patch files themselves) — apply in
this order to avoid conflicts.


| Patch | What it does |
| --- | --- |
| `rosetattoo-start-scene-env.patch` | Adds an `SCUMMVM_SHERLOCK_TATTOO_START_SCENE` environment-variable debug hook so validation tooling can jump straight to a given scene at launch instead of playing through the intro. |
| `rosetattoo-fix-vertical-walk-delta-x.patch` | Fixes a vertical-walk sign bug in `TattooPerson::setWalking()` (unrelated to hires work — a pre-existing engine bug hit while testing). |
| `rosetattoo-hires-mouse-scale.patch` | Scales reported mouse coordinates by the hires scale factor in `Events::pollEvents()`, so the cursor can move across the *entire* enlarged window instead of being confined to a region the size of the original 640x480 game. |
| `rosetattoo-hires-cursor-fix.patch` | Fixes a black/undersized mouse cursor in hires mode: `Screen::setPalette()` was early-returning without forwarding the palette to the backend when the hires pixel format isn't CLUT8. |
| `rosetattoo-hires-cursor-ai-override.patch` | Adds an AI-upscaled true-color room cursor override on top of the coordinate/palette fix above — `Events::setCursor()` now prefers a real upscaled cursor frame (see `tools/extract_rosetattoo_sprites.py`/`upscale_rosetattoo_sprites.py`) over nearest-neighbor-scaling the original 8-bit art. |
| `rosetattoo-hires-map-black-screen-fix.patch` | Fixes the overhead/travel map rendering as a black screen in hires mode by clearing the stale room-sized background override so `update()` falls back to a plain scale-up instead of crashing/rendering garbled. |
| `rosetattoo-hires-map-upscale-override.patch` | Adds a real AI-upscaled hires background override for the overhead map (previously only had the black-screen fix, i.e. correct scaling but still blocky low-res art), factoring background-override decode/validation into a shared `loadRoseTattooHiresBackgroundFromPath()` helper. |
| `rosetattoo-hires-font-ttf-override.patch` | Adds hires TrueType rendering for tooltip/UI text: loads a real vector font from `$SCUMMVM_SHERLOCK_TATTOO_ASSET_OVERRIDES/fonts/hires_font.ttf` via FreeType2 and renders crisp anti-aliased text into a persistent alpha-blended overlay (`Screen::_roseTattooHiresTextLayer`), instead of upscaling the native ~10px bitmap glyphs. |
| `rosetattoo-hires-tooltip-text-fix.patch` | Follow-up to the above: fixes tooltip text lingering on screen after the mouse leaves a hotspot, and the hires background "blinking"/showing through moving character sprites near an open tooltip. Supersedes that patch's `widget_tooltip.cpp`/`.h` hunks. |
| `rosetattoo-hires-map-icons.patch` | Fixes missing location icons on the overhead map in hires mode by baking AI-upscaled icons into the map's persistent hires world buffer via `Screen::paintRoseTattooHiresWorldSprite()`. |
| `rosetattoo-hires-journal-glitch-fix.patch` | Follow-up to the TTF/tooltip patches above: fixes Watson's Journal-specific color-noise (palette read into the wrong buffer) and doubled/ghosted text (missing background-empty guard) bugs. |
| `rosetattoo-hires-character-object-sprites.patch` | Extends the AI-upscaled sprite-override system (previously only used for inventory item icons) to the live scene's walking characters and bg-shape objects, via a new `Screen::queueRoseTattooHiresSceneSprite()`/`_roseTattooHiresSceneSpriteLayer` wired into `TattooScene::drawAllShapes()`. Depends on `rosetattoo-hires-cursor-ai-override.patch`. |
| `rosetattoo-hires-map-sprite-purge-fix.patch` | Fixes AI-upscaled characters "hovering" over the overhead/travel map (and briefly over the next room after closing it): `TattooMap::show()` now clears `_roseTattooHiresSceneSpriteLayer` since the map's own render loop never calls `TattooScene::drawAllShapes()` to refresh/clear it. Depends on `rosetattoo-hires-character-object-sprites.patch`. |
| `rosetattoo-hires-scene-sprite-occlusion-fix.patch` | Fixes AI-upscaled characters/objects visibly "floating above" un-overridden furniture and the right-click verb menu instead of being occluded by them. Adds per-native-pixel provenance tracking so `blendRoseTattooHiresSceneSpriteLayer()` can detect when native-only content was drawn over a queued sprite's position later in the same frame and skip blending there. Depends on `rosetattoo-hires-character-object-sprites.patch`. |

## Writing a new patch

Follow the house style already used above: a short prose section at the top
explaining the bug/feature, root cause (for fixes), and how it was
implemented, cross-referencing any patches it builds on or supersedes,
followed by a standard unified diff.

`scummvm-src/` is its own separate git repository (gitignored by the outer
repo) with all accumulated hires work as *uncommitted* changes in one
working tree — new sessions' changes land in the same dirty tree as every
prior session's. When generating a diff for a new patch:

```sh
GIT_EXTERNAL_DIFF= git -C scummvm-src diff --no-ext-diff --no-color -- <path>
```

The `GIT_EXTERNAL_DIFF=` override is required — this repo's git config
points `diff.external` at a `difftastic` binary that isn't installed in
every environment, and plain `git diff` either fails outright or produces
difftastic's side-by-side text output instead of a real unified diff (which
is *not* `git apply`-able — two earlier patches in this directory were
accidentally saved that way and had to be regenerated). Because the working
tree accumulates every session's changes on shared files (`screen.cpp`,
`screen.h`, etc.), manually isolate only the hunks that belong to the new
patch's feature before saving the file — don't just paste the whole `git
diff` output for a shared file.
