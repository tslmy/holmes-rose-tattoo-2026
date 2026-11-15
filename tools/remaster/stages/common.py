"""Helpers shared by stage implementations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from ..config import Config, TOOLS_DIR

STAMP = ".remaster-stamp.json"
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def sha256_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _HASH_CACHE:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        _HASH_CACHE[key] = digest.hexdigest()
    return _HASH_CACHE[key]


def fingerprint(*parts: Any) -> str:
    """Content hash of inputs: Paths hash their bytes, everything else its JSON."""
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            digest.update(sha256_file(part).encode() if part.is_file() else f"missing:{part}".encode())
        else:
            digest.update(json.dumps(part, sort_keys=True, default=str).encode())
        digest.update(b"\0")
    return digest.hexdigest()[:20]


def tool(name: str) -> Path:
    return TOOLS_DIR / name


def run_tool(cfg: Config, script: str, args: Iterable[str]) -> str:
    """Run an existing tools/ script unchanged; raise with its output on failure."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TOOLS_DIR), env.get("PYTHONPATH")]))
    cmd = [sys.executable, str(tool(script)), *map(str, args)]
    print("$ " + " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=cfg.root, env=env, capture_output=True, text=True)
    sys.stdout.write(proc.stdout[-6000:])
    if proc.returncode != 0:
        sys.stdout.write(proc.stderr[-6000:])
        raise RuntimeError(f"{script} exited {proc.returncode}: {proc.stderr.strip()[-800:]}")
    return proc.stdout


def scratch_dir(cfg: Config, label: str) -> Path:
    base = cfg.work / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=label.replace("/", "_") + "-", dir=base))


def install_dir(source: Path, target: Path) -> None:
    """Move a finished output directory into its stable location atomically.

    The previous version (if any) is moved aside first and removed only after
    the new one is in place, so an interrupted install never leaves a mix.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.with_name(target.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        os.replace(target, old)
    os.replace(source, target)
    if old.exists():
        shutil.rmtree(old)


def read_stamp(directory: Path) -> dict:
    try:
        return json.loads((directory / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_stamp(directory: Path, fp: str, **extra: Any) -> None:
    data = {"fingerprint": fp, **extra}
    tmp = directory / (STAMP + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, directory / STAMP)


def up_to_date(directory: Path, fp: str) -> bool:
    return read_stamp(directory).get("fingerprint") == fp


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def rrm(cfg: Config, scene: int) -> Path:
    return cfg.game_data / f"RES{scene:02d}.RRM"


def resolve_scenes(cfg: Config, scenes: list[int] | None, all_scenes: bool = False) -> list[int]:
    if scenes:
        return sorted(set(scenes))
    if all_scenes:
        import re

        found = []
        for path in cfg.game_data.glob("RES*.RRM"):
            match = re.fullmatch(r"RES(\d+)\.RRM", path.name, re.IGNORECASE)
            if match:
                found.append(int(match.group(1)))
        return sorted(found)
    return cfg.golden_scenes
