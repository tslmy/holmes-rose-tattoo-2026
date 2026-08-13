#!/usr/bin/env python3
"""Run one GPT Image editing pass over every extracted room background.

This is an optional cloud backend. It deliberately writes outside the tracked
tree by default and records request metadata so the pass can be audited and
resumed without relying on a ChatGPT conversation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mask_for_openai(source: Image.Image, protect_path: Path, path: Path) -> None:
    """Create an RGBA mask: opaque pixels are preserved, transparent edited."""
    protect = Image.open(protect_path).convert("L").resize(source.size)
    rgba = Image.new("RGBA", source.size, (255, 255, 255, 0))
    rgba.putalpha(protect)
    rgba.save(path)


def _prompt(scene_dir: Path) -> str:
    brief = ROOT / "profiles" / "neural" / "prompt-briefs" / scene_dir.name / "visual_brief.txt"
    if brief.exists():
        detail = brief.read_text(encoding="utf-8").strip()
    else:
        detail = (scene_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    return (
        "Restyle this 1990s point-and-click adventure game room as a highly "
        "detailed photorealistic period film set. Preserve the exact camera "
        "angle, perspective, room layout, architectural boundaries, doors, "
        "windows, furniture silhouettes, and object placement. Do not add or "
        "remove interactable objects. Preserve all protected regions exactly; "
        "only repaint unprotected background interiors. Avoid readable text "
        "unless it is already present.\n\nScene description:\n" + detail
    )


def process_scene(client, scene_dir: Path, output_dir: Path, model: str, quality: str) -> dict:
    from openai import APIError

    source_path = scene_dir / "background.png"
    protect_path = scene_dir / "protect_mask.png"
    scene_out = output_dir / scene_dir.name
    scene_out.mkdir(parents=True, exist_ok=True)
    output_path = scene_out / "background_gpt.png"
    metadata_path = scene_out / "generation.json"
    if output_path.exists() and metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    with Image.open(source_path) as source:
        mask_path = scene_out / "openai_mask.png"
        _mask_for_openai(source, protect_path, mask_path)
    try:
        with source_path.open("rb") as image_file, mask_path.open("rb") as mask_file:
            result = client.images.edit(
                model=model,
                image=image_file,
                mask=mask_file,
                prompt=_prompt(scene_dir),
                size="auto",
                quality=quality,
                output_format="png",
            )
    except APIError:
        raise

    image_bytes = __import__("base64").b64decode(result.data[0].b64_json)
    output_path.write_bytes(image_bytes)
    record = {
        "scene_id": int(scene_dir.name.split("_")[1]),
        "backend": "openai-images",
        "model": model,
        "quality": quality,
        "source": str(source_path),
        "source_sha256": _sha256(source_path),
        "protect_mask": str(protect_path),
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
        "mask_semantics": "opaque=preserve, transparent=allow edit",
    }
    metadata_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "extracted" / "rosetattoo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated" / "openai-image-pass")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--quality", choices=["low", "medium", "high", "auto"], default="medium")
    parser.add_argument("--scenes", type=int, nargs="*", default=None)
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; ChatGPT Plus and API billing are separate.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Install the optional OpenAI dependency with: uv sync --extra openai") from exc

    client = OpenAI()
    scene_dirs = sorted(args.input_dir.glob("scene_*/"))
    if args.scenes is not None:
        wanted = set(args.scenes)
        scene_dirs = [path for path in scene_dirs if int(path.name.split("_")[1]) in wanted]
    if not scene_dirs:
        raise SystemExit(f"No scenes found under {args.input_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for scene_dir in scene_dirs:
        if not (scene_dir / "background.png").exists() or not (scene_dir / "protect_mask.png").exists():
            raise SystemExit(f"Missing background or protect mask in {scene_dir}")
        print(f"generating {scene_dir.name} ...", flush=True)
        records.append(process_scene(client, scene_dir, args.output_dir, args.model, args.quality))
    manifest = {
        "backend": "openai-images",
        "model": args.model,
        "quality": args.quality,
        "scene_count": len(records),
        "scenes": records,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"completed {len(records)} scenes; manifest: {args.output_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
