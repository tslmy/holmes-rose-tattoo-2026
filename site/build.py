# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "mdit-py-plugins>=0.4", "pillow>=10"]
# ///
"""Build the GitHub Pages site: the walk-through article plus every docs/*.md.

    uv run --script site/build.py            # writes site/_build/
    uv run --script site/build.py --no-images
    uv run --script site/build.py --local-root DIR   # checkout that holds the untracked samples

The article body is site/article.html (hand-written HTML, with <sn>...</sn>
for side notes, as in the brainux-brainai explainer). Every docs/*.md becomes
<name>.html with the same frame.

Images are game-derived, so they are never committed: IMAGES below lists each
one, built from the local samples (work/pack-archive/, work/prepare/,
generated/) into site/_build/img/ (gitignored). The built site is published to
the gh-pages branch only, never to main.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DOCS = ROOT / "docs"
OUT = SITE / "_build"
REPO = "https://github.com/tslmy/holmes-rose-tattoo-2026"
BLOB = f"{REPO}/blob/main/"

TITLE = "Remastering The Rose Tattoo: what we tried, and what worked"
DESCRIPTION = (
    "An illustrated walk-through of four months of AI remastering of a 1996 Sherlock Holmes "
    "adventure: the August diffusion experiments, the October super-resolution pilots, and the "
    "November pipeline that redrew 65 of 80 rooms."
)
DATE = "2026-11-22"

# Docs published as of the 2026-11-22 snapshot: the commit each one is read from.
# Docs not listed come from the working tree. None = untracked, read from --local-root.
DOC_PINS: dict[str, str | None] = {
    "bakeoff-results": "b33b182",
    "code-archive": "9d4a24e",
    "flux-setup": "9d4a24e",
    "linear-review-sync": None,
    "pack-archive": "9d4a24e",
    "phase3-sprites": "cd02682",
    "reproducing": "9d4a24e",
    "scene-start-smoke-test": "c671d2b",
    "sprite-remaster": "c671d2b",
}
LOCAL = ROOT  # untracked image sources and docs; set by --local-root

# Figures of the draft dev blog, which links to them outside docs/figures.
BLOG_FIGURES = "validation/rosetattoo-blog-figures-20261031"
BLOG_NAMES = ["01-candidate-holmes", "02-background-comparison", "03-palette-pilot", "04-soft-alpha-pilot"]

PA = "work/pack-archive"
IT = "generated__playable-overrides-2x-remaster"
# (output name, source, crop box in source pixels or None, max width, resample)
IMAGES: list[tuple[str, str, tuple[int, int, int, int] | None, int, str]] = [
    ("s04-1996.webp", "work/prepare/scene_004/plate.png", None, 960, "lanczos"),
    ("s04-sdxl-cinematic.webp", f"{PA}/generated__neural-redraws-cinematic-full/scene_004/background@2x.png", None, 960, "lanczos"),
    ("s04-sdxl-cuda.webp", f"{PA}/generated__sdxl-cuda-full-2x/sdxl-cuda-full-2x/scene_04/background_sdxl@2x.png", None, 960, "lanczos"),
    ("s04-flux1.webp", f"{PA}/generated__flux-geometry-locked/scene_04/background_flux@2x.png", None, 960, "lanczos"),
    ("s09-1996.webp", "work/prepare/scene_009/plate.png", None, 960, "lanczos"),
    ("s09-it1.webp", f"{PA}/{IT}-it1/scene_009/background@2x.png", None, 960, "lanczos"),
    ("s09-it9.webp", "generated/playable-overrides-2x-remaster-it9/scene_009/background@2x.png", None, 960, "lanczos"),
    ("s26-1996.webp", "work/prepare/scene_026/plate.png", None, 960, "lanczos"),
    ("s26-flux1.webp", f"{PA}/generated__flux-geometry-locked/scene_26/background_flux@2x.png", None, 960, "lanczos"),
    ("s26-it9.webp", "generated/playable-overrides-2x-remaster-it9/scene_026/background@2x.png", None, 960, "lanczos"),
    ("s57-it1-ingame.webp", f"{PA}/work__assemble__play/scene_057.png", (0, 1128, 1504, 2256), 960, "lanczos"),
    ("holmes-1996.webp", "extracted/sprites/svgawalk_vgs/frame_024.png", None, 186, "nearest2x"),
    ("holmes-esrgan.webp", f"{PA}/enhanced__sprites/svgawalk_vgs/frame_024@2x.png", None, 186, "lanczos"),
    ("holmes-lifelike.webp", f"{PA}/enhanced__sprites-lifelike/svgawalk_vgs/frame_024@2x.png", None, 186, "lanczos"),
    ("holmes-sheet.webp", f"{PA}/generated__character-redraws/references/svgawalk_vgs.png", None, 800, "lanczos"),
    *((f"blog-{n}.webp", f"{BLOG_FIGURES}/{n}.png", None, 960, "lanczos") for n in BLOG_NAMES),
]


def build_images() -> list[tuple[str, int, tuple[int, int]]]:
    from PIL import Image

    dest = OUT / "img"
    dest.mkdir(parents=True, exist_ok=True)
    report = []
    for name, src, crop, max_w, mode in IMAGES:
        # An already published image wins: some source packs have since been pruned.
        built = LOCAL / "site/_build/img" / name
        if LOCAL != ROOT and built.is_file():
            shutil.copyfile(built, dest / name)
            with Image.open(built) as im:
                report.append((name, built.stat().st_size, im.size))
            continue
        im = Image.open(LOCAL / src)
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        if crop:
            im = im.crop(crop)
        if mode == "nearest2x":
            im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
        elif im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        if im.mode == "RGBA" and im.getextrema()[3][0] == 255:
            im = im.convert("RGB")
        im.save(dest / name, "WEBP", quality=78, method=6)
        report.append((name, (dest / name).stat().st_size, im.size))
    return report


# ---------------------------------------------------------------- Markdown docs

LAN_IP = re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
LINEAR_LINK = re.compile(r'<a href="https://linear\.app/[^"]*">([^<]*)</a>')
LINEAR_BARE = re.compile(r"\(?https://linear\.app/\S+?\)?(?=[\s<:,.]|$)")


def doc_text(path: Path) -> str:
    if path.stem not in DOC_PINS:
        return path.read_text()
    commit = DOC_PINS[path.stem]
    if commit is None:
        return (LOCAL / "docs" / path.name).read_text()
    return subprocess.check_output(["git", "show", f"{commit}:docs/{path.name}"], cwd=ROOT, text=True)


def doc_pages() -> list[tuple[str, str, Path]]:
    pages = []
    stems = {p.stem for p in DOCS.glob("*.md")} | set(DOC_PINS)
    for stem in sorted(stems):
        p = DOCS / f"{stem}.md"
        first = next((l for l in doc_text(p).splitlines() if l.startswith("# ")), stem)
        pages.append((stem, first.lstrip("# ").strip(), p))
    return pages


def rewrite_links(body: str) -> str:
    def href(m: re.Match) -> str:
        attr, url = m.group(1), m.group(2)
        if re.match(r"^[a-z]+:|^#|^//", url):
            return m.group(0)
        path, _, frag = url.partition("#")
        frag = f"#{frag}" if frag else ""
        if "/" not in path and path.endswith(".md"):
            return f'{attr}="{path[:-3]}.html{frag}"'
        if path.startswith("figures/"):
            return m.group(0)
        target = (DOCS / path).resolve()
        try:
            rel = target.relative_to(ROOT)
        except ValueError:
            return m.group(0)
        return f'{attr}="{BLOB}{rel.as_posix()}{frag}"'

    body = re.sub(r'(href)="([^"]+)"', href, body)
    for n in BLOG_NAMES:
        body = body.replace(f'src="../{BLOG_FIGURES}/{n}.png"', f'src="img/blog-{n}.webp"')
    # Images outside docs/figures are game-derived local files: not published.
    body = re.sub(
        r'<img src="(?!figures/|img/blog-)[^"]*" alt="([^"]*)"[^>]*>',
        lambda m: f'<span class="img-missing">[image not published: {m.group(1)}]</span>',
        body,
    )
    body = LINEAR_LINK.sub(r"\1 (Linear, private)", body)
    body = LINEAR_BARE.sub("(Linear, private)", body)
    body = LAN_IP.sub("&lt;LAN host&gt;", body)
    return body


def render_md(md: MarkdownIt, path: Path) -> str:
    text = doc_text(path)
    text = re.sub(r"\A# .*\n", "", text)  # the title goes in <h1>
    # The blog's figures are published here, so its pre-publication notes are stale.
    text = re.sub(r"^> \*\*Draft image note:\*\*.*\n(?:>.*\n)*", "", text, flags=re.M)
    text = text.replace("*Draft — not yet published*", "*Draft*")
    return rewrite_links(md.render(text))


# ---------------------------------------------------------------- article

def render_article() -> tuple[str, int]:
    body = (SITE / "article.html").read_text()
    body = re.sub(r"^\s*<!--[\s\S]*?-->\s*", "", body)
    n = 0

    def sn(m: re.Match) -> str:
        nonlocal n
        n += 1
        return (
            f'<span class="sidenote-wrap"><label for="sn-{n}" class="sidenote-number"></label>'
            f'<input type="checkbox" id="sn-{n}" class="sidenote-toggle">'
            f'<span class="sidenote">{m.group(1).strip()}</span></span>'
        )

    body = re.sub(r"<sn>([\s\S]*?)</sn>", sn, body)
    return body, n


def page(template: str, *, title: str, description: str, body: str, nav: str, kind: str, extra: str = "") -> str:
    return (
        template.replace("{{TITLE}}", html.escape(title))
        .replace("{{DESCRIPTION}}", html.escape(description))
        .replace("{{DATE}}", DATE)
        .replace("{{KIND}}", kind)
        .replace("{{NAV}}", nav)
        .replace("{{EXTRA}}", extra)
        .replace("{{BODY}}", body)
    )


def nav_html(pages: list[tuple[str, str, Path]], current: str) -> str:
    items = ['<li><a href="index.html"%s>The walk-through</a></li>' % (' aria-current="page"' if current == "index" else "")]
    for stem, title, _ in pages:
        cur = ' aria-current="page"' if stem == current else ""
        items.append(f'<li><a href="{stem}.html"{cur}>{html.escape(title)}</a></li>')
    return "\n".join(items)


SCAN = [
    (re.compile(r"/Users/|/home/\w+"), "home path"),
    (re.compile(r"\.ssh/|\bssh\s+[\w.-]+@"), "ssh detail"),
    (re.compile(r"[\w.+-]+@(?:gmail|outlook|icloud|hotmail)\.com"), "email"),
    (re.compile(r"linear\.app/"), "Linear URL"),
    (re.compile(r"\b(?:sk|hf|ghp|gho|xox[bp])[-_][A-Za-z0-9]{16,}"), "key-like token"),
    (LAN_IP, "LAN address"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--local-root", type=Path, default=ROOT)
    args = ap.parse_args()
    global LOCAL
    LOCAL = args.local_root.resolve()

    if OUT.exists():
        for child in OUT.iterdir():
            if child.name == "img" and args.no_images:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    OUT.mkdir(exist_ok=True)
    shutil.copytree(SITE / "assets", OUT / "assets")
    if (DOCS / "figures").is_dir():
        shutil.copytree(DOCS / "figures", OUT / "figures")
    (OUT / ".nojekyll").write_text("")

    template = (SITE / "template.html").read_text()
    pages = doc_pages()
    md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table").use(
        anchors_plugin, min_level=2, max_level=3, permalinkSymbol="§"
    )

    body, notes = render_article()
    (OUT / "index.html").write_text(
        page(template, title=TITLE, description=DESCRIPTION, body=body, nav=nav_html(pages, "index"),
             kind="article", extra=f'<time datetime="{DATE}">{DATE}</time>')
    )
    for stem, title, path in pages:
        src = f'<p class="doc-source">Rendered from <a href="{BLOB}docs/{path.name}"><code>docs/{path.name}</code></a>, a working record of the project.</p>'
        (OUT / f"{stem}.html").write_text(
            page(template, title=title, description=f"{title}: a working document of the Rose Tattoo remaster.",
                 body=src + render_md(md, path), nav=nav_html(pages, stem), kind="doc")
        )

    if not args.no_images:
        total = 0
        for name, size, (w, h) in build_images():
            total += size
            print(f"  img/{name:28} {w:4}x{h:<4} {size / 1024:6.0f} KB")
        print(f"  images: {len(IMAGES)}, {total / 1024:.0f} KB in all")

    problems = []
    for f in OUT.rglob("*"):
        if f.suffix not in (".html", ".css", ".js", ".svg"):
            continue
        text = f.read_text()
        for rx, what in SCAN:
            for m in rx.finditer(text):
                problems.append(f"{f.relative_to(OUT)}: {what}: {m.group(0)}")
    used = {u for f in OUT.glob("*.html") for u in re.findall(r'src="img/([\w.-]+\.webp)', f.read_text())}
    missing = used - {n for n, *_ in IMAGES}
    print(f"wrote {OUT.relative_to(ROOT)}/: index.html ({notes} side notes) + {len(pages)} doc pages")
    if missing:
        problems.append(f"pages use unknown images: {sorted(missing)}")
    for p in problems:
        print("PRIVACY/CHECK:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
