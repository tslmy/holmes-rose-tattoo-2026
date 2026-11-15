"""Review queue: decisions that wait for a human without blocking work.

State lives in review/queue.json (tracked, text only). Every item carries the
provisional default that the pipeline uses meanwhile. Jobs that depend on a
default are tagged with the item id (see jobs.py), so overturning a decision
re-queues exactly those jobs.

review/queue.md is the human-readable view (tracked); review/sheet.html is
an HTML sheet that may reference game-derived images under work/ and is
therefore ignored by git.
"""

from __future__ import annotations

import base64
import html
import io
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("decision", "download", "approval", "deletion", "disk", "info")


@dataclass
class ReviewItem:
    id: str
    kind: str
    title: str
    default: str
    detail: str = ""
    options: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | confirmed | overturned | done
    decision: str = ""
    size_gb: float | None = None
    images: list[str] = field(default_factory=list)  # paths relative to repo root
    created: str = ""
    updated: str = ""


CONFIRM_WORDS = {"keep", "yes", "y", "ok", "okay", "default", "confirm", "confirmed", "accept", "agree", "approve"}


def confirms_default(decision: str, default: str) -> bool:
    """True when an answer accepts the default: "keep", "yes", the default text itself,
    or a confirmation word followed by a comment ("keep, but ...")."""
    answer = " ".join(decision.split()).lower().rstrip(".!")
    first = re.split(r"[\s,;:.!\-]", answer, maxsplit=1)[0]
    return answer in CONFIRM_WORDS or first in CONFIRM_WORDS or answer == " ".join(default.split()).lower()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ReviewQueue:
    def __init__(self, review_dir: Path, repo_root: Path | None = None):
        self.dir = review_dir
        self.repo_root = repo_root or review_dir.parent
        self.path = review_dir / "queue.json"

    def load(self) -> list[ReviewItem]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [ReviewItem(**item) for item in data.get("items", [])]

    def _save(self, items: list[ReviewItem]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"items": [asdict(i) for i in items]}, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)
        self.render(items)

    def add(self, item: ReviewItem) -> ReviewItem:
        """Add or refresh an item. A human decision already recorded is kept."""
        if item.kind not in KINDS:
            raise ValueError(f"unknown review kind {item.kind!r}")
        items = self.load()
        for index, existing in enumerate(items):
            if existing.id == item.id:
                item.status, item.decision = existing.status, existing.decision
                item.created = existing.created
                item.updated = _now()
                items[index] = item
                break
        else:
            item.created = item.updated = _now()
            items.append(item)
        self._save(items)
        return item

    def resolve(self, ref: str) -> str:
        """Accept an item's number (as shown, with or without '#') or its id."""
        items = self.load()
        key = ref.lstrip("#")
        if key.isdigit() and 1 <= int(key) <= len(items):
            return items[int(key) - 1].id
        if any(i.id == ref for i in items):
            return ref
        raise KeyError(f"no review item {ref!r}; see `uv run remaster review`")

    def get(self, item_id: str) -> ReviewItem | None:
        return next((i for i in self.load() if i.id == item_id), None)

    def effective(self, item_id: str, fallback: str | None = None) -> str | None:
        """The value to use now: the human decision if any, else the default."""
        item = self.get(item_id)
        if item is None:
            return fallback
        return item.decision or item.default

    def decide(self, item_id: str, decision: str) -> tuple[ReviewItem, bool]:
        """Record a decision. Returns (item, overturned_default)."""
        item_id = self.resolve(item_id)
        items = self.load()
        for item in items:
            if item.id == item_id:
                overturned = not confirms_default(decision, item.default)
                item.decision = decision
                item.status = "overturned" if overturned else "confirmed"
                item.updated = _now()
                self._save(items)
                return item, overturned
        raise KeyError(item_id)

    def mark_done(self, item_id: str, note: str = "") -> None:
        items = self.load()
        for item in items:
            if item.id == item_id:
                item.status = "done"
                if note:
                    item.detail = (item.detail + "\n\n" + note).strip()
                item.updated = _now()
        self._save(items)

    # -- rendering -------------------------------------------------------
    def render(self, items: list[ReviewItem] | None = None) -> None:
        items = self.load() if items is None else items
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "queue.md").write_text(render_markdown(items), encoding="utf-8")
        (self.dir / "sheet.html").write_text(render_html(items, self.dir, self.repo_root), encoding="utf-8")


def numbered(items: list[ReviewItem]) -> list[tuple[int, ReviewItem]]:
    """Stable numbers: items are only ever appended, never removed."""
    return list(enumerate(items, start=1))


def render_markdown(items: list[ReviewItem]) -> str:
    pending = [(n, i) for n, i in numbered(items) if i.status == "pending"]
    closed = [(n, i) for n, i in numbered(items) if i.status != "pending"]
    lines = [
        "# Review queue",
        "",
        "Generated from `review/queue.json` by `uv run remaster review`. Answer an item by its number",
        "or its id, e.g. `uv run remaster review decide 5 keep` or `... decide golden-set keep`.",
        "`keep` (or yes/ok/default) accepts the default in use; any other answer overturns it",
        "(quote a choice with spaces). Do not edit this file by hand. Work continues on the",
        "**default in use** until you decide; overturning a default re-queues the jobs tagged with it.",
        "Thumbnails are in `review/sheet.html` (local only, not in git).",
        "",
        f"## Pending ({len(pending)})",
        "",
    ]
    if not pending:
        lines.append("Nothing waiting.\n")
    for number, item in pending:
        lines += _md_item(number, item)
    lines += [f"## Decided or done ({len(closed)})", ""]
    for number, item in closed:
        lines += _md_item(number, item)
    return "\n".join(lines).rstrip() + "\n"


def _md_item(number: int, item: ReviewItem) -> list[str]:
    size = f" ({item.size_gb:g} GB)" if item.size_gb else ""
    out = [f"### #{number} `{item.id}` · {item.kind}{size}: {item.title}", ""]
    out.append(f"- **Default in use:** {item.default}")
    if item.options:
        out.append("- **Options:** " + "; ".join(item.options))
    if item.status != "pending":
        out.append(f"- **Status:** {item.status}" + (f", decision: {item.decision}" if item.decision else ""))
    out.append(f"- Raised {item.created[:10]}" + (f", updated {item.updated[:10]}" if item.updated != item.created else ""))
    if item.detail:
        out += ["", item.detail]
    out.append("")
    return out


def thumbnail_data_uri(path: Path, max_side: int = 320) -> str | None:
    """Small embedded JPEG, so the sheet works when opened as a snapshot."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=72)
    except (OSError, ValueError):
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def render_html(items: list[ReviewItem], review_dir: Path, repo_root: Path) -> str:
    rows = []
    order = sorted(numbered(items), key=lambda pair: (pair[1].status != "pending", pair[0]))
    for number, item in order:
        figures = []
        for rel in item.images:
            uri = thumbnail_data_uri(repo_root / rel)
            caption = html.escape(rel)
            figures.append(f'<figure><img src="{uri}" alt="{caption}"><figcaption>{caption}</figcaption></figure>'
                           if uri else f'<figure class="missing"><figcaption>missing: {caption}</figcaption></figure>')
        imgs = "".join(figures)
        opts = "".join(f"<li>{html.escape(o)}</li>" for o in item.options)
        size = f" · {item.size_gb:g} GB" if item.size_gb else ""
        rows.append(
            f'<section class="{item.status}"><h2><span class="num">#{number}</span> <code>{html.escape(item.id)}</code> '
            f'<span class="kind">{item.kind}{size}</span> {html.escape(item.title)}</h2>'
            f'<p><b>Default in use:</b> {html.escape(item.default)}</p>'
            + (f"<ul>{opts}</ul>" if opts else "")
            + (f'<p class="status">{item.status}: {html.escape(item.decision)}</p>' if item.status != "pending" else
               f'<p class="cmd"><code>uv run remaster review decide {number} "&lt;choice&gt;"</code></p>')
            + (f"<pre>{html.escape(item.detail)}</pre>" if item.detail else "")
            + (f'<div class="imgs">{imgs}</div>' if imgs else "")
            + "</section>"
        )
    return f"""<!doctype html><meta charset="utf-8"><title>Remaster review</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#fbfaf7;--fg:#1d1b18;--muted:#6b665e;--card:#fff;--line:#e3dfd6;--accent:#8a3b12}}
@media (prefers-color-scheme: dark){{:root{{--bg:#171614;--fg:#ece8e1;--muted:#a59f94;--card:#211f1c;--line:#36332e;--accent:#e08a5a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:12px 0}}
section.pending{{border-left:4px solid var(--accent)}} section:not(.pending){{opacity:.7}}
h2{{font-size:16px;margin:0 0 6px}} .kind{{color:var(--muted);font-weight:400;font-size:13px}}
pre{{white-space:pre-wrap;font-size:13px;color:var(--muted)}}
.imgs{{display:flex;flex-wrap:wrap;gap:12px}} figure{{margin:0;max-width:100%}}
.imgs img{{display:block;max-height:240px;max-width:100%;border:1px solid var(--line)}}
figcaption{{font-size:12px;color:var(--muted);word-break:break-all}} .num{{color:var(--accent)}}
.cmd code{{font-size:13px;color:var(--muted)}}
</style>
<h1>Remaster review queue</h1>
<p>Answer with <code>uv run remaster review decide &lt;number or id&gt; "&lt;choice&gt;"</code>. Work proceeds on the default meanwhile. Thumbnails are embedded copies; full images are under <code>work/</code>.</p>
{''.join(rows)}
"""
