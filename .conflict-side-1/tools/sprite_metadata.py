"""Discover frame manifests, ignoring per-scene summary metadata."""

import json
import re
from pathlib import Path


def resource_metadata_paths(root: Path) -> list[Path]:
    paths = []
    for path in sorted(root.rglob("metadata.json")):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if "frames" in metadata:
            if not isinstance(metadata["frames"], list):
                raise ValueError(f"{path}: frames must be a list")
            paths.append(path)
        elif not re.fullmatch(r"scene_\d+", path.parent.name):
            raise ValueError(f"{path}: missing frames")
    return paths
