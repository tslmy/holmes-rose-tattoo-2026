#!/usr/bin/env python3
"""Read-only comparison of existing and support-preserving sprite trees.

See docs/sprite-contour-audit.md for the report contract and limitations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError
from scipy import ndimage


FOREGROUND_8 = np.ones((3, 3), dtype=np.uint8)
BACKGROUND_4 = ndimage.generate_binary_structure(2, 1)
SHA256 = re.compile(r"[0-9a-f]{64}")
SCENE = re.compile(r"scene_\d+")
GEOMETRY = ("width", "height", "offset_x", "offset_y")


def topology(mask: np.ndarray) -> dict:
    """Count 8-connected foreground components and enclosed 4-connected holes."""
    if not mask.any():
        return {"components_8": 0, "holes_4": 0}
    components = int(ndimage.label(mask, structure=FOREGROUND_8)[1])
    # Explicit padding connects every border background pixel to the outside,
    # including a crop whose foreground touches the edge of the image.
    background = ~np.pad(mask, 1, constant_values=False)
    holes = int(ndimage.label(background, structure=BACKGROUND_4)[1]) - 1
    return {"components_8": components, "holes_4": holes}


def compare_pixels(before: np.ndarray, after: np.ndarray) -> tuple[dict, list[str]]:
    """Compare RGBA bytes, including RGB under transparent pixels."""
    old, new = before[:, :, 3], after[:, :, 3]
    rgb_changed = int(np.count_nonzero(np.any(before[:, :, :3] != after[:, :, :3], axis=2)))
    support_changed = int(np.count_nonzero((old != 0) != (new != 0)))
    old_binary, new_binary = old >= 128, new >= 128
    added = int(np.count_nonzero(~old_binary & new_binary))
    removed = int(np.count_nonzero(old_binary & ~new_binary))
    old_sum = int(old.sum(dtype=np.uint64))
    new_sum = int(new.sum(dtype=np.uint64))
    old_topology = topology(old_binary)
    new_topology = topology(new_binary) if added or removed else old_topology.copy()
    issues = []
    if rgb_changed:
        issues.append("rgb_changed")
    if support_changed:
        issues.append("nonzero_alpha_support_changed")
    if added or removed:
        issues.append("threshold_128_crossing")
    if old_topology != new_topology:
        issues.append("threshold_128_topology_changed")
    return {
        "rgb_changed_pixels": rgb_changed,
        "support_changed_pixels": support_changed,
        "alpha_sum_before": old_sum,
        "alpha_sum_after": new_sum,
        "alpha_weighted_area_before": old_sum / 255,
        "alpha_weighted_area_after": new_sum / 255,
        "alpha_weighted_area_delta": (new_sum - old_sum) / 255,
        "alpha_weighted_area_delta_percent": ((new_sum - old_sum) * 100 / old_sum) if old_sum else None,
        "threshold_128_added_pixels": added,
        "threshold_128_removed_pixels": removed,
        "threshold_128_topology_before": old_topology,
        "threshold_128_topology_after": new_topology,
    }, issues


def source_digest(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    digest = hashlib.sha256(f"{rgba.width}x{rgba.height}:".encode("ascii"))
    digest.update(rgba.tobytes())
    return digest.hexdigest()


def safe_file(directory: Path, name: object) -> Path:
    if (not isinstance(name, str) or not name or name in (".", "..")
            or Path(name).name != name or "\\" in name):
        raise ValueError(f"unsafe file name: {name!r}")
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError(f"symlink or escaping file: {name!r}")
    return path


def load_metadata(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("symlink metadata is not accepted")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return metadata


def discover(root: Path) -> tuple[dict[str, Path], dict[str, Path], dict[str, set[str]], list[str]]:
    """Find manifests and PNGs once; never follow directory symlinks."""
    manifests, summaries, images, errors = {}, {}, {}, []

    def onerror(error: OSError) -> None:
        errors.append(str(error))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        folder = Path(directory)
        relative = folder.relative_to(root).as_posix()
        for name in list(dirs):
            if (folder / name).is_symlink():
                errors.append(f"{relative}/{name}: directory symlink not scanned")
                dirs.remove(name)
        if "metadata.json" in files:
            path = folder / "metadata.json"
            # Only scene summary metadata is allowed not to be a frame manifest.
            if SCENE.fullmatch(folder.name):
                try:
                    meta = load_metadata(path)
                except (OSError, ValueError, UnicodeError) as error:
                    errors.append(f"{relative}/metadata.json: {error}")
                    manifests[relative] = path
                else:
                    (manifests if "frames" in meta else summaries)[relative] = path
            else:
                manifests[relative] = path
        pngs = {name for name in files if name.lower().endswith(".png")}
        if pngs:
            images[relative] = pngs
    return manifests, summaries, images, errors


def records(metadata: dict, label: str, issues: list[str]) -> dict[int, dict]:
    frames = metadata.get("frames")
    if not isinstance(frames, list):
        issues.append(f"{label}: frames must be a list")
        return {}
    if not frames:
        issues.append(f"{label}: frame list is empty")
    if "frame_count" in metadata and metadata["frame_count"] != len(frames):
        issues.append(f"{label}: frame_count does not match frames")
    result, filenames = {}, set()
    for record in frames:
        if not isinstance(record, dict) or type(record.get("index")) is not int or record["index"] < 0:
            issues.append(f"{label}: frame has invalid index or is not an object")
            continue
        index = record["index"]
        if index in result:
            issues.append(f"{label}: duplicate frame index {index}")
            continue
        result[index] = record
        name = record.get("file")
        if not isinstance(name, str) or name in filenames:
            issues.append(f"{label}: frame {index} has missing or duplicate filename")
        else:
            filenames.add(name)
        for field in GEOMETRY:
            value = record.get(field)
            if type(value) is not int or (field in ("width", "height") and value <= 0):
                issues.append(f"{label}: frame {index} has invalid {field}")
    return result


def sidecars(metadata: dict, directory: Path, label: str, issues: list[str]) -> dict[str, bytes]:
    names = metadata.get("sidecars", [])
    if (not isinstance(names, list) or any(not isinstance(n, str) for n in names)
            or len(set(names)) != len(names)):
        issues.append(f"{label}: invalid sidecar list")
        return {}
    result = {}
    for name in names:
        try:
            if name == "metadata.json" or name.lower().endswith(".png"):
                raise ValueError(f"invalid sidecar name: {name!r}")
            result[name] = safe_file(directory, name).read_bytes()
        except (OSError, ValueError) as error:
            issues.append(f"{label}: sidecar {name!r}: {error}")
    if (directory / "palette.bin").exists() and "palette.bin" not in names:
        issues.append(f"{label}: palette.bin is not declared as a sidecar")
    return result


def compare_metadata(base: dict, candidate: dict, source: dict | None, issues: list[str]) -> None:
    # The refinement description is the only expected resource-level change.
    ignore = {"frames", "alpha_refinement"}
    for key in sorted((set(base) | set(candidate)) - ignore):
        if key not in base or key not in candidate or base[key] != candidate[key]:
            issues.append(f"baseline/candidate metadata differs: {key}")
    if source is not None:
        for key in sorted(set(source) - {"frames"}):
            if key not in base or source[key] != base[key]:
                issues.append(f"source/baseline metadata differs: {key}")
    for label, meta in (("baseline", base), ("candidate", candidate)):
        if meta.get("complete") is not True:
            issues.append(f"{label}: upscale is not complete")
        if type(meta.get("scale")) is not int or meta["scale"] < 1:
            issues.append(f"{label}: invalid scale")


def open_rgba(path: Path, record: dict, label: str, issues: list[str]) -> tuple[np.ndarray, dict] | None:
    try:
        with Image.open(path) as image:
            if image.mode != "RGBA":
                raise ValueError(f"expected RGBA, got {image.mode}")
            image.load()
            if image.size != (record.get("width"), record.get("height")):
                issues.append(f"{label}: image dimensions differ from metadata")
            return np.array(image), dict(image.info)
    except (OSError, ValueError, UnidentifiedImageError) as error:
        issues.append(f"{label}: cannot read frame: {error}")
        return None


def compare_frame(index: int, base_dir: Path, candidate_dir: Path, source_dir: Path | None,
                  base: dict | None, candidate: dict | None, source: dict | None,
                  scale: object) -> dict:
    result = {"index": index, "issues": []}
    issues = result["issues"]
    if base is None or candidate is None:
        issues.append("frame missing from baseline or candidate metadata")
    if source_dir is not None and source is None:
        issues.append("frame missing from source metadata")
    if base is None or candidate is None:
        return result
    result["baseline_file"] = base.get("file")
    result["candidate_file"] = candidate.get("file")
    for key in sorted(set(base) | set(candidate)):
        if key not in base or key not in candidate or base[key] != candidate[key]:
            issues.append(f"baseline/candidate frame metadata differs: {key}")
    if source is not None:
        result["source_file"] = source.get("file")
        for key in sorted(set(source) - {"file", *GEOMETRY}):
            if key not in base or source[key] != base[key]:
                issues.append(f"source/baseline frame metadata differs: {key}")
        if type(scale) is int and scale > 0:
            for key in GEOMETRY:
                if type(source.get(key)) is not int or base.get(key) != source[key] * scale:
                    issues.append(f"source/baseline scaled geometry differs: {key}")
    try:
        base_path = safe_file(base_dir, base.get("file"))
        candidate_path = safe_file(candidate_dir, candidate.get("file"))
    except (OSError, ValueError) as error:
        issues.append(str(error))
        return result
    old = open_rgba(base_path, base, "baseline", issues)
    new = open_rgba(candidate_path, candidate, "candidate", issues)
    if old is None or new is None:
        return result
    old_pixels, old_info = old
    new_pixels, new_info = new
    old_hash, new_hash = old_info.get("source_sha256"), new_info.get("source_sha256")
    if not isinstance(old_hash, str) or not SHA256.fullmatch(old_hash):
        issues.append("baseline: missing or invalid source_sha256")
    if not isinstance(new_hash, str) or not SHA256.fullmatch(new_hash):
        issues.append("candidate: missing or invalid source_sha256")
    if old_hash != new_hash:
        issues.append("baseline/candidate source_sha256 differs")
    if "alpha_input_sha256" in new_info:
        try:
            if new_info["alpha_input_sha256"] != hashlib.sha256(base_path.read_bytes()).hexdigest():
                issues.append("candidate: alpha_input_sha256 differs from baseline file")
        except OSError as error:
            issues.append(f"baseline: cannot hash file: {error}")
    if source is not None and source_dir is not None:
        try:
            source_path = safe_file(source_dir, source.get("file"))
            with Image.open(source_path) as image:
                image.load()
                if image.size != (source.get("width"), source.get("height")):
                    issues.append("source: image dimensions differ from metadata")
                digest = source_digest(image)
            if old_hash != digest or new_hash != digest:
                issues.append("source: decoded RGBA digest differs from PNG source_sha256")
        except (OSError, ValueError, UnidentifiedImageError) as error:
            issues.append(f"source: cannot read frame: {error}")
    if old_pixels.shape != new_pixels.shape:
        issues.append("baseline/candidate image dimensions differ")
    else:
        metrics, pixel_issues = compare_pixels(old_pixels, new_pixels)
        result.update(metrics)
        issues.extend(pixel_issues)
    return result


def audit_resource(relative: str, base_root: Path, candidate_root: Path, source_root: Path | None,
                   base_path: Path | None, candidate_path: Path | None,
                   base_images: set[str], candidate_images: set[str]) -> dict:
    result = {"resource": relative, "issues": [], "frames": []}
    issues = result["issues"]
    metas = []
    source_directory = source_root / relative if source_root else None
    source_path = source_directory / "metadata.json" if source_directory else None
    if source_root is not None:
        current = source_root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                issues.append(f"source: directory symlink not accepted: {current}")
                source_path = None
                break
    for label, path in (("baseline", base_path), ("candidate", candidate_path),
                        ("source", source_path)):
        if path is None:
            if label != "source":
                issues.append(f"{label}: resource metadata missing")
            metas.append(None)
            continue
        try:
            metas.append(load_metadata(path))
        except (OSError, ValueError, UnicodeError) as error:
            issues.append(f"{label}: cannot read metadata: {error}")
            metas.append(None)
    base, candidate, source = metas
    dirs = (base_root / relative, candidate_root / relative, source_directory)
    maps = [records(meta, label, issues) if meta is not None else {} for meta, label in
            zip(metas, ("baseline", "candidate", "source"))]
    for label, filenames, frame_map in (("baseline", base_images, maps[0]),
                                         ("candidate", candidate_images, maps[1])):
        referenced = {r.get("file") for r in frame_map.values() if isinstance(r.get("file"), str)}
        for name in sorted(filenames - referenced):
            issues.append(f"{label}: unreferenced PNG {name}")
    if source is not None:
        try:
            with os.scandir(dirs[2]) as entries:
                source_images = {e.name for e in entries if e.name.lower().endswith(".png")
                                 and (e.is_file(follow_symlinks=False) or e.is_symlink())}
            referenced = {r.get("file") for r in maps[2].values() if isinstance(r.get("file"), str)}
            for name in sorted(source_images - referenced):
                issues.append(f"source: unreferenced PNG {name}")
        except OSError as error:
            issues.append(f"source: cannot scan resource: {error}")
    if base is not None and candidate is not None:
        compare_metadata(base, candidate, source, issues)
        if list(maps[0]) != list(maps[1]):
            issues.append("baseline/candidate frame index order differs")
        if source is not None and list(maps[0]) != list(maps[2]):
            issues.append("source/baseline frame index order differs")
    copies = [sidecars(meta, directory, label, issues) if meta is not None else {} for meta, directory, label in
              zip(metas, dirs, ("baseline", "candidate", "source"))]
    if base is not None and candidate is not None and copies[0] != copies[1]:
        issues.append("baseline/candidate sidecars differ")
    if source is not None and base is not None and copies[0] != copies[2]:
        issues.append("source/baseline sidecars differ")
    indices = set(maps[0]) | set(maps[1]) | (set(maps[2]) if source_root else set())
    for index in sorted(indices):
        result["frames"].append(compare_frame(index, dirs[0], dirs[1], dirs[2],
                                              maps[0].get(index), maps[1].get(index), maps[2].get(index),
                                              base.get("scale") if base else None))
    compared = [frame for frame in result["frames"] if "alpha_sum_before" in frame]
    result["frames_compared"] = len(compared)
    result["suspect_frame_indices"] = [f["index"] for f in result["frames"] if f["issues"]]
    old_sum = sum(frame["alpha_sum_before"] for frame in compared)
    new_sum = sum(frame["alpha_sum_after"] for frame in compared)
    percentages = [abs(frame["alpha_weighted_area_delta_percent"]) for frame in compared
                   if frame["alpha_weighted_area_delta_percent"] is not None]
    result["metrics"] = {
        "rgb_changed_frames": sum(frame["rgb_changed_pixels"] > 0 for frame in compared),
        "support_changed_frames": sum(frame["support_changed_pixels"] > 0 for frame in compared),
        "threshold_128_crossing_frames": sum(bool(frame["threshold_128_added_pixels"] or
                                                   frame["threshold_128_removed_pixels"]) for frame in compared),
        "threshold_128_topology_changed_frames": sum(frame["threshold_128_topology_before"] !=
                                                      frame["threshold_128_topology_after"] for frame in compared),
        "threshold_128_added_pixels": sum(frame["threshold_128_added_pixels"] for frame in compared),
        "threshold_128_removed_pixels": sum(frame["threshold_128_removed_pixels"] for frame in compared),
        "alpha_weighted_area_delta": (new_sum - old_sum) / 255,
        "alpha_weighted_area_delta_percent": ((new_sum - old_sum) * 100 / old_sum) if old_sum else None,
        "max_absolute_frame_area_delta_percent": max(percentages, default=None),
    }
    result["suspect"] = bool(issues or result["suspect_frame_indices"])
    return result


def audit(base_root: Path, candidate_root: Path, source_root: Path | None) -> dict:
    base_manifests, base_summaries, base_images, base_errors = discover(base_root)
    candidate_manifests, candidate_summaries, candidate_images, candidate_errors = discover(candidate_root)
    errors = [f"baseline: {e}" for e in base_errors] + [f"candidate: {e}" for e in candidate_errors]
    for relative in sorted(set(base_summaries) | set(candidate_summaries)):
        try:
            if relative not in base_summaries or relative not in candidate_summaries:
                errors.append(f"{relative}: scene summary missing from baseline or candidate")
            elif load_metadata(base_summaries[relative]) != load_metadata(candidate_summaries[relative]):
                errors.append(f"{relative}: scene summary metadata differs")
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(f"{relative}: scene summary: {error}")
    relatives = set(base_manifests) | set(candidate_manifests) | set(base_images) | set(candidate_images)
    if not relatives:
        errors.append("no frame resources found")
    resources = [audit_resource(relative, base_root, candidate_root, source_root,
                                base_manifests.get(relative), candidate_manifests.get(relative),
                                base_images.get(relative, set()), candidate_images.get(relative, set()))
                 for relative in sorted(relatives)]
    return {
        "schema_version": 1,
        "inputs": {"baseline": str(base_root), "candidate": str(candidate_root),
                   "source": str(source_root) if source_root else None},
        "source_verification": "decoded_rgba" if source_root else "matching_png_tags_only",
        "summary": {"resources": len(resources),
                    "suspect_resources": sum(r["suspect"] for r in resources),
                    "frames_compared": sum(r["frames_compared"] for r in resources),
                    "suspect_frames": sum(len(r["suspect_frame_indices"]) for r in resources),
                    "global_errors": len(errors)},
        "errors": errors,
        "resources": resources,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--report", type=Path, required=True, help="New JSON file, outside all input trees")
    args = parser.parse_args(argv)
    roots = [p.resolve() if p is not None else None for p in (args.baseline, args.candidate, args.source)]
    for root in roots:
        if root is not None and not root.is_dir():
            parser.error(f"input directory not found: {root}")
    report_path = args.report.resolve()
    for root in roots:
        if root is not None and report_path.is_relative_to(root):
            parser.error("report must be outside all input trees")
    if not report_path.parent.is_dir():
        parser.error("report parent directory must already exist")
    # Exclusive creation prevents accidental replacement, even for an existing symlink.
    try:
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except OSError as error:
        parser.error(f"cannot create new report: {error}")
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        report = audit(*roots)
        json.dump(report, output, ensure_ascii=False, separators=(",", ":"))
        output.write("\n")
    summary = report["summary"]
    print(f"{summary['frames_compared']} frames compared; {summary['suspect_frames']} suspect frames; "
          f"{summary['suspect_resources']}/{summary['resources']} suspect resources; "
          f"{summary['global_errors']} global errors. Report: {args.report}")
    return int(bool(summary["suspect_resources"] or summary["global_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
