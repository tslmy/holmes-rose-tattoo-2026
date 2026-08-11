#!/usr/bin/env python3
"""Create visual-only Stable Diffusion briefs from raw Rose Tattoo resource text."""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def scene_dirs(root: Path, selected: list[int] | None) -> list[Path]:
    if selected:
        return [root / f"scene_{scene_id:02d}" for scene_id in selected]
    return sorted(p for p in root.glob("scene_*") if p.is_dir())


def load_metadata(scene_dir: Path) -> dict[str, Any]:
    metadata_path = scene_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code} from {url}: {body}") from err


def image_file_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def strip_thinking(text: str) -> str:
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return stripped or text.strip()


def trim_raw_prompt(raw_prompt: str, max_chars: int) -> str:
    if len(raw_prompt) <= max_chars:
        return raw_prompt
    header_lines = []
    detail_lines = []
    for line in raw_prompt.splitlines():
        if line.strip().startswith("- "):
            detail_lines.append(line)
        elif len("\n".join(header_lines)) < 1200:
            header_lines.append(line)
    text = "\n".join(header_lines).strip()
    for line in detail_lines:
        candidate = f"{text}\n{line}" if text else line
        if len(candidate) > max_chars:
            break
        text = candidate
    return text + "\n[Raw source truncated for prompt polishing.]"


def fallback_visual_brief(scene_name: str, raw_prompt: str) -> str:
    static_visual_terms = (
        "architecture",
        "bench",
        "book",
        "bottle",
        "brick",
        "cart",
        "chair",
        "chemical",
        "cigar",
        "cloak room",
        "coal",
        "costermonger",
        "desk",
        "door",
        "embankment",
        "fire",
        "fireplace",
        "floor",
        "fog",
        "fruit",
        "gate",
        "glass",
        "grate",
        "granite",
        "hook",
        "hotel",
        "kiosk",
        "lamp",
        "lantern",
        "letter",
        "mantle",
        "metal",
        "newspaper",
        "obelisk",
        "pavement",
        "portrait",
        "produce",
        "rack",
        "rail",
        "river",
        "scuttle",
        "settee",
        "shelf",
        "sideboard",
        "sign",
        "sofa",
        "stairs",
        "step",
        "stone",
        "table",
        "wall",
        "water",
        "window",
        "wood",
    )
    story_patterns = (
        "appears composed",
        "anarchist",
        "biased",
        r"\bblood\b",
        r"\bbloodstain",
        "bomb",
        r"\bbody\b",
        r"\bboy\b",
        r"\bbullet\b",
        r"\bchild\b",
        r"\bchildren\b",
        r"\bcharacters\b",
        "c.i.d",
        "clientele",
        r"\bconstable\b",
        r"\bconstables\b",
        r"\bcrime\b",
        r"\bcrimes\b",
        r"\bcriminal\b",
        r"\bcustomer\b",
        "demeanor",
        r"\bdog\b",
        "dreary",
        "evidence",
        r"\bdoctor\b",
        "forbes",
        r"\bhe\b",
        r"\bhis\b",
        r"\bher\b",
        "holmes",
        "guaiacum",
        r"\bimplied\b",
        "jonas",
        "mother",
        r"\bmurder\b",
        "needhem",
        r"\bold man\b",
        "p.c.",
        "papa",
        "passers-by",
        "pestiferous",
        r"\bpeople\b",
        "perspicacity",
        "preposterous",
        "prosecution",
        "rank breath",
        r"\btraffic\b",
        "loiter",
        r"\bprostitutes\b",
        "regards",
        "ready to",
        r"\bsoul\b",
        "spatter",
        "suffragette",
        r"\btenant\b",
        "torture",
        "virgil",
        "watson",
        r"\bweapons?\b",
        "wiggins",
        r"\bwomen\b",
    )
    lines = []
    for line in raw_prompt.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        lower = line.lower()
        if any(re.search(pattern, lower) for pattern in story_patterns):
            continue
        if not any(term in lower for term in static_visual_terms):
            continue
        lines.append(line[2:])
    if lines:
        details = "; ".join(lines[:8])
    else:
        details = (
            "use the source image as the authority for all visible architecture, "
            "street furniture, props, signage shapes, surfaces, weather, and lighting"
        )
    return (
        f"Scene: {scene_name}. Faithful visual brief for img2img restoration: "
        f"{details}. Preserve only visible static background details; do not add people, inventory objects, "
        "new readable text, new doors, or story-only details."
    )


def sanitize_visual_brief(text: str) -> str:
    scrub_patterns = (
        r"\b1996\b",
        r"\b\d{3,4}x\d{3,4}\b",
        r"\badventure\b",
        r"\badventure game\b",
        r"\bbackground art\b",
        r"\bgame art\b",
        r"\bhand[ -]painted\b",
        r"\blow resolution\b",
        r"\bmuted colors?\b",
        r"\bpixel art\b",
        r"\bpoint-and-click\b",
        r"\bresolution\b",
        r"\bstyle\b",
        r"\btexture\b",
        r"\bvintage game\b",
    )
    for pattern in scrub_patterns:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    banned_patterns = (
        r"\banarchist\b",
        r"\batmosphere\b",
        r"\bblood\b",
        r"\bbloodstain",
        r"\bbody\b",
        r"\bbullet\b",
        r"\bchild\b",
        r"\bchildren\b",
        r"\bcorpse\b",
        r"\bdim\b",
        r"\bdimly\b",
        r"\bdreary\b",
        r"\bforensic\b",
        r"\bfigure\b",
        r"\bfigures\b",
        r"\bman standing\b",
        r"\bno visible characters\b",
        r"\bstanding guard\b",
        r"\buniformed man\b",
        r"\bvisible characters\b",
        r"\bgloom",
        r"\bhazy\b",
        r"\bhilt\b",
        r"\bholmes\b",
        r"\bimplied\b",
        r"\bjonas\b",
        r"\blate afternoon\b",
        r"\blow-angle\b",
        r"\bmoody\b",
        r"\bmurder\b",
        r"\bold woman\b",
        r"\bofficial\b",
        r"\bneedhem\b",
        r"\bovercast\b",
        r"\bpestiferous\b",
        r"\bprostitut",
        r"\brank breath\b",
        r"\bspatter",
        r"\bspattered\b",
        r"\bstain\b",
        r"\bshadow",
        r"\bsabre\b",
        r"\bsaber\b",
        r"\bsunlight\b",
        r"\bvirgil\b",
        r"\bviscous\b",
        r"\bviscous splodge",
        r"\bwatson\b",
        r"\bweapons?\b",
        r"\bwheelbarrow\b",
        r"\bwiggins\b",
        r"\bwoman\b",
        r"\byoung woman\b",
    )
    pieces = re.split(r"(?<=[.!?])\s+|;\s*|,\s*", text.strip())
    kept = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        lower = piece.lower()
        if any(re.search(pattern, lower) for pattern in banned_patterns):
            continue
        kept.append(piece)
    words = []
    for piece in kept:
        piece_words = piece.split()
        if len(words) + len(piece_words) > 80:
            break
        words.extend(piece_words)
    cleaned = " ".join(words).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned:
        return cleaned
    fallback = text
    for pattern in banned_patterns:
        fallback = re.sub(pattern, " ", fallback, flags=re.IGNORECASE)
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback or text.strip()


def polish_with_ollama(
    api_url: str,
    model: str,
    ollama_api: str,
    scene_name: str,
    raw_prompt: str,
    image_path: Path | None,
    max_source_chars: int,
    timeout: int,
) -> str:
    raw_prompt = trim_raw_prompt(raw_prompt, max_source_chars)
    system = (
        "You convert raw adventure-game resource text and a source image into a compact visual inventory for Stable Diffusion img2img. "
        "The source image, ControlNet edges, and original composition are authoritative. "
        "Keep only static details clearly visible in the background plate: architecture, furniture, props, surfaces, "
        "sign shapes, floor/wall boundaries, and puzzle-relevant objects. "
        "Remove biographies, dialogue, invisible/off-screen characters, actions, plot explanations, UI text, "
        "inventory lore, and any instruction to create people unless the text clearly describes a static visible figure. "
        "Do not invent new objects. If an extracted detail is not clearly visible in the image, omit it. "
        "Do not mention character names unless they are printed signage. "
        "Avoid crime-story words such as blood, murder, weapon, bullet, forensic, anarchist, torture, beggar, or prostitute; "
        "describe only neutral visible materials and shapes. "
        "Avoid mood, atmosphere, time of day, darkness, shadows, cinematic terms, and color grading. "
        "Return only comma-separated noun phrases, 35-80 words total, no full sentences, no bullets, no markdown."
    )
    user = (
        f"Scene name: {scene_name}\n\n"
        "Raw extracted text:\n"
        f"{raw_prompt}\n\n"
        "Write the compact visual inventory now."
    )
    images = [image_file_to_base64(image_path)] if image_path else None
    if ollama_api == "chat":
        user_message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_message["images"] = images
        response = post_json(
            f"{api_url.rstrip('/')}/api/chat",
            {
                "model": model,
                "stream": False,
                "think": False,
                "messages": [
                    {"role": "system", "content": system},
                    user_message,
                ],
                "options": {
                    "temperature": 0.0,
                    "top_p": 0.8,
                    "num_ctx": 8192,
                    "num_predict": 220,
                },
            },
            timeout,
        )
        text = strip_thinking(response.get("message", {}).get("content", ""))
    else:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "think": False,
            "system": system,
            "prompt": user,
            "options": {
                "temperature": 0.0,
                "top_p": 0.8,
                "num_ctx": 8192,
                "num_predict": 220,
            },
        }
        if images:
            payload["images"] = images
        response = post_json(
            f"{api_url.rstrip('/')}/api/generate",
            payload,
            timeout,
        )
        text = strip_thinking(response.get("response", ""))
    if not text:
        raise RuntimeError("Ollama returned an empty prompt")
    return sanitize_visual_brief(" ".join(text.split()))


def output_scene_dir(output_root: Path, scene_id: int) -> Path:
    return output_root / f"scene_{scene_id:03d}"


def find_manual_brief(root: Path | None, scene_id: int) -> Path | None:
    if not root:
        return None
    for dirname in (f"scene_{scene_id:03d}", f"scene_{scene_id:02d}", f"scene_{scene_id}"):
        path = root / dirname / "visual_brief.txt"
        if path.exists():
            return path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "prompt-briefs")
    parser.add_argument("--scenes", type=int, nargs="*")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--provider", choices=["ollama", "fallback"], default="ollama")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="qwen3.5:9b-mlx")
    parser.add_argument("--ollama-api", choices=["chat", "generate"], default="chat")
    parser.add_argument("--fallback-on-error", action="store_true")
    parser.add_argument("--api-timeout", type=int, default=600)
    parser.add_argument("--max-source-chars", type=int, default=6000)
    parser.add_argument(
        "--manual-brief-dir",
        type=Path,
        help=(
            "Directory containing scene_NNN/visual_brief.txt overrides for "
            "fragile scenes. Overrides are copied after sanitization and do "
            "not call the LLM."
        ),
    )
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="Do not send the scene background image to vision-capable Ollama models.",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for scene_dir in scene_dirs(args.input_dir, args.scenes):
        scene_id = int(scene_dir.name.split("_", 1)[1])
        metadata = load_metadata(scene_dir)
        scene_name = metadata.get("scene_name", f"Scene {scene_id:02d}")
        raw_prompt_path = scene_dir / "prompt.txt"
        image_path = scene_dir / "background.png"
        if not raw_prompt_path.exists():
            raise FileNotFoundError(raw_prompt_path)
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        scene_output_dir = output_scene_dir(args.output_dir, scene_id)
        brief_path = scene_output_dir / "visual_brief.txt"
        metadata_path = scene_output_dir / "metadata.json"
        if args.skip_existing and brief_path.exists():
            brief = brief_path.read_text(encoding="utf-8").strip()
            skipped = True
        else:
            raw_prompt = raw_prompt_path.read_text(encoding="utf-8")
            started = time.monotonic()
            provider_used = args.provider
            manual_brief_path = find_manual_brief(args.manual_brief_dir, scene_id)
            if manual_brief_path:
                brief = manual_brief_path.read_text(encoding="utf-8").strip()
                provider_used = "manual"
            elif args.provider == "ollama":
                try:
                    brief = polish_with_ollama(
                        args.ollama_url,
                        args.ollama_model,
                        args.ollama_api,
                        scene_name,
                        raw_prompt,
                        None if args.no_image else image_path,
                        args.max_source_chars,
                        args.api_timeout,
                    )
                except Exception:
                    if not args.fallback_on_error:
                        raise
                    brief = fallback_visual_brief(scene_name, raw_prompt)
                    provider_used = "fallback"
            else:
                brief = fallback_visual_brief(scene_name, raw_prompt)
            elapsed = round(time.monotonic() - started, 3)
            scene_output_dir.mkdir(parents=True, exist_ok=True)
            brief_path.write_text(brief + "\n", encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "scene_id": scene_id,
                        "scene_name": scene_name,
                        "provider": provider_used,
                        "requested_provider": args.provider,
                        "ollama_model": args.ollama_model if provider_used == "ollama" else None,
                        "manual_brief": str(manual_brief_path) if manual_brief_path else None,
                        "source_prompt": str(raw_prompt_path),
                        "visual_brief": str(brief_path),
                        "elapsed_seconds": elapsed,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            skipped = False
        results.append({"scene_id": scene_id, "scene_name": scene_name, "skipped": skipped})
        print(f"scene {scene_id:03d}: {'skipped' if skipped else 'wrote'} {brief_path}")

    available_briefs = sorted(args.output_dir.glob("scene_*/visual_brief.txt"))
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "provider": args.provider,
                "ollama_model": args.ollama_model if args.provider == "ollama" else None,
                "processed_scene_count": len(results),
                "available_scene_count": len(available_briefs),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
