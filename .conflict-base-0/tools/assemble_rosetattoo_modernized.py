#!/usr/bin/env python3
"""Assemble a new 2x override pack from completed background and sprite runs.

Output must not already exist. This deliberately does not alter an existing
playable pack, game data, or save files. Use audit_rosetattoo_pack.py against
the original extraction manifests after assembly to verify coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from sprite_metadata import resource_metadata_paths


def assemble(backgrounds: Path, sprite_roots: list[Path], destination: Path, scale: int,
             fallbacks: dict[str, Path] | None = None, contour_audits: list[dict] | None = None) -> dict:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing pack: {destination}")
    if not backgrounds.is_dir() or not sprite_roots or any(not root.is_dir() for root in sprite_roots):
        raise ValueError("A background directory and at least one valid sprite root are required")
    background_paths = sorted(backgrounds.glob(f"scene_*/background@{scale}x.png"))
    if not background_paths:
        raise ValueError("No scale-matching background overrides found")

    # Validate all inputs and collisions before creating the destination.
    copies: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    frame_count = 0
    resources = 0
    provenance: Counter[tuple] = Counter()
    requested_fallbacks = fallbacks or {}
    used_fallbacks: list[dict] = []
    for name, root in requested_fallbacks.items():
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts or "\\" in name or relative.as_posix() != name:
            raise ValueError(f"Invalid fallback resource path: {name}")
        if not root.is_dir():
            raise ValueError(f"Fallback source does not exist: {root}")
    output_root = destination.resolve()
    for input_root in (backgrounds, *sprite_roots, *requested_fallbacks.values()):
        path = input_root.resolve()
        if output_root == path or output_root.is_relative_to(path) or path.is_relative_to(output_root):
            raise ValueError(f"Output and input trees must not overlap: {input_root}")
    for source in background_paths:
        if not re.fullmatch(r"scene_\d+", source.parent.name):
            raise ValueError(f"Invalid scene directory: {source.parent}")
        relative = Path(source.parent.name) / source.name
        if relative in seen:
            raise ValueError(f"Duplicate destination: {relative}")
        seen.add(relative)
        copies.append((source, relative))
    for root in sprite_roots:
        for manifest_path in resource_metadata_paths(root):
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("complete") is not True or metadata.get("scale") != scale:
                raise ValueError(f"Incomplete or incompatible sprite run: {manifest_path}")
            relative = manifest_path.parent.relative_to(root)
            fallback_root = requested_fallbacks.get(relative.as_posix())
            if fallback_root is not None:
                fallback_manifest = fallback_root / relative / "metadata.json"
                fallback_metadata = json.loads(fallback_manifest.read_text(encoding="utf-8"))
                candidate_identity = {key: value for key, value in metadata.items() if key != "alpha_refinement"}
                fallback_identity = {key: value for key, value in fallback_metadata.items() if key != "alpha_refinement"}
                if candidate_identity != fallback_identity:
                    raise ValueError(f"Fallback metadata differs from candidate: {fallback_manifest}")
                manifest_path, metadata = fallback_manifest, fallback_metadata
                used_fallbacks.append({"resource": relative.as_posix(), "source": str(fallback_root.resolve())})
            dest_dir = Path("sprites") / relative
            manifest_dest = dest_dir / "metadata.json"
            if manifest_dest in seen:
                raise ValueError(f"Duplicate sprite resource: {dest_dir}")
            seen.add(manifest_dest)
            copies.append((manifest_path, manifest_dest))
            resources += 1
            alpha_refinement = metadata.get("alpha_refinement") or {}
            if not isinstance(alpha_refinement, dict):
                raise ValueError(f"Invalid alpha refinement metadata: {manifest_path}")
            provenance[(metadata.get("upscaler"), metadata.get("model_sha256"),
                        metadata.get("alpha_mode"), metadata.get("tile_size"), metadata.get("tile_pad"),
                        alpha_refinement.get("method"), alpha_refinement.get("radius"),
                        alpha_refinement.get("thin_guard"), alpha_refinement.get("preserve_support"))] += 1
            sidecars = metadata.get("sidecars", [])
            if not isinstance(sidecars, list):
                raise ValueError(f"Invalid sidecar list: {manifest_path}")
            for name in sidecars:
                if not isinstance(name, str) or Path(name).name != name or name == "metadata.json" or name.startswith("frame_"):
                    raise ValueError(f"Invalid sidecar filename in {manifest_path}: {name!r}")
                sidecar = manifest_path.parent / name
                sidecar_dest = dest_dir / name
                if not sidecar.is_file():
                    raise FileNotFoundError(f"Missing generated sidecar: {sidecar}")
                if sidecar_dest in seen:
                    raise ValueError(f"Duplicate destination: {sidecar_dest}")
                seen.add(sidecar_dest)
                copies.append((sidecar, sidecar_dest))
            for record in metadata["frames"]:
                filename = record["file"]
                if Path(filename).name != filename or not filename.endswith(f"@{scale}x.png"):
                    raise ValueError(f"Invalid frame filename in {manifest_path}: {filename}")
                source = manifest_path.parent / filename
                if not source.is_file():
                    raise FileNotFoundError(f"Missing generated frame: {source}")
                frame_dest = dest_dir / filename
                if frame_dest in seen:
                    raise ValueError(f"Duplicate destination: {frame_dest}")
                seen.add(frame_dest)
                copies.append((source, frame_dest))
                frame_count += 1
    if not resources:
        raise ValueError("No sprite resource manifests found")
    if {entry["resource"] for entry in used_fallbacks} != set(requested_fallbacks):
        raise ValueError("One or more requested fallback resources were not found")

    destination.mkdir(parents=True, exist_ok=False)
    for source, relative in copies:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "name": "Rose Tattoo modernized visual override pack",
        "scale": scale,
        "background_source": str(backgrounds.resolve()),
        "sprite_sources": [str(root.resolve()) for root in sprite_roots],
        "background_count": len(background_paths),
        "sprite_resource_count": resources,
        "sprite_frame_count": frame_count,
        "sprite_fallbacks": used_fallbacks,
        "contour_audits": contour_audits or [],
        "sprite_provenance": [
            {"upscaler": key[0], "model_sha256": key[1], "alpha_mode": key[2],
             "tile_size": key[3], "tile_pad": key[4], "alpha_refinement": key[5],
             "alpha_radius": key[6], "alpha_thin_guard": key[7], "alpha_preserve_support": key[8],
             "resource_count": count}
            for key, count in sorted(provenance.items(), key=lambda item: str(item[0]))
        ],
        "note": "Assembly alone does not establish extraction coverage, runtime support, or visual quality.",
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def resolve_contour_audits(entries: list[list[str]], sprite_roots: list[Path],
                           fallbacks: dict[str, Path]) -> tuple[dict[str, Path], list[dict]]:
    """Revalidate source-bound audits immediately before pack assembly.

    The validator lives in a separate optional tool and uses only the standard
    library. Its report is not a filesystem snapshot; inputs must remain
    unchanged until the pack has finished copying.
    """
    if not entries:
        return fallbacks, []
    from validate_rosetattoo_contour_plan import validate_audit

    roots = {root.resolve() for root in sprite_roots}
    selected = dict(fallbacks)
    records = []
    seen_candidates: set[Path] = set()
    for audit, baseline, candidate, source in entries:
        plan = validate_audit(Path(audit), Path(baseline), Path(candidate), Path(source))
        if plan.candidate_root not in roots:
            raise ValueError(f"Audited candidate is not a sprite input: {plan.candidate_root}")
        if plan.candidate_root in seen_candidates:
            raise ValueError(f"Duplicate contour audit for {plan.candidate_root}")
        seen_candidates.add(plan.candidate_root)
        for resource in plan.fallback_resources:
            if resource in selected and selected[resource].resolve() != plan.baseline_root:
                raise ValueError(f"Conflicting fallback sources for {resource}")
            selected[resource] = plan.baseline_root
        records.append({"report": str(Path(audit).resolve()), "audit_sha256": plan.audit_sha256,
                        "baseline": str(plan.baseline_root), "candidate": str(plan.candidate_root),
                        "source": str(plan.source_root), "fallback_count": len(plan.fallback_resources)})
    return selected, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background-dir", type=Path, required=True)
    parser.add_argument("--sprite-dir", type=Path, action="append", required=True,
                        help="Completed generated sprite tree; repeat for room, cutscene and shared assets.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fallback-sprite", action="append", nargs=2, metavar=("RESOURCE", "BASELINE_ROOT"),
                        help="Use an audited baseline for a whole resource; may be repeated.")
    parser.add_argument("--contour-audit", action="append", nargs=4,
                        metavar=("REPORT", "BASELINE", "CANDIDATE", "SOURCE"),
                        help="Revalidate a source-bound contour audit and select whole-resource fallbacks; repeat per tree.")
    parser.add_argument("--scale", type=int, default=2)
    args = parser.parse_args()
    if args.scale < 1:
        parser.error("--scale must be >= 1")
    pairs = args.fallback_sprite or []
    if len({name for name, _ in pairs}) != len(pairs):
        parser.error("--fallback-sprite resource paths must be unique")
    fallbacks, audits = resolve_contour_audits(args.contour_audit or [], args.sprite_dir,
                                               {name: Path(root) for name, root in pairs})
    manifest = assemble(args.background_dir, args.sprite_dir, args.output_dir, args.scale,
                        fallbacks, audits)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
