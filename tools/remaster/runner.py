"""`remaster run`: execute pending jobs unattended.

- One runner at a time (a flock on work/state/runner.lock).
- Re-executes itself under `caffeinate -ims` on macOS so idle sleep does not
  stall a batch; if the machine sleeps or reboots anyway, the next runner
  start recovers orphaned jobs (jobs.JobQueue.recover).
- Each job runs in a child process, so a crash in a model or extractor marks
  only that job failed/pending; its output goes to work/state/logs/.
- Free disk space is checked before every batch and every job. Below the
  threshold the runner stops and raises a `disk` item in the review queue
  instead of deleting anything.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import Config, TOOLS_DIR
from .jobs import Job, JobQueue
from .review import ReviewItem, ReviewQueue

EXIT_OK, EXIT_FAILED, EXIT_LOCKED, EXIT_DISK = 0, 1, 2, 3


def queue_for(cfg: Config) -> JobQueue:
    return JobQueue(cfg.state_dir / "jobs.sqlite")


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def ensure_caffeinated(argv: list[str]) -> None:
    """Replace this process with `caffeinate -ims <same command>` once."""
    if os.environ.get("REMASTER_CAFFEINATED") or sys.platform != "darwin" or not shutil.which("caffeinate"):
        return
    env = dict(os.environ, REMASTER_CAFFEINATED="1")
    os.execvpe("caffeinate", ["caffeinate", "-ims", sys.executable, "-m", "remaster.cli", *argv], env)


class RunnerLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self) -> "RunnerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise RuntimeError(f"another remaster runner holds {self.path}")
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def _disk_ok(cfg: Config, need_gb: float, review: ReviewQueue) -> bool:
    minimum = cfg.threshold("min_free_disk_gb", 15)
    free = free_gb(cfg.work)
    if free >= minimum + need_gb:
        return True
    review.add(ReviewItem(
        id="disk-low", kind="disk",
        title=f"Free disk {free:.1f} GB is below {minimum + need_gb:.1f} GB; runner paused",
        default="Runner stops before the next job; nothing is deleted. Old pilot trees stay.",
        options=["free space yourself, then `uv run remaster run`",
                 "lower thresholds.min_free_disk_gb in remaster.toml",
                 "approve deleting a named pilot tree (see docs/pilot-lessons.md)"],
        detail=f"Work dir: {cfg.work}. Checked {time.strftime('%Y-%m-%d %H:%M')}.",
    ))
    print(f"[runner] free disk {free:.1f} GB < {minimum + need_gb:.1f} GB; pausing (see review/queue.md)",
          flush=True)
    return False


def run_child(cfg: Config, queue: JobQueue, job: Job) -> tuple[bool, dict | str]:
    log_dir = cfg.state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (job.id.replace(":", "__").replace("/", "_") + ".log")
    result_path = cfg.state_dir / "results" / (job.id.replace(":", "__").replace("/", "_") + ".json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.unlink(missing_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TOOLS_DIR), env.get("PYTHONPATH")]))
    cmd = [sys.executable, "-m", "remaster.cli", "--config", str(cfg.path), "_job", job.id,
           "--result", str(result_path)]
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} attempt {job.attempts} ===\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=cfg.root, env=env)
        queue.set_pid(job.id, proc.pid)
        code = proc.wait()
    if code == 0 and result_path.exists():
        return True, json.loads(result_path.read_text(encoding="utf-8"))
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
    return False, f"exit {code}; log {log_path}\n{tail}"


def run(cfg: Config, only: set[str] | None = None, stages: set[str] | None = None,
        batch_size: int = 10, max_jobs: int | None = None, verbose: bool = True) -> int:
    review = ReviewQueue(cfg.review_dir, cfg.root)
    try:
        lock = RunnerLock(cfg.state_dir / "runner.lock").__enter__()
    except RuntimeError as exc:
        print(f"[runner] {exc}", file=sys.stderr)
        return EXIT_LOCKED
    queue = queue_for(cfg)
    try:
        recovered = queue.recover()
        for job_id in recovered:
            print(f"[runner] recovered {job_id}", flush=True)
        ran = failed = 0
        while True:
            if not _disk_ok(cfg, 0.0, review):
                return EXIT_DISK
            batch_ran = 0
            while batch_ran < batch_size:
                if max_jobs is not None and ran >= max_jobs:
                    return EXIT_FAILED if failed else EXIT_OK
                job = queue.claim_next(only=only, stages=stages)
                if job is None:
                    break
                if job.est_gb and not _disk_ok(cfg, job.est_gb, review):
                    queue.release(job.id)
                    return EXIT_DISK
                started = time.time()
                if verbose:
                    print(f"[runner] {job.id} (attempt {job.attempts}) ...", flush=True)
                ok, payload = run_child(cfg, queue, job)
                if ok:
                    queue.complete(job.id, payload if isinstance(payload, dict) else {})
                    if verbose:
                        print(f"[runner] {job.id} done in {time.time() - started:.1f}s", flush=True)
                else:
                    status = queue.fail(job.id, str(payload))
                    failed += status == "failed"
                    print(f"[runner] {job.id} FAILED ({status}): {str(payload).splitlines()[0]}", flush=True)
                ran += 1
                batch_ran += 1
            if batch_ran == 0:
                break
        pending = [j.id for j in queue.iter_jobs("pending") if (only is None or j.id in only)
                   and (stages is None or j.stage in stages)]
        counts = queue.counts()
        if verbose:
            print(f"[runner] ran {ran} job(s); queue: {counts}", flush=True)
        if pending:
            print(f"[runner] {len(pending)} job(s) still pending (blocked by failed prerequisites?)", flush=True)
        any_failed = any(j.status == "failed" for j in queue.iter_jobs("failed")
                         if (only is None or j.id in only) and (stages is None or j.stage in stages))
        return EXIT_FAILED if (failed or any_failed or pending) else EXIT_OK
    finally:
        queue.close()
        lock.__exit__(None, None, None)


LAUNCHD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.shcrt.remaster-runner</string>
  <key>ProgramArguments</key><array>
    <string>{uv}</string><string>run</string><string>remaster</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key><string>{root}/logs/remaster/runner.log</string>
  <key>StandardErrorPath</key><string>{root}/logs/remaster/runner.log</string>
</dict></plist>
"""


def launchd_plist(cfg: Config) -> str:
    return LAUNCHD_TEMPLATE.format(uv=shutil.which("uv") or "uv", root=cfg.root)
