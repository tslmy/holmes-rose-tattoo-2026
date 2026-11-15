"""`delegate`: hand engineering tasks to headless OpenCode on the local-model endpoints.

A task is a TOML file in tools/remaster/delegate_tasks/ (tracked):
  prompt = "what to do"            # the spec; interfaces and acceptance criteria included
  files  = ["tools/tests/x.py"]    # the only paths the agent may change
  test   = "python -m unittest tests.test_x"   # acceptance test, run from tools/ with the venv
  model  = "lmstudio-remote/qwen/qwen3.8-27b"  # optional; the wrapper's default otherwise

One job per task -> work/delegate/<task>/: agent transcript, diff of the allowed
files, test output and record.json (seconds, test result, files touched). A
failed acceptance test fails the job, so the runner retries it once with the
test output appended to the prompt (up to thresholds.job_max_attempts); after that it stays failed
for the reviewer (Opus) to take over, and the record says why.

Edits outside `files` are reported, never reverted: other agents share this
checkout, and a change there may be theirs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from ..config import Config, TOOLS_DIR
from ..jobs import JobSpec
from . import common

NAME = "delegate"
SUMMARY = "Engineering tasks run by headless OpenCode on the local LLM, checked by an acceptance test"
TASKS = Path(__file__).resolve().parents[1] / "delegate_tasks"
WRAPPER = TOOLS_DIR / "opencode" / "opencode-local.sh"
CODE = [Path(__file__)]
RULES = ("Rules: change only these files: {files}. Do not run git or but, do not commit, do not touch any other "
         "file, do not install packages. When done, run the acceptance test yourself: `{test}` (from the tools/ "
         "directory, with ../.venv/bin/python) and fix until it passes.")


def tasks(names: list[str] | None = None) -> dict[str, dict]:
    found = {p.stem: tomllib.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS.glob("*.toml"))}
    return {k: v for k, v in found.items() if not names or k in names}


def out_dir(cfg: Config, name: str) -> Path:
    return cfg.work / "delegate" / name


def plan(cfg: Config, args: argparse.Namespace) -> list[JobSpec]:
    specs = []
    for name, task in tasks(args.resources).items():
        fp = common.fingerprint(task, *CODE)
        specs.append(JobSpec(NAME, name, {"task": name}, fp))
    return specs


def _digest(paths: list[Path]) -> dict[str, str]:
    out = {}
    for p in paths:
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
    return out


def _git_changed(root: Path) -> set[str]:
    proc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
                          capture_output=True, text=True)
    return {line[3:].strip() for line in proc.stdout.splitlines() if line.strip()}


def run_test(cfg: Config, test: str) -> tuple[bool, str]:
    cmd = test.replace("python ", f"{cfg.root / '.venv' / 'bin' / 'python'} ", 1) if test.startswith("python ") else test
    proc = subprocess.run(cmd, shell=True, cwd=TOOLS_DIR, capture_output=True, text=True, timeout=1800)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-6000:]


def run_job(cfg: Config, params: dict, fingerprint: str) -> dict:
    name = params["task"]
    task = tasks([name])[name]
    out = out_dir(cfg, name)
    out.mkdir(parents=True, exist_ok=True)
    allowed = [cfg.root / f for f in task["files"]]
    before_hash = _digest(allowed)
    before_dirty = _git_changed(cfg.root)
    attempt = len(list(out.glob("transcript-*.txt"))) + 1
    prompt = task["prompt"].strip() + "\n\n" + RULES.format(files=", ".join(task["files"]), test=task["test"])
    last = out / "test-output.txt"
    if attempt > 1 and last.exists():
        prompt += "\n\nThe previous attempt failed the acceptance test with:\n" + last.read_text()[-3000:]
    env = dict(os.environ)
    if task.get("model"):
        env["OPENCODE_MODEL"] = task["model"]
    started = time.monotonic()
    proc = subprocess.run([str(WRAPPER), "--auto", prompt], cwd=cfg.root, env=env, capture_output=True, text=True,
                          timeout=int(task.get("timeout_s", 3600)))
    seconds = round(time.monotonic() - started, 1)
    (out / f"transcript-{attempt}.txt").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    after_hash = _digest(allowed)
    changed = [f for f, p in zip(task["files"], allowed) if before_hash[str(p)] != after_hash[str(p)]]
    outside = sorted(_git_changed(cfg.root) - before_dirty - set(task["files"]))
    diff = subprocess.run(["git", "diff", "--no-color", "--", *task["files"]], cwd=cfg.root, capture_output=True,
                          text=True).stdout
    untracked = [f for f in task["files"] if (cfg.root / f).exists() and f in _git_changed(cfg.root)
                 and subprocess.run(["git", "ls-files", "--error-unmatch", f], cwd=cfg.root,
                                    capture_output=True).returncode]
    for f in untracked:
        diff += f"\n--- new file {f} ---\n" + (cfg.root / f).read_text(encoding="utf-8", errors="replace")
    (out / f"diff-{attempt}.patch").write_text(diff, encoding="utf-8")
    passed, output = run_test(cfg, task["test"])
    last.write_text(output, encoding="utf-8")
    record = {"task": name, "attempt": attempt, "agent_exit": proc.returncode, "agent_seconds": seconds,
              "model": task.get("model", "lmstudio-remote/qwen/qwen3.8-27b"), "files_changed": changed,
              "outside_scope": outside, "test_passed": passed, "date": time.strftime("%Y-%m-%d %H:%M")}
    common.write_json(out / f"record-{attempt}.json", record)
    common.write_json(out / "record.json", record)
    print(output[-2000:])
    if not passed:
        raise RuntimeError(f"acceptance test failed after attempt {attempt}; see {last}")
    if outside:
        print(f"note: files changed outside the task's scope (not reverted): {outside}", file=sys.stderr)
    return record
