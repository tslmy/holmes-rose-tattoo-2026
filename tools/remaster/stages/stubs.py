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
    StubStage("brief", "Per-scene prompt briefs via the OpenAI-compatible LLM endpoint",
              "for each scene, send the extracted descriptions and object names to [llm].text_model and "
              "write a short photoreal visual brief to work/brief/scene_NNN/visual_brief.txt",
              "Phase 1", "tools/polish_rosetattoo_prompts.py --provider openai (already ported)"),
    StubStage("redraw-bg", "Photoreal re-render of each plate, then tiled detail pass to 4x",
              "re-render work/prepare/scene_NNN/plate_dedither.png with the [image].backgrounds backend, "
              "conditioned on edges.png and depth, protecting writing/clue regions; then a tiled pass to 4x",
              "Phase 1-2", "tools/img2img/*.py"),
    StubStage("cast", "Identity sheets per character, for human approval",
              "generate photoreal identity sheets (face, costume, angles) per character from the original "
              "sprites; queue each in review/queue.md and continue on the top-ranked sheet",
              "Phase 3"),
    StubStage("redraw-sprites", "Cut-outs from plates; identity-conditioned sequence redraw; per-room grade",
              "cut static foreground layers out of the redrawn plate using plate_layers.png; redraw sprite "
              "sequences conditioned on frame + cast sheet; derive per-room grades from palette projections",
              "Phase 3", "tools/upscale_rosetattoo_sprites.py (SR fallback)"),
    StubStage("geometry", "Room warp, segmentation of new art, alpha and geometry sidecar",
              "estimate an invertible warp original->redraw anchored on named objects, segment new art for "
              "alpha seeded by the original mask, write the geometry sidecar and check invertibility",
              "Phase 1-3"),
    StubStage("interpolate", "Offline in-between frames for animated resources",
              "generate (frame A, frame B, k) in-betweens on premultiplied RGBA, with per-pair ghosting check",
              "Phase 5"),
    StubStage("qa", "Deterministic gates, VLM review, contact sheets, review-queue entries",
              "run the gates in docs/remaster-plan.md, ask [llm].vision_model what was added/removed, build "
              "HTML contact sheets, raise review items and re-queue failures with changed settings",
              "Phase 1", "tools/audit_rosetattoo_sprite_contours.py, tools/build_variant_contact_sheet.py"),
    StubStage("assemble", "Existing assembler + pack audit",
              "assemble a new pack from the selected trees and audit it",
              "Phase 2", "tools/assemble_rosetattoo_modernized.py, tools/audit_rosetattoo_pack.py"),
    StubStage("smoke", "Existing scene-start smoke test + fixtures",
              "launch each scene with an isolated save copy and wait for the rendered-frame marker",
              "Phase 2", "tools/smoke_rosetattoo_scenes.py"),
]
