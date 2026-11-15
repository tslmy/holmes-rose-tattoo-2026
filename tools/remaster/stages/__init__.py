"""Stage registry, in pipeline order."""

from __future__ import annotations

from . import baseline, brief, delegate, extract, geometry, prepare, qa, redraw_bg, selftest, sprite_test
from .stubs import STUBS

_ORDER = ["extract", "prepare", "brief", "baseline", "redraw-bg", "cast", "redraw-sprites", "geometry",
          "interpolate", "qa", "sprite-test", "assemble", "smoke", "delegate"]

STAGES = {stage.NAME: stage for stage in (delegate, extract, prepare, brief, baseline, redraw_bg, geometry, qa, sprite_test,
                                          selftest, *STUBS)}
PIPELINE = [name for name in _ORDER if name in STAGES]


def get(name: str):
    try:
        return STAGES[name]
    except KeyError:
        raise SystemExit(f"unknown stage {name!r}; known: {', '.join(PIPELINE)}, run, status, review")
