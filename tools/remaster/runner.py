"""`remaster run`: execute pending jobs unattended.

- One runner at a time (a flock on work/state/runner.lock).
- Re-executes itself under `caffeinate -ims` on macOS so idle sleep does not
  stall a batch; if the machine sleeps or reboots anyway, the next runner
  start recovers orphaned jobs (jobs.JobQueue.recover).
- Each job runs in a child process, so a crash in a model or extractor marks
  only that job failed/pending; its output goes to work/state/logs/.
- Free disk space is checked before every batch and every job. macOS can
  report much less free space than is really reclaimable: Time Machine local
  snapshots pin deleted files for about a day. The check therefore reports
  free and purgeable space separately. Short only because of purgeable space,
  the runner raises a `disk-purgeable` review item and waits for the
  snapshots to age out (up to `disk_wait_minutes`); short for real, it stops
  with a `disk-low` item. It never deletes anything itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
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


@dataclass
class DiskSpace:
    free_gb: float        # what df reports: usable right now
    purgeable_gb: float   # extra macOS would reclaim on demand (snapshots, caches)
    snapshots: int        # Time Machine local snapshots on the volume (-1 unknown)

    @property
    def available_gb(self) -> float:
        return self.free_gb + self.purgeable_gb

    def describe(self) -> str:
        snaps = f", {self.snapshots} local snapshot(s)" if self.snapshots >= 0 else ""
        return f"free {self.free_gb:.1f} GB, purgeable {self.purgeable_gb:.1f} GB{snaps}"


_JXA = ('ObjC.import("Foundation");var u=$.NSURL.fileURLWithPath(%s);'
        'var d=u.resourceValuesForKeysError(["NSURLVolumeAvailableCapacityForImportantUsageKey"],$());'
        'String(ObjC.unwrap(d.objectForKey("NSURLVolumeAvailableCapacityForImportantUsageKey")))')


def _important_capacity(path: Path) -> float | None:
    """Finder's "available" figure (free + purgeable), via NSURL; None off macOS."""
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return None
    try:
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA % json.dumps(str(path))],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return float(out) / 1e9
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _snapshot_count(path: Path) -> int:
    if sys.platform != "darwin" or not shutil.which("tmutil"):
        return -1
    try:
        out = subprocess.run(["tmutil", "listlocalsnapshots", str(path)], capture_output=True, text=True,
                             timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return -1
    return sum(1 for line in out.splitlines() if "com.apple.TimeMachine" in line)


def disk_space(path: Path) -> DiskSpace:
    free = free_gb(path)
    important = _important_capacity(path)
    purgeable = max(0.0, important - free) if important is not None else 0.0
    return DiskSpace(free, purgeable, _snapshot_count(path) if purgeable > 0.5 else 0)


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


def _disk_ok(cfg: Config, need_gb: float, review: ReviewQueue, *, probe=None, sleep=time.sleep,
             wait_minutes: float | None = None) -> bool:
    """True when `need_gb` plus the reserve is free. Never deletes anything.

    Short for real (free + purgeable below the line): raise `disk-low`, return False.
    Short only because of purgeable space: raise `disk-purgeable` and wait, re-checking every
    few minutes, up to `disk_wait_minutes`; then return whether space came back.
    """
    probe = probe or disk_space
    minimum = cfg.threshold("min_free_disk_gb", 15)
    line = minimum + need_gb
    space = probe(cfg.work)
    if space.free_gb >= line:
        return True
    stamp = time.strftime('%Y-%m-%d %H:%M')
    if space.available_gb < line:
        review.add(ReviewItem(
            id="disk-low", kind="disk",
            title=f"Disk short: {space.describe()}; runner needs {line:.1f} GB and paused",
            default="Runner stops before the next job; nothing is deleted. Old pilot trees stay.",
            options=["free space yourself, then `uv run remaster run`",
                     "lower thresholds.min_free_disk_gb in remaster.toml",
                     "approve deleting a named pilot tree (see docs/pilot-lessons.md)"],
            detail=f"Work dir: {cfg.work}. Checked {stamp}.",
        ))
        print(f"[runner] {space.describe()} < {line:.1f} GB; pausing (see review/queue.md)", flush=True)
        return False
    wait = cfg.threshold("disk_wait_minutes", 360) if wait_minutes is None else wait_minutes
    review.add(ReviewItem(
        id="disk-purgeable", kind="disk",
        title=f"Free disk {space.free_gb:.1f} GB is below {line:.1f} GB, but {space.purgeable_gb:.1f} GB "
              "is purgeable (Time Machine local snapshots)",
        default=f"Runner waits up to {wait:.0f} min for the snapshots to age out (hourly snapshots expire "
                "after about 24 h), then pauses; nothing is deleted",
        options=["delete the local snapshots yourself: `tmutil listlocalsnapshots /` then "
                 "`sudo tmutil deletelocalsnapshots <date>`",
                 "lower thresholds.min_free_disk_gb or disk_wait_minutes in remaster.toml"],
        detail=f"{space.describe()} at {stamp}. Work dir: {cfg.work}. df under-reports reclaimable space "
               "after large deletions while snapshots still reference the deleted blocks.",
    ))
    print(f"[runner] {space.describe()}; below {line:.1f} GB only because of purgeable space; "
          f"waiting up to {wait:.0f} min (see review/queue.md)", flush=True)
    deadline = time.monotonic() + wait * 60
    while time.monotonic() < deadline:
        sleep(min(300.0, max(1.0, deadline - time.monotonic())))
        space = probe(cfg.work)
        if space.free_gb >= line:
            review.mark_done("disk-purgeable", f"Space came back at {time.strftime('%Y-%m-%d %H:%M')}: "
                                               f"{space.describe()}.")
            print(f"[runner] {space.describe()}; resuming", flush=True)
            return True
        if space.available_gb < line:
            break
    print(f"[runner] still {space.describe()}; pausing", flush=True)
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
