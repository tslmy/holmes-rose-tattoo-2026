"""`remaster bakeoff-report`: collect the Phase 1 results, pick provisional defaults.

Reads the redraw records, geometry sidecars, QA results and sprite-test
records for every golden scene x backend x level and writes, under
work/bakeoff/ (game-derived, not in git):
  summary.json   every row plus per-backend aggregates and the picks
  results.md     the same as Markdown tables (source for docs/bakeoff-results.md)
  contact.html   one contact sheet: original, ESRGAN baseline, ChatGPT reference where one
                 matches, then each backend x level (L2 with the warped hotspot / walk-zone
                 overlay), and the sprite strips; thumbnails in thumbs/

Picks (queued in review/queue.md for confirmation, used meanwhile):
  backend   most gate passes over all scenes and levels, then mean QA score, then speed
  settings  that backend's per-level settings
  levels    per room, the most ambitious level whose result passed every known gate with the
            picked backend; none passed -> "baseline" (the room keeps the ESRGAN baseline)
  sprites   the sprite-test backend that passed, else lowest flicker; none passed -> the plan's
            fallback (characters stay on de-dithered SR)
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

from PIL import Image

from .config import Config
from .jobs import Job, JobQueue
from .review import ReviewItem, ReviewQueue
from .stages import baseline, brief, geometry, prepare, qa, redraw_bg, sprite_test

LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2}


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _jobs(cfg: Config) -> dict[str, Job]:
    path = cfg.state_dir / "jobs.sqlite"
    if not path.exists():
        return {}
    queue = JobQueue(path)
    try:
        return {job.id: job for job in queue.iter_jobs()}
    finally:
        queue.close()


def _failure_reason(job: Job) -> str:
    """The last error line of a failed job's log, with an HTTP error's message pulled out."""
    match = re.search(r"log (\S+)$", job.error or "")
    lines = []
    if match and Path(match.group(1)).is_file():
        lines = [ln for ln in Path(match.group(1)).read_text(errors="replace").splitlines()
                 if re.match(r"\w*(Error|Exception|Unavailable)\b", ln)]
    text = lines[-1] if lines else (job.error or "failed")
    msg = re.search(r'"msg":"([^"]+)"', text)
    return (msg.group(1) if msg else text)[:200]


def change_from_original(cfg: Config, scene: int, final: Path) -> float | None:
    """Mean absolute difference (0-255) between the shipped picture and the original plate, both
    at the original size. A redraw that barely changes the picture is not a remaster, however
    well it scores: several "photoreal" ratings came back as high for the original renders."""
    plate = prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate.png"
    if not (plate.is_file() and final.is_file()):
        return None
    import numpy as np
    with Image.open(plate) as a, Image.open(final) as b:
        orig = np.asarray(a.convert("RGB"), dtype=np.float32)
        new = np.asarray(b.convert("RGB").resize(a.size, Image.Resampling.LANCZOS), dtype=np.float32)
    return round(float(np.abs(orig - new).mean()), 1)


def collect(cfg: Config) -> list[dict]:
    """One row per scene x backend x level. The job queue is authoritative: a job that failed in
    the latest run reports as failed, even if an older run left a record on disk."""
    rows = []
    jobs = _jobs(cfg)
    for scene in cfg.golden_scenes:
        for backend in redraw_bg.backends(cfg):
            for level in redraw_bg.levels(cfg):
                red = _read(redraw_bg.job_dir(cfg, scene, backend, level) / "record.json")
                geo = _read(geometry.job_dir(cfg, scene, backend, level) / "geometry.json").get("summary", {})
                q = _read(qa.job_dir(cfg, scene, backend, level) / "qa.json").get("summary", {})
                status = red.get("status") or "not run"
                reason = red.get("reason")
                job = jobs.get(f"{redraw_bg.NAME}:{redraw_bg.key(scene, backend, level)}")
                if job and job.status == "failed":
                    status, reason, geo, q = "failed", _failure_reason(job), {}, {}
                elif job and job.status in ("pending", "running"):
                    status, reason, geo, q = "not run", f"job {job.status}", {}, {}
                if status == "ok" and not geo:
                    status = "geometry not run"
                elif status == "ok" and not q:
                    status = "qa not run"
                change, changed = None, None
                if status == "ok":
                    change = change_from_original(cfg, scene, geometry.job_dir(cfg, scene, backend, level) / "final.png")
                    changed = change is not None and change >= cfg.threshold("bakeoff_min_change", 20.0)
                    q = q | {"gates": q.get("gates", {}) | {"changed": changed}}
                rows.append({"scene": scene, "backend": backend, "level": level, "status": status,
                             "reason": reason, "seconds": red.get("seconds"),
                             "peak_memory_gb": red.get("peak_memory_gb"), "where": red.get("where"),
                             "geometry": geo, "qa": q, "change": change,
                             "pass": bool(q.get("pass")) and bool(changed), "score": q.get("score")})
    return rows


def aggregate(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        agg = out.setdefault(row["backend"], {"images": 0, "unavailable": 0, "failed": 0, "passes": 0,
                                              "seconds": [], "peaks": [], "scores": [], "reasons": set(),
                                              "pass_by_level": {}})
        if row["status"] == "unavailable":
            agg["unavailable"] += 1
            agg["reasons"].add(row["reason"])
            continue
        if row["status"] != "ok":
            agg["failed"] += 1
            if row["status"] == "failed":
                agg["reasons"].add(f'{row["level"]} failed: {row["reason"]}')
            continue
        agg["images"] += 1
        agg["passes"] += row["pass"]
        lvl = agg["pass_by_level"].setdefault(row["level"], [0, 0])
        lvl[0] += row["pass"]
        lvl[1] += 1
        for key, value in (("seconds", row["seconds"]), ("peaks", row["peak_memory_gb"]), ("scores", row["score"])):
            if isinstance(value, (int, float)):
                agg[key].append(value)
    for agg in out.values():
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        agg["mean_seconds"] = mean(agg.pop("seconds"))
        peaks = agg.pop("peaks")
        agg["peak_memory_gb"] = max(peaks) if peaks else None
        agg["mean_score"] = mean(agg.pop("scores"))
        agg["reasons"] = sorted(r for r in agg["reasons"] if r)
    return out


def pick(cfg: Config, rows: list[dict], aggs: dict[str, dict]) -> dict:
    ran = {b: a for b, a in aggs.items() if a["images"]}
    if not ran:
        return {"backend": None, "levels": {}, "reason": "no backend produced an image"}
    backend = max(ran, key=lambda b: (ran[b]["passes"], ran[b]["mean_score"] or 0, -(ran[b]["mean_seconds"] or 1e9)))
    levels = {}
    for scene in cfg.golden_scenes:
        mine = [r for r in rows if r["scene"] == scene and r["backend"] == backend and r["status"] == "ok"]
        passed = [r for r in mine if r["pass"]]
        if passed:
            best = max(passed, key=lambda r: (LEVEL_RANK[r["level"]], r["score"] or 0))
            levels[scene] = {"level": best["level"], "score": best["score"], "why": "most ambitious level that passed"}
        else:
            best = max(mine, key=lambda r: (r["score"] or 0), default=None)
            levels[scene] = {"level": "baseline", "best_failed": best["level"] if best else None,
                             "score": best["score"] if best else None,
                             "why": "no level passed every gate; the room keeps the ESRGAN baseline"}
    try:
        settings = redraw_bg.imagegen.backend(cfg, backend)
    except KeyError:
        settings = {}
    return {"backend": backend, "settings": {k: v for k, v in settings.items() if k not in ("api_key_env",)},
            "levels": levels, "passes": ran[backend]["passes"], "images": ran[backend]["images"],
            "mean_score": ran[backend]["mean_score"]}


def sprite_rows(cfg: Config) -> list[dict]:
    rows = []
    test = sprite_test.settings(cfg)
    for character in test.get("characters", {}):
        for backend in test.get("backends", []):
            rec = _read(sprite_test.job_dir(cfg, character, backend) / "record.json").get("summary", {})
            row = {"character": character, "backend": backend, **({"status": "not run"} | rec)}
            # Far less frame-to-frame change than the original walk means the model ignored the
            # pose and drew one still figure; the pose-IoU gate alone lets that through.
            low = cfg.threshold("sprite_min_flicker_ratio", 0.6)
            if row.get("status") == "ok" and (row.get("flicker_ratio") or 0) < low:
                row["pass"] = False
                row["note"] = f"static: flicker ratio {row.get('flicker_ratio')} < {low} (pose ignored)"
            rows.append(row)
    return rows


def pick_sprites(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    by_backend: dict[str, list[dict]] = {}
    for r in ok:
        by_backend.setdefault(r["backend"], []).append(r)
    complete = {b: rs for b, rs in by_backend.items() if len(rs) == len({r["character"] for r in rows})}
    if not complete:
        return {"route": "sr-baseline", "why": "no backend finished both characters"}
    passing = [b for b, rs in complete.items() if all(r.get("pass") for r in rs)]
    flick = lambda b: max(r.get("flicker_ratio") or 99 for r in complete[b])  # noqa: E731
    if passing:
        best = min(passing, key=flick)
        return {"route": "identity-conditioned", "backend": best, "why": "passed flicker, drift and pose gates"}
    best = min(complete, key=flick)
    return {"route": "sr-baseline", "closest_backend": best, "closest_flicker": flick(best),
            "why": "no backend passed the flicker/drift/pose gates; characters stay on de-dithered SR "
                   "(plan risk fallback) until Phase 3's research"}


# -- contact sheet -----------------------------------------------------------
def thumb(cfg: Config, src: Path, name: str, width: int = 420) -> str | None:
    if not src.exists():
        return None
    out = cfg.work / "bakeoff" / "thumbs" / (name + ".jpg")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as image:
            image = image.convert("RGB")
            image.thumbnail((width, width))
            image.save(out, quality=82)
    return os.path.relpath(out, cfg.work / "bakeoff")


def _fig(cfg: Config, src: Path, name: str, caption: str, cls: str = "") -> str:
    rel = thumb(cfg, src, name)
    if rel is None:
        return f'<figure class="missing {cls}"><div class="ph">{html.escape(caption)}</div></figure>'
    full = os.path.relpath(src, cfg.work / "bakeoff")
    return (f'<figure class="{cls}"><a href="{html.escape(full)}"><img loading="lazy" src="{html.escape(rel)}" '
            f'alt=""></a><figcaption>{caption}</figcaption></figure>')


def _badges(row: dict) -> str:
    if row["status"] != "ok":
        why = f': {row["reason"]}' if row.get("reason") else ""
        return f'<span class="bad">{html.escape(row["status"] + why)}</span>'
    q = row["qa"]
    gates = q.get("gates", {})
    parts = []
    for name, value in gates.items():
        cls = "ok" if value else ("unk" if value is None else "bad")
        parts.append(f'<span class="{cls}">{name}</span>')
    mem = f' · {row["peak_memory_gb"]} GB' if row.get("peak_memory_gb") else ""
    head = (f'<b class="{"ok" if row["pass"] else "bad"}">{"PASS" if row["pass"] else "fail"}</b> '
            f'score {row["score"]} · {row["seconds"]} s{mem}')
    return head + "<br>" + " ".join(parts)


def contact_sheet(cfg: Config, rows: list[dict], aggs: dict, picks: dict, sprites: list[dict],
                  sprite_pick: dict) -> Path:
    refs = cfg.section("bakeoff").get("references", {})
    ref_dir = Path(os.path.expanduser(cfg.section("bakeoff").get("reference_dir", "")))
    sections = []
    for scene in cfg.golden_scenes:
        role = cfg.golden.get("scene_roles", {}).get(str(scene), "")
        head = [_fig(cfg, prepare.out_root(cfg) / f"scene_{scene:03d}" / "plate.png", f"s{scene}-orig", "original plate"),
                _fig(cfg, baseline.out_root(cfg) / f"scene_{scene:03d}" / "plate_esrgan.png", f"s{scene}-base",
                     "de-dithered ESRGAN baseline")]
        if str(scene) in refs:
            head.append(_fig(cfg, ref_dir / refs[str(scene)], f"s{scene}-ref", "ChatGPT reference (loose match)"))
        level_pick = picks.get("levels", {}).get(scene, {})
        grid = []
        for backend in redraw_bg.backends(cfg):
            cells = [f'<th>{html.escape(backend)}</th>']
            for level in redraw_bg.levels(cfg):
                row = next(r for r in rows if r["scene"] == scene and r["backend"] == backend and r["level"] == level)
                gdir = geometry.job_dir(cfg, scene, backend, level)
                tag = f"s{scene}-{backend}-{level}"
                if row["status"] in ("unavailable", "failed", "not run"):
                    cells.append(f'<td class="na">{row["status"]}: {html.escape(row["reason"] or "")}</td>')
                    continue
                figs = _fig(cfg, gdir / "final.png", tag, _badges(row))
                if level == "L2":
                    figs += _fig(cfg, gdir / "overlay.png", tag + "-ov", "warped hotspots (yellow) and walk zones (cyan)",
                                 "overlay")
                chosen = picks.get("backend") == backend and level_pick.get("level") == level
                cells.append(f'<td class="{"chosen" if chosen else ""}">{figs}</td>')
            grid.append("<tr>" + "".join(cells) + "</tr>")
        levels_head = "".join(f"<th>{l}</th>" for l in redraw_bg.levels(cfg))
        sections.append(
            f'<section><h2>Scene {scene} <small>{html.escape(role)}</small></h2>'
            f'<p>Provisional level: <b>{html.escape(str(level_pick.get("level")))}</b> — {html.escape(level_pick.get("why", ""))}</p>'
            f'<div class="row">{"".join(head)}</div>'
            f'<div class="scroll"><table><tr><th></th>{levels_head}</tr>{"".join(grid)}</table></div></section>')
    sprite_html = []
    for r in sprites:
        d = sprite_test.job_dir(cfg, r["character"], r["backend"])
        cap = (f'{html.escape(r["character"])} · {html.escape(r["backend"])} · '
               + (f'{"PASS" if r.get("pass") else "fail"} · flicker x{r.get("flicker_ratio")} · drift +{r.get("drift_excess")}'
                  f' · pose IoU {r.get("pose_iou_mean")} · {r.get("seconds_per_image")} s/img'
                  if r.get("status") == "ok" else html.escape(f'{r.get("status")}: {r.get("reason") or ""}')))
        sprite_html.append('<div class="row">' + _fig(cfg, d / "cast.png", f'{r["character"]}-{r["backend"]}-cast', "cast sheet")
                           + _fig(cfg, d / "strip.jpg", f'{r["character"]}-{r["backend"]}-strip',
                                  cap + "<br>top: ESRGAN baseline, bottom: redraw", "wide") + "</div>")
    agg_rows = "".join(
        f'<tr><td>{html.escape(b)}</td><td>{a["images"]}</td><td>{a["passes"]}</td>'
        f'<td>{a["mean_score"]}</td><td>{a["mean_seconds"]}</td><td>{a["peak_memory_gb"]}</td>'
        f'<td>{html.escape("; ".join(a["reasons"]))}</td></tr>' for b, a in aggs.items())
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Bake-off contact sheet</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#f7f5f0;--fg:#1d1b18;--muted:#6b665e;--card:#fff;--line:#e2ddd2;--ok:#1f7a3a;--bad:#a8321e;--unk:#8a6d1a;--pick:#d07a2a}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161513;--fg:#ebe7df;--muted:#a39d92;--card:#1f1d1a;--line:#36322c;--ok:#5fc27e;--bad:#ef7b62;--unk:#d8b24a;--pick:#f0a050}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;padding:16px}}
h1{{font-size:22px}} h2{{font-size:17px;margin:0 0 4px}} h2 small{{color:var(--muted);font-weight:400}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:16px 0}}
.row{{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}} figure{{margin:0;max-width:420px}}
figure img{{display:block;width:100%;height:auto;border:1px solid var(--line)}} figure.wide{{max-width:100%}}
figcaption{{font-size:12px;color:var(--muted)}} .scroll{{overflow-x:auto}} table{{border-collapse:collapse}}
td,th{{vertical-align:top;padding:4px;border-top:1px solid var(--line);text-align:left}} td figure{{width:300px}}
td.na{{color:var(--muted);font-size:12px;max-width:220px}} td.chosen{{outline:3px solid var(--pick);outline-offset:-3px}}
.ok{{color:var(--ok)}} .bad{{color:var(--bad)}} .unk{{color:var(--unk)}} figcaption span{{margin-right:4px}}
.ph{{width:300px;height:80px;display:grid;place-items:center;border:1px dashed var(--line);color:var(--muted)}}
</style>
<h1>Phase 1 bake-off</h1>
<p>Generated {time.strftime('%Y-%m-%d %H:%M')}. Provisional backend: <b>{html.escape(str(picks.get('backend')))}</b>
(outlined cells are the per-room picks). Sprites: <b>{html.escape(sprite_pick.get('route', ''))}</b> — {html.escape(sprite_pick.get('why', ''))}.
Click a thumbnail for the full image. Local only: game-derived images.</p>
<section><h2>Backends</h2><div class="scroll"><table><tr><th>backend</th><th>images</th><th>passed</th><th>mean score</th>
<th>mean s/image</th><th>peak GB</th><th>not run because</th></tr>{agg_rows}</table></div></section>
{''.join(sections)}
<section><h2>Sprite test: Holmes and Watson</h2>{''.join(sprite_html)}</section>
</html>"""
    path = cfg.work / "bakeoff" / "contact.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def markdown(cfg: Config, rows: list[dict], aggs: dict, picks: dict, sprites: list[dict], sprite_pick: dict) -> str:
    out = ["| backend | images | passed | failed / not run | mean score | mean s/image | peak GB | not run because |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for b, a in aggs.items():
        out.append(f'| `{b}` | {a["images"]} | {a["passes"]} | {a["failed"] + a["unavailable"]} | '
                   f'{a["mean_score"]} | {a["mean_seconds"]} | {a["peak_memory_gb"]} | {"; ".join(a["reasons"])} |')
    out += ["", "| scene | backend | level | pass | score | change | s | GB | warp p95 px | stretch | found | gates failed |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for r in rows:
        if r["status"] != "ok":
            out.append(f'| {r["scene"]} | `{r["backend"]}` | {r["level"]} | {r["status"]} | | | | | | | | '
                       f'{r["reason"] or ""} |')
            continue
        q, g = r["qa"], r["geometry"]
        failed = [k for k, v in q.get("gates", {}).items() if v is False]
        out.append(f'| {r["scene"]} | `{r["backend"]}` | {r["level"]} | {"yes" if r["pass"] else "no"} | '
                   f'{r["score"]} | {r["change"]} | {r["seconds"]} | {r["peak_memory_gb"] or "-"} | {g.get("disp_p95")} | '
                   f'{g.get("max_stretch")} | {q.get("found_ratio")} | {", ".join(failed)} |')
    out += ["", "| character | backend | status | pass | flicker x | drift excess | pose IoU | s/image | GB |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in sprites:
        out.append(f'| {r["character"]} | `{r["backend"]}` | {r.get("status")} | {r.get("pass")} | '
                   f'{r.get("flicker_ratio")} | {r.get("drift_excess")} | {r.get("pose_iou_mean")} | '
                   f'{r.get("seconds_per_image")} | {r.get("peak_memory_gb")} |')
    return "\n".join(out) + "\n"


def queue_picks(cfg: Config, picks: dict, aggs: dict, sprite_pick: dict) -> list[str]:
    review = ReviewQueue(cfg.review_dir, cfg.root)
    contact = "work/bakeoff/contact.html (local)"
    alternatives = [f"{b} ({a['passes']}/{a['images']} passed, score {a['mean_score']})"
                    for b, a in aggs.items() if a["images"] and b != picks.get("backend")]
    ids = []
    if picks.get("backend"):
        review.add(ReviewItem(
            id="bakeoff-backend", kind="decision",
            title="Phase 1 pick: image backend for backgrounds (by gate scores)",
            default=f"{picks['backend']} ({picks['passes']}/{picks['images']} results passed, mean score "
                    f"{picks['mean_score']})",
            options=alternatives, detail=f"Results: docs/bakeoff-results.md; images: {contact}."))
        review.add(ReviewItem(
            id="bakeoff-settings", kind="decision",
            title="Phase 1 pick: settings for that backend",
            default=json.dumps({k: v for k, v in picks["settings"].items() if k not in ("url",)}, sort_keys=True)
                    + "; seed 7; ~1 MP generation; level instruction appended to the brief",
            options=["other strengths/levels from docs/bakeoff-results.md"],
            detail="The 4x tiled detail pass is not part of the bake-off (Phase 2)."))
        lv = "; ".join(f"{s}: {v['level']}" for s, v in picks["levels"].items())
        review.add(ReviewItem(
            id="bakeoff-room-levels", kind="decision",
            title="Phase 1 pick: freedom level per golden room",
            default=lv, options=["set a room to L0 / L1 / L2 / baseline"],
            detail="Most ambitious level whose result passed every known gate with the picked backend; "
                   "'baseline' = no level passed, the room keeps the ESRGAN baseline. Reasons per room in "
                   f"docs/bakeoff-results.md; overlays for L2 in {contact}."))
        ids += ["bakeoff-backend", "bakeoff-settings", "bakeoff-room-levels"]
    review.add(ReviewItem(
        id="bakeoff-sprite-route", kind="decision",
        title="Phase 1 pick: route for character sprites",
        default=f"{sprite_pick.get('route')}" + (f" with {sprite_pick['backend']}" if sprite_pick.get("backend") else ""),
        options=["identity-conditioned redraw with a named backend", "sr-baseline until Phase 3"],
        detail=sprite_pick.get("why", "")))
    ids.append("bakeoff-sprite-route")
    return ids


def report(cfg: Config, queue: bool = True) -> dict:
    rows = collect(cfg)
    aggs = aggregate(rows)
    picks = pick(cfg, rows, aggs)
    sprites = sprite_rows(cfg)
    sprite_pick = pick_sprites(sprites)
    out = cfg.work / "bakeoff"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({"rows": rows, "backends": aggs, "picks": picks,
                                                  "sprites": sprites, "sprite_pick": sprite_pick},
                                                 indent=2, default=str) + "\n", encoding="utf-8")
    (out / "results.md").write_text(markdown(cfg, rows, aggs, picks, sprites, sprite_pick), encoding="utf-8")
    sheet = contact_sheet(cfg, rows, aggs, picks, sprites, sprite_pick)
    queued = queue_picks(cfg, picks, aggs, sprite_pick) if queue else []
    return {"contact": str(sheet), "picks": picks, "sprite_pick": sprite_pick, "queued": queued}


__all__ = ["report", "collect", "aggregate", "pick", "pick_sprites", "brief"]
