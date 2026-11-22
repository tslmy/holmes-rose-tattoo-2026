# Ambiguous shared VGS variants

`tools/extract_rosetattoo_vgs_variants.py` is a separate command for the five
shared resources `HTUPRIGH.VGS`, `JTDOWNRG.VGS`, `JTRIGHT.VGS`,
`JTUPRIGH.VGS`, and `NOHAT.VGS`. The original sprite extractor selects the
first matching library; this command requires and reads **both** `VGS.LIB`
and `WALK.LIB` instead. It uses the existing sprite extractor's archive,
palette, and normal-frame decoders. Python 3.10+ and Pillow are required.

## Extraction and audit

Run from the repository root, with the game data installed under `scummvm/`.
Choose a **new** destination with an existing parent directory. The tool
refuses an existing file, directory, or symlink, refuses overlap with the game
data directory, and exclusively creates all output files; it never replaces
an existing extraction. A failed/interrupted run can leave a partial new
directory: inspect it and choose a different new destination for a retry.

The following commands were used for this extraction and audit; substitute a
fresh destination when reproducing them:

```sh
.venv-upscale/bin/python tools/extract_rosetattoo_vgs_variants.py \
  --data-dir scummvm --palette-scene 1 \
  --output-dir extracted/shared-vgs-variants-agent-20261031

.venv-upscale/bin/python tools/extract_rosetattoo_vgs_variants.py \
  --data-dir scummvm --palette-scene 1 \
  --audit-dir extracted/shared-vgs-variants-agent-20261031
```

Audit is read-only. It re-reads both archives, recomputes hashes, re-decodes
every frame and compares directories, manifests, provenance, sizes, offsets,
and RGBA pixels. It uses the same decoder as extraction: this verifies
consistency with the installed data, **not** an independent ScummVM decode,
runtime archive selection, visual quality, or in-game behavior.

## Engine and upscaler contract

The identity is the full lowercase `hashlib.md5` of the **decompressed VGS
resource bytes**, using `usedforsecurity=False`: MD5 here is a content
identity for engine compatibility, not a security check. Byte-identical
resources from both archives share a single variant and retain both archive
provenances in `metadata.json`; distinct bytes with the same digest are
rejected rather than silently conflated. The stored and decompressed resource
hashes and sizes are recorded per archive. No raw game bytes are committed.

For `skipPal=false`, `animImages=false` only, the extracted source tree is:

```text
<resource>_vgs/variants/<32-lowercase-hex-md5>-s0a0/
  frame_000.png
  ...
  metadata.json
```

The top-level `manifest.json` summarizes all variants. Metadata includes
source archives, the digest, decoding flags, palette choice, and frame records
compatible with the existing upscaler. For example, an unmodified recursive
upscaler run produces the same nested paths with `frame_000@2x.png`, etc.
Do not pass top-level `--resources` names: those directories do not themselves
contain frame metadata. The upscaler itself can overwrite existing outputs,
so reserve an empty output directory with `mkdir` first and **do not run it**
if `mkdir` fails:

```sh
mkdir enhanced/shared-vgs-variants-agent-20261031
.venv-upscale/bin/python tools/upscale_rosetattoo_sprites.py \
  --input-dir extracted/shared-vgs-variants-agent-20261031 \
  --output-dir enhanced/shared-vgs-variants-agent-20261031 \
  --scale 2 --mode photo --alpha-mode soft --device mps
```

This command requires the existing ESRGAN dependencies and model checkpoint;
see `docs/esrgan-setup.md`. The upscaler generates inferred detail and is not
pixel-identical to the source. Its output is not installed into any pack by
this tool. `-s0a0` must not be used for other loading modes, and the engine
must actually support digest-qualified variants for these files to be used.
The run above produced 350 frames in 10 variant directories. All were checked
for paths, RGBA dimensions, scaled offsets, embedded source-pixel SHA-256, and
the model SHA-256 `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1`.
These checks do not establish visual quality or correct in-game rendering.

`--palette-scene 1` uses `RES01.RRM` as the fallback for resources without an
embedded palette, as in the existing shared sprite workflow. These five
installed resources have no embedded palette. This fixed RGB approximation
cannot reproduce different active room palettes, dynamic palette changes,
fades, or other runtime effects; matching source identity does not guarantee
correct colors.

## Installed data audit

For the installed game data used here, both archive entries differ for every
resource: **5 resources, 10 variants, 350 frames**, all audited. Each of the
first four variants has 20 frames per archive; `NOHAT` has 95 per archive.
These are content identities for this particular game installation, not
universal constants for every edition or localization.

| Resource | VGS.LIB decompressed MD5 | WALK.LIB decompressed MD5 |
| --- | --- | --- |
| HTUPRIGH.VGS | `f1fc49f52b79e268d8285deeb2f0205a` | `1c556aa76b83313cbc35b72c673a33dd` |
| JTDOWNRG.VGS | `01d0082f85ea94cd8add3221e8f1fc9c` | `830b73c40d4ee7b21a4d77ca4980960d` |
| JTRIGHT.VGS | `cd5e67c69caefccf42422e03a2f43200` | `c59de1cd591db59afbde383cc6eb2a9c` |
| JTUPRIGH.VGS | `f3f3a369d447a0fe6d3dab395d35382b` | `61f95194d5c4a7a2d0fc9458a7e3332d` |
| NOHAT.VGS | `3f34012e8889b70bdc249172cfd5d3a0` | `7be439a4e52f46602b83a83056733054` |

Run the focused synthetic tests (the upscaler integration test also needs
NumPy):

```sh
.venv-upscale/bin/python -m unittest discover -s tools/tests -p test_vgs_variants.py -v
```
