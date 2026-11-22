"""Load remaster.toml and resolve its paths."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_CONFIG = REPO_ROOT / "remaster.toml"


@dataclass
class Config:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.path.parent

    def _path(self, key: str, default: str) -> Path:
        value = Path(self.raw.get("paths", {}).get(key, default))
        return value if value.is_absolute() else self.root / value

    @property
    def game_data(self) -> Path:
        return self._path("game_data", "scummvm")

    @property
    def work(self) -> Path:
        return self._path("work", "work")

    @property
    def review_dir(self) -> Path:
        return self._path("review", "review")

    @property
    def state_dir(self) -> Path:
        return self.work / "state"

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    @property
    def golden(self) -> dict[str, Any]:
        return self.section("golden")

    @property
    def golden_scenes(self) -> list[int]:
        return [int(s) for s in self.golden.get("scenes", [])]

    @property
    def golden_canims(self) -> list[tuple[int, int]]:
        result = []
        for item in self.golden.get("canims", []):
            scene, index = str(item).split("/")
            result.append((int(scene), int(index)))
        return result

    def threshold(self, key: str, default: float) -> float:
        return float(self.section("thresholds").get(key, default))


def load_config(path: Path | None = None) -> Config:
    path = (path or DEFAULT_CONFIG).resolve()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return Config(path=path, raw=raw)
