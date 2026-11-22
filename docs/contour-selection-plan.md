# Contour selection-plan validation

`tools/validate_rosetattoo_contour_plan.py` is a standalone Python 3.10+
standard-library validator. It reads a **trusted**, completed, source-backed
contour audit and the three current trees; it does not copy, modify or select
assets in a pack. Its output is a plan for a separate, later assembler.

## Decision policy

* Empty frame issue lists and the expected `threshold_128_crossing` are allowed.
* If any frame's `threshold_128_topology_changed` is present and consistent
  with its reported counts, the **entire resource** is listed for baseline
  fallback. Other frames of that sequence are not switched independently.
* Any other frame or resource issue, global error, nonzero RGB/support change,
  missing frame, unverified source, missing hash or inconsistency is a hard
  failure; no plan is produced.

The candidate must declare 2x `native-alpha-scale4-box` refinement with
`preserve_support: true`; a baseline that already declares alpha refinement is
rejected. The known `preserve-font-alpha`/`lanczos-alpha` exception is accepted
only with its no-op settings and unchanged reported opacity and threshold
metrics. This accommodates the bitmap-font resources in the existing tree.
The audit itself still determines the pixel and topology metrics.

## Command and integration

```sh
python3 -B tools/validate_rosetattoo_contour_plan.py \
  --audit validation/canimations-contour-bound-audit-20261031.json \
  --baseline enhanced/canimations-remaster-20261031 \
  --candidate enhanced/canimations-contour-staging-20261031 \
  --source extracted/canimations-remaster-20261031 \
  --plan validation/contour-selection-canimations-new.json
```

`--plan` is optional; without it the validated result is printed as JSON.
The plan path must be new, outside all input trees, and distinct from the
audit. It is published atomically with an exclusive hard link; the filesystem
must support hard links. Existing files, symlinks, input assets and the audit
are never overwritten. An interrupted process can leave its private temporary
file behind. Exit 0 means the plan passed these checks, 1 means validation or
publication failed, and argparse reports invalid arguments with exit 2.

For a parent assembler, the reusable function returns an immutable
`SelectionPlan` containing canonical `baseline_root`, `candidate_root`,
`source_root`, `fallback_resources` (tuple of relative resource paths) and
the exact `audit_sha256`:

```python
from pathlib import Path
from validate_rosetattoo_contour_plan import validate_audit

plan = validate_audit(Path("validation/audit.json"), Path("enhanced/baseline"),
                      Path("enhanced/contour"), Path("extracted/source"))
fallbacks = {resource: plan.baseline_root for resource in plan.fallback_resources}
# A caller may pass `fallbacks` to the assembler only after its own preflight.
```

Import from `tools/` as appropriate for the caller. A root-level resource `.`
that needs fallback is rejected because the existing assembler requires a
nonempty resource-relative name; audit the parent directory instead.

## What is verified

The report must have `schema_version: 1`, `file_hashes_version: 1`,
`source_verification: "decoded_rgba"`, and canonical inputs matching the
three requested, distinct trees. Old reports without file hashes are not
accepted. The validator reads the exact audit bytes once, returns their SHA-256,
and rejects malformed JSON, duplicate keys and non-finite numbers.

It independently walks all three roots, rejects symlinks and non-regular or
orphan files, and requires identical **resource frame-manifest sets** across
all three trees and the report. Source supersets accepted by the audit are not
accepted here. It validates metadata, frame indices/order, geometry and
filenames, declared sidecars, and hashes every baseline/candidate/source PNG,
resource `metadata.json` and declared sidecar against the report. It also
compares declared sidecar bytes across all three trees. Missing, extra or
unreadable files and incorrect or absent hashes fail closed. Frame issue lists,
pixel/threshold counts, topology flags, opacity calculations, resource
aggregates, suspect lists and global totals must agree with one another.

Baseline and candidate scene-summary sets must match and their current JSON
contents must agree. Source scene summaries are cross-checked when a matching
enhanced summary exists; a source-only summary is allowed and its scene ID and
recognized room-list/count fields are checked against source manifests when
available. Known source-root VGS, palette-walk and room-extraction manifests
are checked against their current resource sets, identities and counts. Palette
projections must partition the listed scenes for each resource identity; room
root entries must match the corresponding scene summaries. Unknown root formats
and unexplained files are rejected. The audit did **not** fingerprint scene
summaries or the root manifest, so these checks compare their current state,
not their state when the audit ran.

## Trust and runtime limitations

The validator does **not** decode images, recompute topology or independently
prove source provenance. It verifies hashes and internal report consistency,
not authenticity: an attacker able to fabricate a self-consistent report and
hashes can misstate the measurements. The audit must come from a trusted
process; preserve or authenticate its returned SHA-256 separately. A plan
file is not an authorization or a substitute for revalidation.

Filesystem reads do not create an atomic multi-file snapshot. Keep all input
trees and the audit immutable during validation **and** until the assembler has
finished reading them, or repeat validation immediately before use and employ
an external snapshot/locking mechanism. Scene-summary/root-manifest changes
that happened before validation cannot be compared to the audit snapshot.
Component/hole counts also miss shape and position changes, so a valid plan
cannot prove visual, palette, composition or gameplay safety; in-engine
review and playtesting remain necessary.

## Verification run

On 2026-10-31 the completed source-backed cAnim bound audit above validated
767 resources and 33,189 frames, with the single whole-resource fallback
`scene_044/canim_004` (frame 9 changed hole counts). The audit SHA-256 was
`38dd88521a964757f3c50fb8f7c30c5db82b05553df3c1c81680f2ad835082e2`;
the ignored local plan is `validation/contour-selection-canimations-terra-20261031.json`.
On an Apple M2 Max, that run took 30.82 seconds wall time and about 200 MiB
maximum RSS; this is not a benchmark of every full corpus. Watson, room and
cAnim pilots, plus the 3,040-frame shared and 350-frame VGS bound audits, also
validated without fallback. These are validation observations, not approval to
roll out a pack.

Run the standalone tests with:

```sh
python3 -B -m unittest tools.tests.test_contour_selection_plan -v
```
