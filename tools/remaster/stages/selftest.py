"""`selftest`: slow no-op jobs for checking that the runner resumes.

`uv run remaster selftest --count 3 --seconds 20`, interrupt it (Ctrl-C, kill
-9, sleep, reboot), then `uv run remaster run`: finished jobs stay finished,
the interrupted one is recovered and the rest run. Output goes to
work/selftest/.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..config import Config
from ..jobs import JobSpec

NAME = "selftest"
SUMMARY = "Slow no-op jobs to exercise crash/sleep/reboot recovery"
HIDDEN = True


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    count = getattr(args, "count", 3) or 3
    seconds = getattr(args, "seconds", 5.0) or 5.0
    return [JobSpec(NAME, f"job/{i:02d}", {"index": i, "seconds": seconds}, f"selftest-{seconds}")
            for i in range(count)]


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    out = cfg.work / "selftest"
    out.mkdir(parents=True, exist_ok=True)
    marker = out / f"job_{params['index']:02d}.txt"
    with (out / "starts.log").open("a") as log:
        log.write(f"{params['index']}\n")
    time.sleep(float(params["seconds"]))
    marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    return {"marker": str(Path(marker).relative_to(cfg.root))}
