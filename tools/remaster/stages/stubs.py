"""Registered stages that are not implemented yet: each says what it will do."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from ..config import Config
from ..jobs import JobSpec


@dataclass
class StubStage:
    NAME: str
    SUMMARY: str
    PLAN: str
    PHASE: str
    WRAPS: str = ""

    def plan(self, cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
        print(f"[{self.NAME}] not implemented yet ({self.PHASE}).")
        print(f"[{self.NAME}] will: {self.PLAN}")
        if self.WRAPS:
            print(f"[{self.NAME}] existing tools to wrap: {self.WRAPS}")
        return []

    def run_job(self, cfg: Config, params: dict, fingerprint: str) -> dict:
        raise NotImplementedError(self.NAME)


STUBS = [
    StubStage("cast", "Identity sheets per character, for human approval",
              "generate photoreal identity sheets (face, costume, angles) per character from the original "
              "sprites; queue each in review/queue.md and continue on the top-ranked sheet",
              "Phase 3"),
    StubStage("redraw-sprites", "Cut-outs from plates; identity-conditioned sequence redraw; per-room grade",
              "cut static foreground layers out of the redrawn plate using plate_layers.png; redraw sprite "
              "sequences conditioned on frame + cast sheet; derive per-room grades from palette projections",
              "Phase 3", "tools/upscale_rosetattoo_sprites.py (SR fallback)"),
    StubStage("interpolate", "Offline in-between frames for animated resources",
              "generate (frame A, frame B, k) in-betweens on premultiplied RGBA, with per-pair ghosting check",
              "Phase 5"),
    StubStage("assemble", "Existing assembler + pack audit",
              "assemble a new pack from the selected trees and audit it",
              "Phase 2", "tools/assemble_rosetattoo_modernized.py, tools/audit_rosetattoo_pack.py"),
    StubStage("smoke", "Existing scene-start smoke test + fixtures",
              "launch each scene with an isolated save copy and wait for the rendered-frame marker",
              "Phase 2", "tools/smoke_rosetattoo_scenes.py"),
]
