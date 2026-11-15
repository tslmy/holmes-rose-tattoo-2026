"""`uv run remaster ...` entry point.

  remaster extract prepare [--scenes 1 2] [--all]   plan, queue and run stages in order
  remaster <stage> --enqueue-only                    queue only; run later
  remaster run [--stage S] [--max-jobs N]            run every pending job (unattended)
  remaster status                                    queue counts and failures
  remaster review [decide ID CHOICE]                 render / answer the review queue
  remaster launchd                                   print a LaunchAgent plist for auto-resume
  remaster bakeoff-report                            Phase 1 summary, contact sheet, provisional picks
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import runner
from .config import load_config
from .review import ReviewQueue
from .stages import PIPELINE, STAGES, get

COMMANDS = {"run", "status", "review", "launchd", "_job", "retry", "bakeoff-report"}


def build_parser() -> argparse.ArgumentParser:
    stages = ", ".join(PIPELINE)
    parser = argparse.ArgumentParser(
        prog="remaster", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"stages: {stages}")
    parser.add_argument("--config", type=Path, default=None, help="remaster.toml (default: repo root)")
    parser.add_argument("targets", nargs="+", help="stage names, or one of: run, status, review, launchd")
    parser.add_argument("--scenes", type=int, nargs="+", help="scene numbers (default: the golden set)")
    parser.add_argument("--all", action="store_true", help="every RES*.RRM scene instead of the golden set")
    parser.add_argument("--resources", nargs="+", help="shared VGS resources (default: golden sprites)")
    parser.add_argument("--enqueue-only", action="store_true", help="queue jobs without running them")
    parser.add_argument("--stage", action="append", help="`run`: only jobs of this stage")
    parser.add_argument("--max-jobs", type=int, help="`run`: stop after this many jobs")
    parser.add_argument("--batch-size", type=int, default=10, help="jobs between disk-space checks")
    parser.add_argument("--no-caffeinate", action="store_true", help="do not wrap in caffeinate")
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, default=3, help=argparse.SUPPRESS)
    parser.add_argument("--seconds", type=float, default=5.0, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    command = args.targets[0]

    if command == "_job":
        return _run_one(cfg, args.targets[1], args.result)
    if command == "status":
        return _status(cfg)
    if command == "review":
        return _review(cfg, args.targets[1:])
    if command == "bakeoff-report":
        from . import bakeoff

        result = bakeoff.report(cfg, queue=not args.enqueue_only)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if command == "launchd":
        print(runner.launchd_plist(cfg))
        return 0
    if command == "retry":
        queue = runner.queue_for(cfg)
        print(f"reset {queue.retry_failed(args.targets[1] if len(args.targets) > 1 else None)} failed job(s)")
        return 0

    if not args.no_caffeinate and not args.enqueue_only:
        runner.ensure_caffeinated(argv)

    if command == "run":
        stages = set(args.stage) if args.stage else None
        return runner.run(cfg, stages=stages, batch_size=args.batch_size, max_jobs=args.max_jobs)

    bad = [t for t in args.targets if t in COMMANDS or t not in STAGES]
    if bad:
        raise SystemExit(f"cannot mix {bad} with stages; known stages: {', '.join(PIPELINE)}")
    for name in args.targets:
        stage = get(name)
        specs = stage.plan(cfg, args)
        if not specs:
            continue
        queue = runner.queue_for(cfg)
        max_attempts = int(cfg.threshold("job_max_attempts", 3))
        statuses = [queue.enqueue(spec, max_attempts) for spec in specs]
        queue.close()
        print(f"[{name}] {len(specs)} job(s): {statuses.count('done')} up to date, "
              f"{statuses.count('pending')} queued", flush=True)
        if args.enqueue_only:
            continue
        code = runner.run(cfg, only={s.id for s in specs}, batch_size=args.batch_size)
        if code != runner.EXIT_OK:
            print(f"[{name}] stopped with exit code {code}; `uv run remaster status` for details")
            return code
    return 0


def _run_one(cfg, job_id: str, result_path: Path | None) -> int:
    queue = runner.queue_for(cfg)
    job = queue.get(job_id)
    queue.close()
    if job is None:
        print(f"no such job {job_id}", file=sys.stderr)
        return 2
    try:
        result = get(job.stage).run_job(cfg, job.params, job.fingerprint)
    except Exception:
        traceback.print_exc()
        return 1
    if result_path:
        result_path.write_text(json.dumps(result or {}, default=str) + "\n", encoding="utf-8")
    return 0


def _status(cfg) -> int:
    queue = runner.queue_for(cfg)
    counts = queue.counts()
    print("jobs:", ", ".join(f"{k} {v}" for k, v in counts.items()))
    by_stage: dict[str, dict[str, int]] = {}
    for job in queue.iter_jobs():
        by_stage.setdefault(job.stage, {}).setdefault(job.status, 0)
        by_stage[job.stage][job.status] += 1
    for stage, c in by_stage.items():
        print(f"  {stage}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    for job in queue.iter_jobs("failed"):
        print(f"FAILED {job.id}: {job.error.splitlines()[0] if job.error else ''}")
    print(f"disk: {runner.disk_space(cfg.work).describe()}")
    pending = [i for i in ReviewQueue(cfg.review_dir, cfg.root).load() if i.status == "pending"]
    print(f"review queue: {len(pending)} pending (review/queue.md)")
    queue.close()
    return 0


def _review(cfg, rest: list[str]) -> int:
    review = ReviewQueue(cfg.review_dir, cfg.root)
    if rest[:1] == ["decide"]:
        if len(rest) != 3:
            raise SystemExit('usage: remaster review decide <number or id> "<choice>"')
        item, overturned = review.decide(rest[1], rest[2])
        print(f"{item.id}: {item.status}")
        if overturned:
            queue = runner.queue_for(cfg)
            ids = queue.requeue_tag(item.id)
            queue.close()
            print(f"re-queued {len(ids)} job(s) tagged {item.id}; run `uv run remaster run`")
        return 0
    review.render()
    from .review import numbered

    pending = [(n, i) for n, i in numbered(review.load()) if i.status == "pending"]
    print(f"{len(pending)} pending. Answer with: uv run remaster review decide <#> \"<choice>\"  (keep = accept the default)\n")
    for number, item in pending:
        print(f"#{number:<3} {item.id}  [{item.kind}]  {item.title}\n     default in use: {item.default}")
        if item.options:
            print("     options: " + "; ".join(item.options))
    print(f"\nwrote {cfg.review_dir / 'queue.md'} and {cfg.review_dir / 'sheet.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
