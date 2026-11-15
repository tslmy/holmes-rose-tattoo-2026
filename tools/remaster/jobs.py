"""Persistent, resumable job queue (SQLite under work/state/jobs.sqlite).

A job is one unit of stage work (one scene, one resource). Its id is
`<stage>:<key>`. Jobs are idempotent: enqueueing an existing id keeps a
finished job when its fingerprint (content hash of its inputs and settings)
is unchanged, and resets it to pending otherwise.

Crash/reboot recovery: a job left `running` whose recorded pid is no longer
alive (or whose host booted since it started) is put back to `pending` when
any runner starts. Jobs can name prerequisites (`after`), and carry tags
naming the review-queue items whose provisional defaults they depend on.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    key TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL DEFAULT '',
    after TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    est_gb REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    pid INTEGER,
    host TEXT,
    boot_time REAL,
    error TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    started REAL,
    finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, seq);
"""

STATUSES = ("pending", "running", "done", "failed")


@dataclass
class JobSpec:
    stage: str
    key: str
    params: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    after: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    est_gb: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.stage}:{self.key}"


@dataclass
class Job:
    id: str
    stage: str
    key: str
    params: dict[str, Any]
    fingerprint: str
    after: list[str]
    tags: list[str]
    est_gb: float
    status: str
    attempts: int
    max_attempts: int
    pid: int | None
    error: str
    result: dict[str, Any]


def boot_time() -> float:
    """Kernel boot time (seconds since epoch); 0 if unknown."""
    try:
        import subprocess

        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        # "{ sec = 1731500000, usec = 123 } ..."
        return float(out.split("sec =")[1].split(",")[0])
    except Exception:
        try:
            with open("/proc/stat") as handle:
                for line in handle:
                    if line.startswith("btime"):
                        return float(line.split()[1])
        except OSError:
            pass
    return 0.0


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobQueue:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- writing ---------------------------------------------------------
    def enqueue(self, spec: JobSpec, max_attempts: int = 3) -> str:
        """Insert or refresh a job; returns its resulting status."""
        now = time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT status, fingerprint FROM jobs WHERE id=?", (spec.id,)).fetchone()
            payload = (json.dumps(spec.params, sort_keys=True), spec.fingerprint, json.dumps(spec.after),
                       json.dumps(sorted(set(spec.tags))), spec.est_gb, max_attempts)
            if row is None:
                seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM jobs").fetchone()[0]
                self.db.execute(
                    "INSERT INTO jobs (id, stage, key, params, fingerprint, after, tags, est_gb, max_attempts,"
                    " seq, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (spec.id, spec.stage, spec.key, *payload, seq, now, now),
                )
                status = "pending"
            elif row["status"] == "running":
                status = "running"  # leave an active job alone
            elif row["status"] == "done" and row["fingerprint"] == spec.fingerprint:
                self.db.execute("UPDATE jobs SET params=?, after=?, tags=?, est_gb=?, updated=? WHERE id=?",
                                (payload[0], payload[2], payload[3], payload[4], now, spec.id))
                status = "done"
            else:
                self.db.execute(
                    "UPDATE jobs SET params=?, fingerprint=?, after=?, tags=?, est_gb=?, max_attempts=?,"
                    " status='pending', attempts=0, error='', updated=? WHERE id=?",
                    (*payload, now, spec.id),
                )
                status = "pending"
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return status

    def recover(self) -> list[str]:
        """Return jobs orphaned by a crash, sleep-kill or reboot to pending."""
        recovered = []
        current_boot = boot_time()
        host = socket.gethostname()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for row in self.db.execute("SELECT id, pid, host, boot_time FROM jobs WHERE status='running'").fetchall():
                rebooted = bool(row["boot_time"]) and current_boot and abs(row["boot_time"] - current_boot) > 1
                if rebooted or row["host"] != host or not pid_alive(row["pid"]):
                    self.db.execute(
                        "UPDATE jobs SET status='pending', pid=NULL, updated=?,"
                        " error=error || ? WHERE id=?",
                        (time.time(), f"[recovered after {'reboot' if rebooted else 'lost runner'}] ", row["id"]),
                    )
                    recovered.append(row["id"])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return recovered

    def claim_next(self, only: set[str] | None = None, stages: set[str] | None = None) -> Job | None:
        """Atomically take the oldest runnable pending job."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            done = {r[0] for r in self.db.execute("SELECT id FROM jobs WHERE status='done'")}
            known = {r[0] for r in self.db.execute("SELECT id FROM jobs")}
            for row in self.db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY seq").fetchall():
                if only is not None and row["id"] not in only:
                    continue
                if stages is not None and row["stage"] not in stages:
                    continue
                deps = json.loads(row["after"])
                # A prerequisite that was never enqueued does not block.
                if any(dep in known and dep not in done for dep in deps):
                    continue
                now = time.time()
                self.db.execute(
                    "UPDATE jobs SET status='running', attempts=attempts+1, pid=?, host=?, boot_time=?,"
                    " started=?, updated=? WHERE id=?",
                    (os.getpid(), socket.gethostname(), boot_time(), now, now, row["id"]),
                )
                self.db.execute("COMMIT")
                return self.get(row["id"])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return None

    def set_pid(self, job_id: str, pid: int) -> None:
        self.db.execute("UPDATE jobs SET pid=?, updated=? WHERE id=?", (pid, time.time(), job_id))

    def complete(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        now = time.time()
        self.db.execute(
            "UPDATE jobs SET status='done', pid=NULL, error='', result=?, finished=?, updated=? WHERE id=?",
            (json.dumps(result or {}, sort_keys=True), now, now, job_id),
        )

    def fail(self, job_id: str, error: str) -> str:
        row = self.db.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        status = "failed" if row["attempts"] >= row["max_attempts"] else "pending"
        self.db.execute("UPDATE jobs SET status=?, pid=NULL, error=?, updated=? WHERE id=?",
                        (status, error[-4000:], time.time(), job_id))
        return status

    def release(self, job_id: str) -> None:
        """Put a claimed job back without counting the attempt (e.g. low disk)."""
        self.db.execute("UPDATE jobs SET status='pending', pid=NULL, attempts=MAX(attempts-1,0), updated=?"
                        " WHERE id=?", (time.time(), job_id))

    def retry_failed(self, stage: str | None = None) -> int:
        query = "UPDATE jobs SET status='pending', attempts=0, updated=? WHERE status='failed'"
        args: list[Any] = [time.time()]
        if stage:
            query += " AND stage=?"
            args.append(stage)
        return self.db.execute(query, args).rowcount

    def requeue_tag(self, tag: str) -> list[str]:
        """Reset every job depending on a review item (e.g. an overturned default)."""
        ids = [job.id for job in self.iter_jobs() if tag in job.tags and job.status != "running"]
        for job_id in ids:
            self.db.execute("UPDATE jobs SET status='pending', attempts=0, fingerprint='', updated=? WHERE id=?",
                            (time.time(), job_id))
        return ids

    # -- reading ---------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job(row) if row else None

    def iter_jobs(self, status: str | None = None) -> Iterator[Job]:
        if status:
            rows = self.db.execute("SELECT * FROM jobs WHERE status=? ORDER BY seq", (status,))
        else:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY seq")
        for row in rows.fetchall():
            yield _job(row)

    def counts(self) -> dict[str, int]:
        counts = {s: 0 for s in STATUSES}
        for row in self.db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
            counts[row[0]] = row[1]
        return counts


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], stage=row["stage"], key=row["key"], params=json.loads(row["params"]),
        fingerprint=row["fingerprint"], after=json.loads(row["after"]), tags=json.loads(row["tags"]),
        est_gb=row["est_gb"], status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
        pid=row["pid"], error=row["error"], result=json.loads(row["result"] or "{}"),
    )
