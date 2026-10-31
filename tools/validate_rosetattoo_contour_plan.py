#!/usr/bin/env python3
"""Validate a source-backed contour audit and plan whole-resource fallbacks.

This standard-library-only tool never copies or changes assets. It assumes the
audit was produced by a trusted auditor; see docs/contour-selection-plan.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple


HASH = re.compile(r"[0-9a-f]{64}\Z")
MD5 = re.compile(r"[0-9a-f]{32}\Z")
SCENE = re.compile(r"scene_\d+\Z")
WALK_NAME = re.compile(r"[A-Z0-9_]{1,8}\.VGS\Z")
GEOMETRY = ("width", "height", "offset_x", "offset_y")
LABELS = ("baseline", "candidate", "source")
CROSSING = "threshold_128_crossing"
TOPOLOGY = "threshold_128_topology_changed"


class ValidationError(ValueError):
    """The report or current input trees are unsafe or inconsistent."""


class SelectionPlan(NamedTuple):
    baseline_root: Path
    candidate_root: Path
    source_root: Path
    fallback_resources: tuple[str, ...]
    audit_sha256: str


@dataclass
class Manifest:
    path: Path
    data: dict
    digest: str


@dataclass
class Tree:
    root: Path
    resources: dict[str, Manifest]
    summaries: dict[str, Manifest]
    files: dict[str, set[str]]


def fail(message: str) -> None:
    raise ValidationError(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    fail(f"non-finite JSON number: {value}")


def finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        fail(f"non-finite JSON number: {value}")
    return result


def parse_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant, parse_float=finite_float)
    except (UnicodeError, ValueError, RecursionError) as error:
        fail(f"{label}: invalid JSON: {error}")
    if not isinstance(value, dict):
        fail(f"{label}: expected a JSON object")
    return value


def same_json(left: object, right: object) -> bool:
    # Python equates True with 1; JSON booleans are not JSON numbers.
    def compare(a: object, b: object) -> bool:
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a == b
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(compare(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(compare(x, y) for x, y in zip(a, b))
        return a == b
    try:
        return compare(left, right)
    except RecursionError:
        fail("JSON nesting exceeds comparison limits")


def read_regular(path: Path, *, contents: bool = False) -> bytes | str:
    """Read a regular, non-symlink file and detect changes during this read."""
    try:
        if path.is_symlink():
            fail(f"symlink is not allowed: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                fail(f"not a regular file: {path}")
            if contents:
                result = stream.read()
            else:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                result = digest.hexdigest()
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                fail(f"file changed while reading: {path}")
            return result
    except OSError as error:
        fail(f"cannot read {path}: {error}")


def root_path(path: Path, label: str) -> Path:
    try:
        if path.is_symlink():
            fail(f"{label} root is a symlink: {path}")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            fail(f"{label} root is not a directory: {path}")
        return resolved
    except (OSError, RuntimeError) as error:
        fail(f"invalid {label} root {path}: {error}")


def relative_name(name: object) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name:
        fail(f"invalid resource path: {name!r}")
    if name == ".":
        return name
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in (".", "..") for p in path.parts) or path.as_posix() != name:
        fail(f"invalid resource path: {name!r}")
    return name


def filename(name: object) -> str:
    if (not isinstance(name, str) or not name or name in (".", "..") or "\\" in name
            or "\0" in name or "/" in name or Path(name).name != name):
        fail(f"unsafe filename: {name!r}")
    return name


def discover(root: Path) -> Tree:
    resources, summaries, files = {}, {}, {}

    def onerror(error: OSError) -> None:
        fail(f"cannot scan {root}: {error}")

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        folder = Path(directory)
        relative = folder.relative_to(root).as_posix()
        for name in dirs + names:
            path = folder / name
            if path.is_symlink():
                fail(f"symlink in input tree: {path}")
        for name in names:
            if not (folder / name).is_file():
                fail(f"not a regular file: {folder / name}")
        if names:
            files[relative] = set(names)
        if "metadata.json" not in names:
            continue
        path = folder / "metadata.json"
        raw = read_regular(path, contents=True)
        assert isinstance(raw, bytes)
        data = parse_json(raw, str(path))
        manifest = Manifest(path, data, hashlib.sha256(raw).hexdigest())
        if "frames" in data:
            resources[relative] = manifest
        elif SCENE.fullmatch(folder.name):
            summaries[relative] = manifest
        else:
            fail(f"{path}: missing frame manifest")
    return Tree(root, resources, summaries, files)


def integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        fail(f"{label}: expected an integer >= {minimum}")
    return value


def match_number(value: object, expected: float | None, label: str) -> None:
    if expected is None:
        if value is not None:
            fail(f"{label}: expected null")
    else:
        try:
            valid = (type(value) in (int, float) and math.isfinite(value)
                     and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12))
        except OverflowError:
            valid = False
        if not valid:
            fail(f"{label}: inconsistent value")


def require_hash(expected: object, actual: str, label: str) -> None:
    if not isinstance(expected, str) or not HASH.fullmatch(expected) or expected != actual:
        fail(f"{label}: missing, invalid or mismatched SHA-256")


def frame_records(metadata: dict, label: str) -> dict[int, dict]:
    frames = metadata.get("frames")
    if not isinstance(frames, list) or not frames:
        fail(f"{label}: frames must be a nonempty list")
    if "frame_count" in metadata and integer(metadata["frame_count"], f"{label}: frame_count") != len(frames):
        fail(f"{label}: frame_count differs")
    records, names = {}, set()
    for record in frames:
        if not isinstance(record, dict):
            fail(f"{label}: invalid frame record")
        index = integer(record.get("index"), f"{label}: frame index")
        name = filename(record.get("file"))
        if not name.lower().endswith(".png") or index in records or name in names:
            fail(f"{label}: invalid or duplicate frame index/filename")
        for field in GEOMETRY:
            value = record.get(field)
            if (type(value) is not int or
                    (field in ("width", "height") and not 0 < value <= 0x7fffffff)):
                fail(f"{label}: invalid frame {index} {field}")
        records[index] = record
        names.add(name)
    return records


def sidecar_names(metadata: dict, label: str) -> list[str]:
    values = metadata.get("sidecars", [])
    if not isinstance(values, list):
        fail(f"{label}: invalid sidecar list")
    names = [filename(value) for value in values]
    if (len(set(names)) != len(names)
            or any(name == "metadata.json" or name.lower().endswith(".png") for name in names)):
        fail(f"{label}: invalid or duplicate sidecar")
    return names


def verify_inventory(tree: Tree, relative: str, records: dict[int, dict], sidecars: list[str]) -> None:
    expected = {"metadata.json", *sidecars, *(r["file"] for r in records.values())}
    actual = tree.files.get(relative, set())
    if actual != expected:
        fail(f"{tree.root / relative}: missing or orphan files: {sorted(expected ^ actual)}")


def verify_summary(tree: Tree, relative: str, metadata: dict) -> None:
    scene_name = Path(relative).name
    if "scene_id" in metadata:
        if integer(metadata["scene_id"], f"{relative}: scene_id") != int(scene_name[6:]):
            fail(f"{relative}: summary scene_id differs from directory")
    if "rooms" not in metadata:
        return
    rooms = metadata["rooms"]
    if not isinstance(rooms, list):
        fail(f"{relative}: invalid summary rooms")
    found, total = set(), 0
    for room in rooms:
        if not isinstance(room, dict):
            fail(f"{relative}: invalid summary room")
        name = filename(room.get("directory"))
        resource = f"{relative}/{name}" if relative != "." else name
        if resource in found or resource not in tree.resources:
            fail(f"{relative}: summary room missing or duplicated: {name}")
        found.add(resource)
        actual = tree.resources[resource].data
        count = len(frame_records(actual, resource))
        if integer(room.get("frame_count"), f"{resource}: summary frame_count") != count:
            fail(f"{resource}: summary frame_count differs")
        if ("room_index" in actual and
                ("room_index" not in room or not same_json(room["room_index"], actual["room_index"]))):
            fail(f"{resource}: summary room_index differs")
        total += count
    room_resources = {name for name in tree.resources if PurePosixPath(name).parent.as_posix() == relative
                      and PurePosixPath(name).name.startswith("room_")}
    if found != room_resources:
        fail(f"{relative}: summary room set differs from resources")
    if integer(metadata.get("room_count"), f"{relative}: room_count") != len(rooms):
        fail(f"{relative}: summary room_count differs")
    if integer(metadata.get("frame_count"), f"{relative}: frame_count") != total:
        fail(f"{relative}: summary frame_count differs")


def verify_variant_manifest(tree: Tree) -> None:
    """Validate the known extraction-level VGS variants manifest, if present."""
    path = tree.root / "manifest.json"
    raw = read_regular(path, contents=True)
    assert isinstance(raw, bytes)
    manifest = parse_json(raw, str(path))
    if manifest.get("format") == "RT-palette-walk-sources-v1":
        verify_palette_manifest(tree, manifest, path)
        return
    if "format" not in manifest and "scenes" in manifest:
        verify_room_manifest(tree, manifest, path)
        return
    if "format" in manifest:
        fail(f"{path}: unrecognized source root manifest format")
    variants = manifest.get("variants")
    if not isinstance(variants, list):
        fail(f"{path}: unrecognized source root manifest")
    found, names, total = set(), set(), 0
    for variant in variants:
        if not isinstance(variant, dict):
            fail(f"{path}: invalid variant")
        relative = relative_name(variant.get("path"))
        if relative in found or relative not in tree.resources:
            fail(f"{path}: duplicate or missing variant resource: {relative}")
        found.add(relative)
        meta = tree.resources[relative].data
        name, digest = variant.get("resource"), variant.get("md5")
        if (not isinstance(name, str) or not name or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{32}", digest)
                or meta.get("resource_name") != name or meta.get("resource_md5") != digest):
            fail(f"{path}: variant identity differs for {relative}")
        names.add(name)
        count = len(frame_records(meta, relative))
        if integer(variant.get("frames"), f"{relative}: variant frames") != count:
            fail(f"{path}: variant frame count differs for {relative}")
        archives, source_archives = variant.get("archives"), meta.get("source_archives")
        if (not isinstance(archives, list) or any(not isinstance(a, str) for a in archives)
                or len(set(archives)) != len(archives) or not isinstance(source_archives, list)
                or any(not isinstance(a, dict) or not isinstance(a.get("archive"), str) for a in source_archives)
                or set(archives) != {a["archive"] for a in source_archives}):
            fail(f"{path}: variant archives differ for {relative}")
        if "decode_mode" in manifest and not same_json(manifest["decode_mode"], meta.get("decode_mode")):
            fail(f"{path}: variant decode mode differs for {relative}")
        if "palette_scene" in manifest and "fallback_palette" in meta:
            scene = integer(manifest["palette_scene"], f"{path}: palette_scene")
            if meta["fallback_palette"] != f"scene:{scene}":
                fail(f"{path}: variant palette scene differs for {relative}")
        total += count
    if found != set(tree.resources):
        fail(f"{path}: variant resource set differs")
    if (integer(manifest.get("variant_count"), f"{path}: variant_count") != len(variants)
            or integer(manifest.get("resource_count"), f"{path}: resource_count") != len(names)
            or integer(manifest.get("frame_count"), f"{path}: frame_count") != total):
        fail(f"{path}: variant summary counts differ")


def verify_palette_manifest(tree: Tree, manifest: dict, path: Path) -> None:
    """Cross-check a palette-walk extraction index against its current tree."""
    mode = {"skipPal": False, "animImages": False}
    if not same_json(manifest.get("decode_mode"), mode):
        fail(f"{path}: unsupported palette walk decode mode")
    scenes, resources, projections = (manifest.get(key) for key in ("scenes", "resources", "projections"))
    if (not isinstance(scenes, list) or not scenes or
            any(type(n) is not int or n < 0 for n in scenes) or len(set(scenes)) != len(scenes)):
        fail(f"{path}: invalid palette scene list")
    if (not isinstance(resources, list) or not resources or
            any(not isinstance(n, str) or not WALK_NAME.fullmatch(n) for n in resources)
            or len(set(resources)) != len(resources)):
        fail(f"{path}: invalid palette resource list")
    if not isinstance(projections, list) or not projections:
        fail(f"{path}: invalid palette projections")
    found, identities, names, covered, total = set(), set(), set(), {}, 0
    for projection in projections:
        if not isinstance(projection, dict):
            fail(f"{path}: invalid palette projection")
        relative = relative_name(projection.get("path"))
        if relative in found or relative not in tree.resources:
            fail(f"{path}: duplicate or missing palette projection: {relative}")
        found.add(relative)
        name, digest, palette = (projection.get(key) for key in ("resource", "resource_md5", "palette_md5"))
        if (not isinstance(name, str) or not WALK_NAME.fullmatch(name) or
                not isinstance(digest, str) or not MD5.fullmatch(digest) or
                not isinstance(palette, str) or not MD5.fullmatch(palette)):
            fail(f"{path}: invalid palette projection identity: {relative}")
        expected = f"{name.lower().replace('.', '_')}/variants/{digest}-s0a0/palettes/{palette}"
        if relative != expected:
            fail(f"{path}: palette projection path differs: {relative}")
        meta = tree.resources[relative].data
        if (meta.get("resource_name") != name or meta.get("resource_md5") != digest or
                meta.get("palette_md5") != palette or meta.get("palette_format") != "RT-PAL1" or
                meta.get("sidecars") != ["palette.bin"] or not same_json(meta.get("decode_mode"), mode)):
            fail(f"{path}: palette projection metadata differs: {relative}")
        palette_data = read_regular(tree.root / relative / "palette.bin", contents=True)
        assert isinstance(palette_data, bytes)
        if hashlib.md5(palette_data, usedforsecurity=False).hexdigest() != palette:
            fail(f"{path}: palette sidecar identity differs: {relative}")
        selection = projection.get("scenes")
        if (not isinstance(selection, list) or not selection or
                any(type(n) is not int or n not in scenes for n in selection) or
                len(set(selection)) != len(selection) or not same_json(selection, meta.get("source_scenes"))):
            fail(f"{path}: palette projection scenes differ: {relative}")
        identity = (name, digest)
        prior = covered.setdefault(identity, set())
        if prior.intersection(selection):
            fail(f"{path}: duplicate scene in palette projections: {relative}")
        prior.update(selection)
        identities.add(identity)
        names.add(name)
        count = len(frame_records(meta, relative))
        if integer(projection.get("frames"), f"{relative}: projection frames") != count:
            fail(f"{path}: palette projection frame count differs: {relative}")
        total += count
    if found != set(tree.resources) or names != set(resources):
        fail(f"{path}: palette resource set differs")
    if any(value != set(scenes) for value in covered.values()):
        fail(f"{path}: palette projections do not cover the scene set")
    for field, expected in (("scene_count", len(scenes)), ("resource_count", len(names)),
                            ("identity_count", len(identities)), ("projection_count", len(projections)),
                            ("frame_count", total)):
        if integer(manifest.get(field), f"{path}: {field}") != expected:
            fail(f"{path}: palette summary counts differ: {field}")


def verify_room_manifest(tree: Tree, manifest: dict, path: Path) -> None:
    """Cross-check the room extraction index with source scene summaries."""
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        fail(f"{path}: invalid room scenes")
    found, rooms, frames = set(), 0, 0
    for scene in scenes:
        if not isinstance(scene, dict):
            fail(f"{path}: invalid room scene")
        scene_id = integer(scene.get("scene_id"), f"{path}: scene_id")
        relative = f"scene_{scene_id:03d}"
        if relative in found or relative not in tree.summaries:
            fail(f"{path}: duplicate or missing room scene: {relative}")
        found.add(relative)
        if not same_json(scene, tree.summaries[relative].data):
            fail(f"{path}: root/scene summary differs: {relative}")
        verify_summary(tree, relative, scene)
        rooms += integer(scene.get("room_count"), f"{relative}: room_count")
        frames += integer(scene.get("frame_count"), f"{relative}: frame_count")
    if found != set(tree.summaries):
        fail(f"{path}: room scene summary set differs")
    if any(PurePosixPath(resource).parent.as_posix() not in found for resource in tree.resources):
        fail(f"{path}: room resource outside declared scenes")
    if len(tree.resources) != rooms:
        fail(f"{path}: room resource set differs from summaries")
    for field, expected in (("scene_count", len(found)), ("room_count", rooms), ("frame_count", frames)):
        if integer(manifest.get(field), f"{path}: {field}") != expected:
            fail(f"{path}: room summary counts differ: {field}")


def verify_trees(trees: tuple[Tree, Tree, Tree], report_resources: dict[str, dict]) -> None:
    base, candidate, source = trees
    expected = set(base.resources)
    if not expected or any(set(tree.resources) != expected for tree in trees[1:]) or set(report_resources) != expected:
        fail("baseline, candidate, source and report resource manifest sets differ or are empty")
    if set(base.summaries) != set(candidate.summaries):
        fail("baseline/candidate scene summary sets differ")
    for relative in base.summaries:
        if not same_json(base.summaries[relative].data, candidate.summaries[relative].data):
            fail(f"{relative}: baseline/candidate scene summaries differ")
        if relative in source.summaries and not same_json(source.summaries[relative].data, base.summaries[relative].data):
            fail(f"{relative}: source scene summary differs")
    for tree in trees:
        for relative in tree.files:
            if relative in tree.summaries:
                if tree.files[relative] != {"metadata.json"}:
                    fail(f"{tree.root / relative}: orphan file in summary directory")
                verify_summary(tree, relative, tree.summaries[relative].data)
            elif relative not in tree.resources:
                if tree is source and relative == "." and tree.files[relative] == {"manifest.json"}:
                    verify_variant_manifest(tree)
                else:
                    fail(f"{tree.root / relative}: orphan files outside any resource")


def topology_counts(value: object, label: str, pixels: int) -> dict:
    if not isinstance(value, dict) or set(value) != {"components_8", "holes_4"}:
        fail(f"{label}: invalid topology counts")
    for key in value:
        if integer(value[key], f"{label}: {key}") > pixels:
            fail(f"{label}: topology count exceeds image size")
    return value


def verify_frame(frame: dict, records: tuple[dict, dict, dict], trees: tuple[Tree, Tree, Tree],
                 relative: str, font: bool) -> tuple[bool, bool, int, int, int, int, float | None]:
    label = f"{relative} frame {frame.get('index')}"
    if {key for key in frame if key.endswith("_sha256")} != {f"{name}_png_sha256" for name in LABELS}:
        fail(f"{label}: missing or unexpected frame hash fields")
    for name, record, tree in zip(LABELS, records, trees):
        if frame.get(f"{name}_file") != record["file"]:
            fail(f"{label}: {name} filename differs from manifest")
        digest = read_regular(tree.root / relative / record["file"])
        assert isinstance(digest, str)
        require_hash(frame.get(f"{name}_png_sha256"), digest, f"{label}: {name} PNG")
    pixels = records[0]["width"] * records[0]["height"]
    for key in ("rgb_changed_pixels", "support_changed_pixels"):
        if integer(frame.get(key), f"{label}: {key}") != 0:
            fail(f"{label}: {key} must be zero")
    added = integer(frame.get("threshold_128_added_pixels"), f"{label}: threshold added")
    removed = integer(frame.get("threshold_128_removed_pixels"), f"{label}: threshold removed")
    if added + removed > pixels:
        fail(f"{label}: threshold crossings exceed image size")
    before = topology_counts(frame.get("threshold_128_topology_before"), label, pixels)
    after = topology_counts(frame.get("threshold_128_topology_after"), label, pixels)
    crossing, changed = bool(added or removed), before != after
    if changed and not crossing:
        fail(f"{label}: topology changed without threshold crossings")
    issues = frame.get("issues")
    expected = ({CROSSING} if crossing else set()) | ({TOPOLOGY} if changed else set())
    if (not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues)
            or len(issues) != len(set(issues)) or set(issues) != expected):
        fail(f"{label}: unexpected or inconsistent frame issues")
    old_sum = integer(frame.get("alpha_sum_before"), f"{label}: alpha sum before")
    new_sum = integer(frame.get("alpha_sum_after"), f"{label}: alpha sum after")
    if old_sum > 255 * pixels or new_sum > 255 * pixels:
        fail(f"{label}: alpha sum exceeds image size")
    percent = (new_sum - old_sum) * 100 / old_sum if old_sum else None
    for key, expected_value in (("alpha_weighted_area_before", old_sum / 255),
                                ("alpha_weighted_area_after", new_sum / 255),
                                ("alpha_weighted_area_delta", (new_sum - old_sum) / 255),
                                ("alpha_weighted_area_delta_percent", percent)):
        if key not in frame:
            fail(f"{label}: missing {key}")
        match_number(frame.get(key), expected_value, f"{label}: {key}")
    if font and (crossing or old_sum != new_sum):
        fail(f"{label}: preserve-font-alpha has changed opacity metrics")
    return crossing, changed, added, removed, old_sum, new_sum, percent


def verify_resource(relative: str, entry: dict, trees: tuple[Tree, Tree, Tree]) -> tuple[bool, int, int, bool]:
    if entry.get("issues") != []:
        fail(f"{relative}: resource issues are a hard failure")
    manifests = tuple(tree.resources[relative] for tree in trees)
    metas = tuple(manifest.data for manifest in manifests)
    base, candidate, source = metas
    if not same_json({k: v for k, v in base.items() if k not in ("frames", "alpha_refinement")},
                     {k: v for k, v in candidate.items() if k not in ("frames", "alpha_refinement")}):
        fail(f"{relative}: baseline/candidate metadata differs")
    for key, value in source.items():
        if key != "frames" and (key not in base or not same_json(base[key], value)):
            fail(f"{relative}: source metadata differs: {key}")
    if base.get("alpha_refinement") is not None:
        fail(f"{relative}: baseline already has alpha refinement")
    scale = integer(base.get("scale"), f"{relative}: scale", minimum=1)
    if base.get("complete") is not True or candidate.get("complete") is not True:
        fail(f"{relative}: incomplete upscale")
    refinement = candidate.get("alpha_refinement")
    if (not isinstance(refinement, dict) or type(refinement.get("scale")) is not int
            or refinement["scale"] != scale):
        fail(f"{relative}: missing or invalid alpha refinement")
    font = refinement.get("method") == "preserve-font-alpha"
    if font:
        if (candidate.get("upscaler") != "lanczos-alpha" or refinement.get("preserve_support") is not False
                or type(refinement.get("radius")) not in (int, float) or refinement.get("radius") != 0
                or refinement.get("thin_guard") is not False):
            fail(f"{relative}: invalid preserved-font refinement")
    elif refinement.get("method") != "native-alpha-scale4-box" or refinement.get("preserve_support") is not True:
        fail(f"{relative}: candidate is not support-preserving geometric contour refinement")
    elif (scale != 2 or type(refinement.get("thin_guard")) is not bool or
          type(refinement.get("radius")) not in (int, float)
          or not 0 <= refinement["radius"] <= 2):
        fail(f"{relative}: invalid geometric refinement settings")
    maps = tuple(frame_records(meta, f"{relative}: {name}") for name, meta in zip(LABELS, metas))
    if any(list(records) != list(maps[0]) for records in maps[1:]):
        fail(f"{relative}: frame index order differs")
    names = tuple(sidecar_names(meta, f"{relative}: {name}") for name, meta in zip(LABELS, metas))
    if names[0] != names[1] or names[0] != names[2]:
        fail(f"{relative}: sidecar declarations differ")
    hashes = entry.get("file_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(LABELS):
        fail(f"{relative}: missing or unexpected file hash groups")
    sidecar_digests = []
    for label, tree, manifest, records, sidecars in zip(LABELS, trees, manifests, maps, names):
        verify_inventory(tree, relative, records, sidecars)
        group = hashes[label]
        if not isinstance(group, dict) or set(group) != {"metadata", "sidecars"}:
            fail(f"{relative}: invalid {label} file hashes")
        require_hash(group["metadata"], manifest.digest, f"{relative}: {label} metadata")
        reported = group["sidecars"]
        if not isinstance(reported, dict) or set(reported) != set(sidecars):
            fail(f"{relative}: {label} sidecar hash set differs")
        actual = {}
        for name in sidecars:
            digest = read_regular(tree.root / relative / name)
            assert isinstance(digest, str)
            require_hash(reported[name], digest, f"{relative}: {label} sidecar {name}")
            actual[name] = digest
        sidecar_digests.append(actual)
    if sidecar_digests[0] != sidecar_digests[1] or sidecar_digests[0] != sidecar_digests[2]:
        fail(f"{relative}: sidecar bytes differ")
    frames = entry.get("frames")
    if not isinstance(frames, list) or len(frames) != len(maps[0]):
        fail(f"{relative}: missing or extra report frames")
    seen, suspects, values = set(), [], []
    for frame in frames:
        if not isinstance(frame, dict):
            fail(f"{relative}: invalid report frame")
        index = integer(frame.get("index"), f"{relative}: report frame index")
        if index in seen or index not in maps[0]:
            fail(f"{relative}: duplicate or unknown report frame {index}")
        seen.add(index)
        before, after, native = (records[index] for records in maps)
        if not same_json(before, after):
            fail(f"{relative}: baseline/candidate frame metadata differs for {index}")
        for key, value in native.items():
            if key not in ("file", *GEOMETRY) and (key not in before or not same_json(before[key], value)):
                fail(f"{relative}: source frame metadata differs for {index}: {key}")
        for key in GEOMETRY:
            if before[key] != native[key] * scale:
                fail(f"{relative}: scaled source frame {index} differs: {key}")
        value = verify_frame(frame, (before, after, native), trees, relative, font)
        values.append(value)
        if frame["issues"]:
            suspects.append(index)
    if [frame["index"] for frame in frames] != sorted(maps[0]):
        fail(f"{relative}: report frame indices are not the complete sorted set")
    if integer(entry.get("frames_compared"), f"{relative}: frames_compared") != len(frames):
        fail(f"{relative}: frames_compared differs")
    suspect_indices = entry.get("suspect_frame_indices")
    if (not isinstance(suspect_indices, list) or any(type(index) is not int for index in suspect_indices)
            or suspect_indices != suspects):
        fail(f"{relative}: suspect frame list differs")
    if type(entry.get("suspect")) is not bool or entry["suspect"] != bool(suspects):
        fail(f"{relative}: suspect resource flag differs")
    metrics = entry.get("metrics")
    if not isinstance(metrics, dict):
        fail(f"{relative}: missing resource metrics")
    crossings, changes = sum(v[0] for v in values), sum(v[1] for v in values)
    old_sum, new_sum = sum(v[4] for v in values), sum(v[5] for v in values)
    for key, expected in (("rgb_changed_frames", 0), ("support_changed_frames", 0),
                          ("threshold_128_crossing_frames", crossings),
                          ("threshold_128_topology_changed_frames", changes),
                          ("threshold_128_added_pixels", sum(v[2] for v in values)),
                          ("threshold_128_removed_pixels", sum(v[3] for v in values))):
        if integer(metrics.get(key), f"{relative}: {key}") != expected:
            fail(f"{relative}: {key} differs")
    percentages = [abs(v[6]) for v in values if v[6] is not None]
    for key, expected in (("alpha_weighted_area_delta", (new_sum - old_sum) / 255),
                          ("alpha_weighted_area_delta_percent", (new_sum - old_sum) * 100 / old_sum if old_sum else None),
                          ("max_absolute_frame_area_delta_percent", max(percentages, default=None))):
        if key not in metrics:
            fail(f"{relative}: missing {key}")
        match_number(metrics.get(key), expected, f"{relative}: {key}")
    return bool(changes), len(frames), len(suspects), bool(suspects)


def validate_audit(audit_path: Path, baseline: Path, candidate: Path, source: Path) -> SelectionPlan:
    """Validate current trees against a trusted audit; raise ValidationError on failure."""
    audit_path = Path(audit_path)
    roots = tuple(root_path(Path(path), label) for path, label in zip((baseline, candidate, source), LABELS))
    for i, root in enumerate(roots):
        for other in roots[i + 1:]:
            if root == other or root.is_relative_to(other) or other.is_relative_to(root):
                fail("input trees must be distinct and non-overlapping")
    if audit_path.is_symlink():
        fail("audit report must not be a symlink")
    try:
        resolved_audit = audit_path.resolve()
    except (OSError, RuntimeError) as error:
        fail(f"invalid audit report path: {error}")
    if any(resolved_audit.is_relative_to(root) for root in roots):
        fail("audit report must be outside input trees")
    raw = read_regular(resolved_audit, contents=True)
    assert isinstance(raw, bytes)
    report = parse_json(raw, str(audit_path))
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or type(report.get("file_hashes_version")) is not int or report["file_hashes_version"] != 1
            or report.get("source_verification") != "decoded_rgba"):
        fail("audit requires schema 1, file hashes 1 and decoded_rgba source verification")
    inputs = report.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(LABELS) or any(
            inputs[label] != str(root) for label, root in zip(LABELS, roots)):
        fail("audit inputs do not match the requested roots")
    if report.get("errors") != []:
        fail("audit has global errors or malformed errors")
    entries = report.get("resources")
    if not isinstance(entries, list):
        fail("audit resources must be a list")
    resources = {}
    for entry in entries:
        if not isinstance(entry, dict):
            fail("audit resource must be an object")
        name = relative_name(entry.get("resource"))
        if name in resources:
            fail(f"duplicate audit resource: {name}")
        resources[name] = entry
    trees = tuple(discover(root) for root in roots)
    verify_trees(trees, resources)
    fallbacks, frames, suspects, suspect_resources = [], 0, 0, 0
    for relative in sorted(resources):
        fallback, frame_count, suspect_count, suspect = verify_resource(relative, resources[relative], trees)
        if fallback:
            if relative == ".":
                fail("root resource fallback is not assembler-compatible; audit its parent directory")
            fallbacks.append(relative)
        frames += frame_count
        suspects += suspect_count
        suspect_resources += suspect
    summary = report.get("summary")
    if not isinstance(summary, dict):
        fail("audit summary is missing")
    for key, expected in (("resources", len(resources)), ("suspect_resources", suspect_resources),
                          ("frames_compared", frames), ("suspect_frames", suspects), ("global_errors", 0)):
        if integer(summary.get(key), f"summary: {key}") != expected:
            fail(f"summary: {key} differs")
    return SelectionPlan(*roots, tuple(fallbacks), hashlib.sha256(raw).hexdigest())


def write_plan(path: Path, plan: SelectionPlan, audit_path: Path) -> None:
    try:
        output = path.resolve()
        audit_path = audit_path.resolve()
    except (OSError, RuntimeError) as error:
        fail(f"invalid plan or audit path: {error}")
    if any(output.is_relative_to(root) for root in plan[:3]) or output == audit_path:
        fail("plan must be outside input trees and must not replace the audit")
    if os.path.lexists(path):
        fail("plan already exists")
    if not output.parent.is_dir():
        fail("plan parent directory must already exist")
    data = {"schema_version": 1, "audit_sha256": plan.audit_sha256,
            "baseline_root": str(plan.baseline_root), "candidate_root": str(plan.candidate_root),
            "source_root": str(plan.source_root), "fallback_resources": list(plan.fallback_resources)}
    try:
        fd, temporary = tempfile.mkstemp(prefix=".contour-selection-plan-", suffix=".tmp", dir=output.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
            os.link(temporary, output)
        finally:
            os.unlink(temporary)
    except OSError as error:
        fail(f"cannot publish new plan: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="Optional new plan JSON outside all input trees")
    args = parser.parse_args(argv)
    try:
        plan = validate_audit(args.audit, args.baseline, args.candidate, args.source)
        if args.plan:
            write_plan(args.plan, plan, args.audit)
    except (ValidationError, OSError) as error:
        print(f"Validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"baseline_root": str(plan.baseline_root), "candidate_root": str(plan.candidate_root),
                      "source_root": str(plan.source_root), "fallback_resources": plan.fallback_resources,
                      "audit_sha256": plan.audit_sha256, "plan": str(args.plan) if args.plan else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
