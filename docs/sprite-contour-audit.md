# Read-only sprite contour audit

`tools/audit_rosetattoo_sprite_contours.py` compares an existing upscale tree
with a proposed support-preserving refined tree. It only reads those trees and
the optional extracted source tree; it never chooses, copies, changes, or
installs assets. Each resource is assessed as a whole so a later decision can
avoid mixing frames within an animation. A flagged resource is **not** an
automatic fallback decision.

## Dependencies and use

Python 3.10+, Pillow, NumPy, and **SciPy** are required. SciPy's compiled
`ndimage.label` keeps connected-component work off Python's per-pixel loops;
there is intentionally no slow pure-Python fallback. These packages are not
all core project dependencies. Install them into a separate environment, for
example:

```sh
uv venv /path/to/sprite-audit-venv --python 3.12
uv pip install --python /path/to/sprite-audit-venv/bin/python pillow numpy scipy
/path/to/sprite-audit-venv/bin/python -B -m unittest tools.tests.test_sprite_contour_audit -v
```

From the repository root, an example pilot comparison is:

```sh
/path/to/sprite-audit-venv/bin/python -B tools/audit_rosetattoo_sprite_contours.py \
  --baseline enhanced/palette-watson26-runtime-20261031 \
  --candidate enhanced/palette-watson26-support-pilot-20261031 \
  --source extracted/palette-watson26-source-pilot-20261031 \
  --report validation/contour-audit-watson-new.json
```

The report must be a **new** file outside every input tree, in an existing
directory. `validation/` is ignored; use a different report name on each run.
Creation is exclusive, so an existing file or symlink is never replaced. If
the process is interrupted or fails unexpectedly, the newly created file can
be incomplete; choose a different name to rerun. Exit status 0 means no flags;
1 means the completed report contains suspects or global errors; 2 means an
invalid invocation. Do not equate 0 with a playtest pass.

`--source` may be omitted, but then matching `source_sha256` PNG tags are
only compared with one another, not verified against extracted pixels. A
present tag is not proof of provenance. The report states the verification
mode and records problems; `decoded_rgba` indicates the requested mode, not
that every source read succeeded. When provided, source may be a superset:
only the resource paths present in baseline/candidate are examined. Source
files are paired by their own manifest `index`, not by a synthesized filename.

## What is checked

* Recursively discover resource `metadata.json` files and PNGs by relative
  resource directory, pair records by index, and flag missing, duplicate,
  reordered, empty, unreferenced, unreadable, or invalid records. Directory
  symlinks are not traversed; file symlinks and unsafe filenames are rejected.
  Errors outside an individual resource are in `errors` and block a clean
  result even if no resource is individually flagged. Per-scene summary
  metadata is compared separately.
* Compare all baseline/candidate resource metadata except `frames` and the
  expected `alpha_refinement` description. Compare frame records including
  index, filename, dimensions, offsets, and other metadata. Require complete
  upscales and positive integral scales. With `--source`, compare its resource
  and frame metadata to the baseline, allowing for renamed frame files and
  dimensions/offsets scaled by the baseline scale.
* Compare every declared sidecar byte-for-byte; compare against source too
  when supplied. A present but undeclared `palette.bin` is flagged. This checks
  preserved palette sidecars, not whether the running engine selects or
  interprets the correct palette.
* Check baseline/candidate `source_sha256` tags are valid and identical. With
  source, recompute SHA-256 over ASCII `WIDTHxHEIGHT:` followed by the decoded
  RGBA bytes, as the upscaler does. If candidate PNGs contain
  `alpha_input_sha256`, check it against the complete baseline PNG bytes.
* Require RGBA output images and compare their dimensions and all RGB bytes,
  **including transparent pixels**. Count pixels where `(alpha != 0)` changes.
* Compute alpha-weighted area as `sum(alpha)/255` in output-pixel units,
  its signed difference and percentage relative to baseline. A zero baseline
  makes the percentage `null`. Count threshold crossings separately in both
  directions for `alpha >= 128`.
* At that threshold, count foreground components with 8-connectivity and
  enclosed background holes with 4-connectivity. Explicit transparent padding
  connects the outside background; that outside component is not a hole,
  including for fully transparent or edge-cropped frames.

Each JSON resource lists its `issues`, `suspect_frame_indices`, frame-level
metrics, and aggregate `metrics` over the frames actually compared. A frame is
suspect on any metadata/provenance/read error, RGB change, nonzero-support
change, or **any** >=128 threshold crossing. A resource is suspect if it has
any resource issue or suspect frame. Alpha-only area change without a threshold
crossing is reported but is not itself flagged: there is no invented area
threshold. The resource metrics also show threshold-crossing and topology-count
changes, summed added/removed pixels, area change, and the maximum absolute
per-frame percentage. Missing/unreadable frames are not included in pixel
aggregates; check `frames_compared`, resource issues, and global errors before
using them.

Counts alone miss positional and shape changes that preserve component/hole
counts. Preserving nonzero support and palette bytes also does not verify
runtime composition, hit ownership, animation timing, visual quality, or
gameplay. Inspect flagged sequences and perform in-engine tests before rollout;
this tool cannot prove safety or select a threshold for you.

## Pilot observations and performance

On 2026-10-31 the three source-backed comparisons below used Python 3.12.11,
NumPy 2.5.4, SciPy 1.18.1, and Pillow 13.0.0 on an Apple M2 Max. The pilot
reports are local ignored files; the command lines were:

```sh
PY=/private/var/folders/kr/hnndgkzd7wddlz37gv3py84r0000gn/T/opencode/sprite-audit-venv/bin/python
/usr/bin/time -lp "$PY" -B tools/audit_rosetattoo_sprite_contours.py --baseline enhanced/palette-watson26-runtime-20261031 --candidate enhanced/palette-watson26-support-pilot-20261031 --source extracted/palette-watson26-source-pilot-20261031 --report validation/contour-audit-watson26-terra-v3-20261031.json
/usr/bin/time -lp "$PY" -B tools/audit_rosetattoo_sprite_contours.py --baseline enhanced/room-remaster-pilot-20261031 --candidate enhanced/room-support-pilot-20261031 --source extracted/room-source-pilot-20261031 --report validation/contour-audit-room26-terra-v3-20261031.json
/usr/bin/time -lp "$PY" -B tools/audit_rosetattoo_sprite_contours.py --baseline enhanced/canimations-pilot-20261031 --candidate enhanced/canimations-support-pilot-20261031 --source extracted/canimations-source-pilot-20261031 --report validation/contour-audit-canim26-terra-v3-20261031.json
```

| Pilot | Frames / resources | Frames with >=128 crossings | Frames with count changes | Wall / user CPU | Max RSS |
|---|---:|---:|---:|---:|---:|
| Watson | 95 / 1 | 95 | 0 | 1.28 / 0.99 s | 71 MiB |
| Room | 128 / 2 | 128 | 0 | 1.14 / 0.84 s | 73 MiB |
| cAnim | 18 / 1 | 18 | 0 | 0.74 / 0.62 s | 71 MiB |

All 241 compared frames had identical RGB and nonzero support; source hashes,
metadata and declared sidecars matched. All four resources are therefore flagged
for review solely due to threshold crossings, not count changes. The processes
ran concurrently with the tests, include Python/SciPy startup and source PNG
decoding, and returned 1 as designed. These small warm-cache samples are not a
benchmark of 87,000 frames. Cost depends on image sizes, PNG decode and storage;
the program holds the report and discovered filename lists in memory, so
memory grows with frame count. Time a representative full corpus and inspect
memory before relying on it for production-scale review.
