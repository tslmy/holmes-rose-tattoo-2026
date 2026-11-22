# Palette-qualified walk sprite sources

`tools/extract_rosetattoo_palette_walks.py` extracts native-size PC Rose Tattoo
walk images for each distinct **initial room palette projection**. It is a
separate extractor: it does not change older scripts, engine files, packs, or
game data. It requires Python 3.10+ and Pillow. No model or diffusion is used.

## Run safely

From the repository root, choose a **new** destination beneath an ignored
directory such as `extracted/`; its parent must already exist. Existing files,
directories and symlinks are refused, as is overlap with the game directory.
All files are created exclusively. A failed/interrupted run can leave a partial
new directory; inspect it and choose a different new destination on retry.

The commands used here were:

```sh
.venv-upscale/bin/python tools/extract_rosetattoo_palette_walks.py \
  --data-dir scummvm --output-dir extracted/palette-walk-pilot-terra-20261031 \
  --scenes 1 26 36 --resources NOHAT.VGS OPIUM.VGS
.venv-upscale/bin/python tools/audit_rosetattoo_palette_walks.py \
  --data-dir scummvm --source-dir extracted/palette-walk-pilot-terra-20261031

.venv-upscale/bin/python tools/extract_rosetattoo_palette_walks.py \
  --data-dir scummvm --output-dir extracted/palette-walk-all-terra-20261031
.venv-upscale/bin/python tools/audit_rosetattoo_palette_walks.py \
  --data-dir scummvm --source-dir extracted/palette-walk-all-terra-20261031

.venv-upscale/bin/python -m unittest discover -s tools/tests -p test_palette_walk_extraction.py -v
```

Use fresh destination names when repeating extraction. `--scenes` limits the
room palettes, not the set of sprites; without it all case-insensitively
discovered `RES*.RRM` rooms are used. `--resources` accepts a case-insensitive
subset of names found in the walk SEQs or listed as extras below. Unknown,
repeated, missing or unsafe names fail rather than being silently skipped.
Both `VGS.LIB` and `WALK.LIB` are required; missing referenced resources,
malformed records, collisions, and truncated frames are errors.

The read-only audit recalculates source identities, all palette groups, and
the entire manifest, then checks every sidecar, metadata record, PNG pixel,
size, offset, and expected path. It uses the extractor's decoder, so it checks
reproducibility and completeness, **not an independent engine decode**. It
does not validate visual quality, in-game lookup, or temporal behavior.

## Selection and identity

The extractor parses the count and variable-length `WalkSequence::load` records
in every Tattoo-format `.SEQ` found in the two libraries, including separately
stored copies, and takes the union of their actual nine-byte VGS names. It
records each archive/SEQ reference in metadata. `TINYWALK.SEQ` is explicitly
excluded: it is a legacy fixed-layout sequence, not that record format. Real
SEQ files can have unused trailing bytes; the engine reads the declared count.

Six conservative walk-looking directional files not referenced by the installed
SEQs are additionally included: `3TDOWNRG.VGS`, `3TRIGHT.VGS`,
`3TUPRIGH.VGS`, `GTSDOWNR.VGS`, `GTSRIGHT.VGS`, and `GTSUPRIG.VGS`. Their
presence is not proof of actual gameplay use. `--resources` can select only
these known extras or parsed SEQ references, not arbitrary archive content.

Every selected name is searched in both archives. Byte-identical
**decompressed** streams deduplicate while retaining both archive provenances;
different bytes get different identities. This includes both archive streams of
`HTUPRIGH`, `JTDOWNRG`, `JTRIGHT`, `JTUPRIGH`, and `NOHAT`.
For those five names, selection requires an entry in **both** libraries;
missing either entry is an error, even if the other archive has the resource.
Archive names and filesystem names are matched case-insensitively, with
ambiguous case duplicates rejected. Loose VGS files and other libraries are
not searched. Identity is `hashlib.md5(decompressed_bytes,
usedforsecurity=False).hexdigest()`; MD5 is an engine-compatible content key,
not a security guarantee, and same-name digest collisions are rejected.

For each identity **every frame** is decoded in `skipPal=false`,
`animImages=false` mode, and its opaque native index values are unioned.
Index 255 is the transparent key; index 0 is opaque and must be included when
used. No palette range is guessed. Each resource is paired conservatively with
all selected scenes, then scenes with identical relevant colors are grouped.
The scene list is a palette provenance list, **not** an assertion that the
character appears in or can reach those rooms.

## Format contract

```text
<resource>_vgs/variants/<resource-md5>-s0a0/palettes/<projection-md5>/
  frame_000.png
  ...
  metadata.json
  palette.bin
manifest.json
```

The full lowercase projection MD5 hashes **exactly** the `palette.bin` bytes:

1. Eight ASCII bytes `b"RT-PAL1\0"`.
2. 32 bitset bytes: index `i` sets bit `i % 8` of byte `i // 8`. Bit 255 is
   always clear. All opaque indices used in any frame, including 0, are set.
3. Three bytes of active-palette RGB for each set index, in ascending index
   order. Each 6-bit channel from the room palette is converted with the
   engine's integer formula `x * 255 // 63`.

The sidecar has no padding or trailing data. Metadata contains
`sidecars: ["palette.bin"]`, the hashes, mode, archive/SEQ provenance, exact
used indices, palette source scenes, original frame dimensions and unsigned
offsets, alpha policy and limitations. PNGs are RGBA, index 255 transparent,
and other indices opaque. RGB for transparent pixels is set to zero.

`ImageFile::loadPalette` can read an embedded VGS palette, but Tattoo person
drawing uses `SHtransBlitFrom` into the indexed active scene buffer
(`tattoo_people.cpp`, `tattoo_scene.cpp`, `image_file.cpp`). Therefore these
PNGs always use the selected RRM palette even if the VGS has an embedded
palette. This is covered by a synthetic embedded-palette test. Only initial
RRM palettes are represented: dynamic changes, fades, special effects, other
decode modes, scene reachability and non-walk assets remain out of scope.

The existing recursive upscaler and pack assembler must support `sidecars`
and preserve this nested tree; merely extracting it does not install a pack or
make an engine use these keys. A compatible runtime must calculate the same
resource/decode and palette keys and fail safely when no exact projection is
available. Dynamic palettes are not approximated by a different initial
projection. Test in-game separately. Never assume a fixed scene palette is
correct in another room.

## Local results

The installed data contains 19 Tattoo SEQ archive entries referencing 35
names. Their five additional distinct same-name streams give 40 identities,
436 projections and 17,180 frame paths across the 80 available room palettes.
The six extra names/identities add 60 projections and 1,200 frame paths. Full
result: **41 names, 46 identities, 496 projections, 18,380 frame paths**, with
80 scenes (not numbered consecutively). `QTRIGHT` alone has 52 projections,
while `TOBY` has 4; the other 44 identities have 10 each. This is why a
fixed palette-index range or a single palette per sprite is insufficient.
45 of the 46 identities use index 0. No selected installed identity has an
embedded palette; the synthetic test covers that separate case.

The pilot contains 3 scenes, 2 names, 3 identities, 9 projections and 855
frame paths. Both pilot and full outputs passed the read-only audit above.
As an additional local comparison, the existing independent archive and room
frame decoders were run on all **46 identities and 1,811 native frames**,
using an injective test palette: decoded bytes, every RGBA pixel, opaque index
sets, dimensions, encodings and offsets agreed. These comparisons do not run
the actual engine. A stub-only 2x upscaler check additionally verified 95
frames, dimensions, scaled offsets, nested path and copied sidecar; no model was
used and those fake nearest-neighbor test frames must not be used in a pack.
