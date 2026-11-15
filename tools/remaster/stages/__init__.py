"""Stage registry, in pipeline order."""

from __future__ import annotations

from . import extract, prepare, selftest
from .stubs import STUBS

_ORDER = ["extract", "prepare", "brief", "redraw-bg", "cast", "redraw-sprites", "geometry",
          "interpolate", "qa", "assemble", "smoke"]

STAGES = {stage.NAME: stage for stage in (extract, prepare, selftest, *STUBS)}
PIPELINE = [name for name in _ORDER if name in STAGES]


def get(name: str):
    try:
        return STAGES[name]
    except KeyError:
        raise SystemExit(f"unknown stage {name!r}; known: {', '.join(PIPELINE)}, run, status, review")
