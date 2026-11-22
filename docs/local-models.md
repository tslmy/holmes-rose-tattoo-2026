# Local models: LLM, OpenCode and image backends

Phase 0 choices for this machine (M2 Max, 32 GB, ~30 GB free disk after the
image weights). All are recorded in `remaster.toml` and, while unconfirmed,
in `review/queue.md`.

## Text and vision model

**Choice (decision #4/#11): `qwen/qwen3.8-27b` in LM Studio on the second
laptop** (`http://10.0.0.26:1234/v1`; vision-capable, loaded with a 262k
context), for both text and vision. It keeps the LLM off this Mac, so its
32 GB stays free for diffusion and QA no longer has to wait for a batch to
finish. **Fallback:** when that laptop is unreachable or the model is not
listed, `ChatClient.from_config()` switches to `qwen3.5:9b-mlx` in Ollama on
this Mac, served through `http://127.0.0.1:11434/v1` as the alias
`qwen3.5-9b-32k` (same weights, `num_ctx 32768`; no download needed).
Both are set in `[llm]` of `remaster.toml` (`fallback_*` keys).

The alias was made with:

```sh
printf 'FROM qwen3.5:9b-mlx\nPARAMETER num_ctx 32768\n' > Modelfile
ollama create qwen3.5-9b-32k -f Modelfile
```

Why:

| Candidate | Where | Vision | Context | Result (2026-11-14) |
| --- | --- | --- | --- | --- |
| `qwen/qwen3.8-27b` (remote LM Studio) | second laptop | yes | 262k loaded | Briefs for scenes 1, 4, 26 grounded in the image, ~6 s per scene; named only clearly legible signs ("221B", "EXTRA"). Plate-vs-background diff correct (called the hearth fire a "campfire"). Full-frame OCR invented text and looped; enlarged crops read "METROPOLITAN POLICE" but not the small plaque ("NO ENTRANCE"). |
| `qwen3.5:9b-mlx` (9.4B, nvfp4, MLX runner) | Ollama 0.40.16 | yes | 262k native; 32k alias loads at 9.0 GB | Briefs for scenes 1, 4, 26 grounded in the image (read "EXTRA", "221B", "Metropolitan Police"); ~5 s per scene warm. Correctly listed what a plate added over its background (two men, balls, jacket). |
| `mimo-v2.6-distill-qwen-9b-heretic` | LM Studio | no | loaded at 8k | Works through the same client, ~4 s per scene, but text-only: briefs pull lore from the resource text that is not visible (a "canary-yellow cashmere coat", "Wormwood Alley"). Cannot do QA on images. |
| `gpt-oss:20b` | Ollama | no | – | Does not load in Ollama 0.40.16 (`unsupported tensor ... size overflows`); would need a re-pull (13 GB). |

Limits found:

- Free OCR at native 640x480 is unreliable with both models: the full
  scene-4 frame produced invented sign text. Cropped and enlarged 4x, the main sign read correctly
  ("METROPOLITAN POLICE"); a small plaque still came back wrong ("NO
  DETERING" for "No Loitering"). Sign/writing checks must crop by hotspot
  bounds, enlarge, and compare with the extracted description text, and a
  mismatch should send the region to the SR-baseline fallback rather than be
  trusted either way.
- Thinking is off for every call (`reasoning_effort: none` on `/v1`, `think:
  false` natively); with it on, the model can spend its budget on hidden
  reasoning.
- When the local fallback is in use, unload it before diffusion: `ollama stop
  qwen3.5-9b-32k` (and `lms unload --all` for LM Studio). 32 GB does not hold
  a 9 GB LLM and a 20 GB diffusion peak together. The remote model needs no
  unloading.

The second laptop also lists `nvidia/nemotron-3.5-super-vl` (vision, 1M
context) and `meta/muse-glimmer` (vision), not loaded and not yet compared;
candidates if the 27B proves too weak a QA judge.

### Client

`tools/remaster/llm.py` (`ChatClient`) is a standard-library client for any
OpenAI-compatible server, with a native Ollama mode. `ChatClient.from_config(
cfg.section("llm"), "vision")` builds it from `remaster.toml`.
`tools/polish_rosetattoo_prompts.py` now defaults to `--provider openai`
with the URL and model from `[llm]`; `--provider ollama` keeps the old native
path:

```sh
uv run python tools/polish_rosetattoo_prompts.py --input-dir work/extract/rooms \
  --output-dir work/brief --scenes 1 4 26
# LM Studio instead:
uv run python tools/polish_rosetattoo_prompts.py ... \
  --base-url http://localhost:1234/v1 --model mimo-v2.6-distill-qwen-9b-heretic --no-image
```

## OpenCode headless

`opencode` is not on `PATH`, but two copies are installed:

- `/Applications/OpenChamber.app/Contents/Resources/opencode-cli/opencode`
  (v2.2.7, bundled with OpenChamber; **used**), and
- `~/Library/Application Support/ai.opencode.desktop/cli/2.0.32-arm64/opencode-cli`
  (v2.0.32, from the OpenCode desktop app).

The project config `tools/opencode/opencode.json` adds an `lmstudio-remote`
provider for `qwen/qwen3.8-27b` (the default) and an `ollama-local` provider
for `qwen3.5-9b-32k` (`OPENCODE_MODEL=ollama-local/qwen3.5-9b-32k`); it is passed with `OPENCODE_CONFIG`, so the
global `~/.config/opencode/opencode.jsonc` is untouched. The wrapper finds a
binary and runs headless with a private server:

```sh
tools/opencode/opencode-local.sh "Read tools/remaster/jobs.py and summarise recover()"
tools/opencode/opencode-local.sh --auto "Fix the failing test in tools/tests/test_remaster_jobs.py"
OPENCODE_MODEL=ollama-local/qwen3.5-9b-32k tools/opencode/opencode-local.sh --format json "..."
```

Checked: with the local model, a plain reply (24 s including server start
and model load) and a tool-using request (it called `read` on
`remaster.toml` and returned the golden scene list, 29 s); the same
tool-using request against the remote 27B took 20 s. Not yet checked: multi-step edits with `--auto`.
Use it for the QA triage loop and small script edits with tests, as the plan
says; keep engine C++ off it.

## Image backends (mflux)

mflux 0.24.0 is installed as an isolated tool (`uv tool install --python
3.13 mflux==0.24.0`; binaries in `~/.local/bin`). The project's `zimage`
extra still pins mflux 0.18.1 from git for the existing
`tools/img2img/z_image*.py`; bumping it is a queued decision.

The plan's candidates were checked against current mflux docs. Weights are
the pre-quantized `mflux-community` repos, chosen to fit the disk:

| Backend (`remaster.toml`) | Weights | Download | Notes |
| --- | --- | ---: | --- |
| `flux2-klein-edit` | `mflux-community/flux2-klein-9b-mflux-q4` | 9.5 GB | FLUX.2 klein 9B edit; the 4B (Apache-2.0) is the alternative. 9B is under the FLUX non-commercial licence. |
| `qwen21-edit` | `mflux-community/qwen-image-2-1-mflux-q8` | 18.5 GB | Replaces the plan's Qwen-Image-Edit (20B; 29 GB even at 4-bit) with Qwen-Image 2.1 (7.1B + Qwen3-VL encoder), which mflux documents with reference editing, masks and `--strength`. |
| `zimage-controlnet` | `mflux-community/z-image-turbo-controlnet-union-2-1-mflux-q8` | 15.3 GB | Z-Image Turbo + Fun ControlNet Union 2.1; canny/depth/HED hints computed locally. |
| `flux3-klein-edit` | `mflux-community/flux-3-image-klein-mflux-q8` | 9.9 GB | FLUX 3 Image Klein (Oct 2026, 5.6B + Qwen3-VL-4B encoder), added on request: editing with up to 10 references, text rendering. 16 distilled steps; no LoRA training in mflux. FLUX Kommunity License v1.0 (non-commercial, plus commercial output for small users). |

Also seen in current mflux and worth a Phase 1 slot: SeedVR2 for the tiled
4x pass.

Smoke results (one 1024x768 image each from the scene-26 de-dithered plate,
seed 7, LLMs unloaded; time includes model load) are in the table below and
in `work/smoke/<backend>/record.json`:

| Backend | Steps | Time | Peak memory | What the image showed |
| --- | ---: | ---: | ---: | --- |
| `flux2-klein-edit` (9B q4) | 4 | 112 s | 19.8 GB | Clear photoreal restyle (rug, wood, lamps); layout mostly kept, but the bartender vanished and the camera shifted slightly. |
| `flux3-klein-edit` (q8) | 16 | 225 s | 12.9 GB | Closest to the original layout with more real-looking materials and people; the patron at the right edge was dropped. Lowest memory. |
| `qwen21-edit` (q8, `--strength 0.7`) | 25 | 267 s | 25.9 GB | Almost unchanged at this strength: geometry fully kept, little photoreal shift. Needs higher strength in Phase 1. |
| `zimage-controlnet` (q8, canny 0.8) | 20 | 2,209 s | 25.8 GB | Text-to-image under a canny hint: structure followed, but materials, palette and props were reinvented (modern bar, new fireplace). The run swapped heavily (about 10 GB of swap in use), so the time is not representative. |

**Weights deleted afterwards (decision #15):** the Qwen-Image 2.1 and Z-Image
ControlNet weights were removed with `hf cache rm` to free disk; those two
backends re-download (18.5 GB and 15.3 GB plus 6.7 GB) on next use. FLUX 3
Klein and FLUX.2 klein 9B remain.

### Remote image server

A second laptop serves mflux behind an OpenAI-style
`POST http://10.0.0.26:8000/v1/images/generations` (bearer key, kept out of
the repo; `GET /healthz` lists models). Request fields: `prompt`, `model`,
`width`, `height`, `seed`, optional base64 `image` for editing. There is no
steps or strength field, so the server's defaults apply. Profiled on
2026-11-15 with `REMASTER_IMAGE_API_KEY=... uv run python -m
remaster.remote_profile` (same 1024x768 scene-26 input, prompt and seed as
the local smoke; three runs each; the first includes the model load).
Records are in `work/smoke/remote/profile.json`.

| Model | Text-to-image first / warm | Edit first / warm | What the edit showed |
| --- | ---: | ---: | --- |
| `flux3-image-klein` | 17.9 s / 13.0 s | 36.5 s / 29.6 s | Almost identical to the input: lamps and figures redrawn slightly, no photoreal shift. The local 16-step run changed far more, so the server's step or strength default probably differs. |
| `flux2-klein-4b` | 8.2 s / 4.0 s | 12.3 s / 7.3 s | Convincing photoreal restyle (wood grain, rugs, lit lamps) with the layout, figures and props kept. |
| `flux2-klein-9b` | 37.4 s / 10.1 s | 22.8 s / 18.6 s | Similar to the 4B, slightly warmer and richer; layout kept. |

The server reports no memory use. Warm edits are 6 to 30 times faster than
the local runs (225 s for FLUX 3 Klein, 112 s for FLUX.2 9B), and this Mac's
memory stays free. Text-to-image from all three gave plausible Victorian
billiard rooms.

**Decision #14 (2026-11-15):** `remote-flux2-klein-4b` is now the provisional
backend for backgrounds and casting in `remaster.toml`, with local
`flux3-klein-edit` as the fallback when the remote laptop is off. It is the
fastest backend, edits as well as the 9B, and is Apache-2.0. Phase 1's
bake-off can still change it.

All four load and produce an image on this machine. These are single
smoke images, not a bake-off: choosing a backend is Phase 1's job. Run one
again with `uv run python -m remaster.mflux_smoke <backend> [--extra ...]`.
