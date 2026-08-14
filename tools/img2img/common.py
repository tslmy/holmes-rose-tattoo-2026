"""Shared scene loading, masks, and hard geometry restoration."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[2]
STYLE_PROMPT = (
    "Photorealistic Victorian-era period-film set, cinematic practical lighting, rich "
    "wood, brass, stone, fabric, and believable materials. Preserve the exact camera "
    "angle, perspective, architecture, doorway and window boundaries, furniture "
    "silhouettes, and object placement. Do not add people or readable text."
)
EXCLUDED_ACTION_TYPES = {"nowalk_zone", "blank_zone", "script_zone"}


def scene_dirs(root: Path, selected: list[int] | None) -> list[Path]:
    paths = sorted(path for path in root.glob("scene_*") if path.is_dir())
    return paths if selected is None else [path for path in paths if int(path.name.split("_")[1]) in set(selected)]


def scene_id(scene_dir: Path) -> int:
    return int(scene_dir.name.split("_")[1])


def prompt_for(scene_dir: Path) -> str:
    number = scene_id(scene_dir)
    brief = ROOT / "profiles" / "neural" / "prompt-briefs" / f"scene_{number:03d}" / "visual_brief.txt"
    detail = brief.read_text(encoding="utf-8").strip() if brief.exists() else (scene_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    return f"{STYLE_PROMPT}\n\nScene details:\n{detail}".strip()


def scaled_source(scene_dir: Path, scale: float, blur_radius: float = 1.0) -> tuple[Image.Image, Image.Image]:
    source = Image.open(scene_dir / "background.png").convert("RGB")
    if blur_radius:
        init = source.filter(ImageFilter.GaussianBlur(blur_radius))
    else:
        init = source
    size = (max(16, round(source.width * scale / 16) * 16), max(16, round(source.height * scale / 16) * 16))
    return source, init.resize(size, Image.LANCZOS)


def freedom_mask(scene_dir: Path, size: tuple[int, int], feather: float = 6.0) -> Image.Image:
    protect_path = scene_dir / "protect_mask.png"
    if not protect_path.exists():
        return Image.new("L", size, 255)
    protected = Image.open(protect_path).convert("L").resize(size, Image.NEAREST)
    editable = ImageOps.invert(protected)
    return editable.filter(ImageFilter.GaussianBlur(feather)) if feather else editable


def boundary_freedom_mask(scene_dir: Path, size: tuple[int, int], radius: int = 3, feather: float = 2.0) -> Image.Image:
    """Allow redraw except for a thin band around resource-derived regions."""
    protect_path = scene_dir / "protect_mask.png"
    if not protect_path.exists():
        return Image.new("L", size, 255)
    protected = Image.open(protect_path).convert("L")
    boundary = _boundary(protected, radius)
    locked = boundary.resize(size, Image.NEAREST)
    editable = ImageOps.invert(locked)
    return editable.filter(ImageFilter.GaussianBlur(feather)) if feather else editable


def structural_freedom_mask(
    scene_dir: Path,
    source: Image.Image,
    size: tuple[int, int],
    edge_threshold: int = 42,
    edge_radius: int = 9,
) -> Image.Image:
    """Allow material repainting while retaining canonical room geometry.

    Game resources identify collision and hotspot rectangles, but a background
    plate also contains non-interactable geometry such as doorway frames and
    window mullions. Blur before detecting edges to discard palette dithering,
    then keep a thick source-derived contour band alongside resource regions.
    """
    blurred = source.convert("L").filter(ImageFilter.GaussianBlur(2.0))
    edges = blurred.filter(ImageFilter.FIND_EDGES).point(lambda value: 255 if value >= edge_threshold else 0)
    edge_band = edges.filter(ImageFilter.MaxFilter(edge_radius * 2 + 1)).resize(size, Image.NEAREST)
    protected = ImageOps.invert(freedom_mask(scene_dir, size, feather=0.0))
    locked = ImageChops.lighter(edge_band, protected)
    return ImageOps.invert(locked)


def canny_like(image: Image.Image, low: int = 40) -> Image.Image:
    """Dependency-free structural control image for ControlNet."""
    edges = image.convert("L").filter(ImageFilter.GaussianBlur(1.0)).filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda value: 255 if value >= low else 0)
    return Image.merge("RGB", (edges, edges, edges))


def _boundary(mask: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return mask
    size = radius * 2 + 1
    return ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(size)), mask.filter(ImageFilter.MinFilter(size)))


def geometry_lock(scene_dir: Path, original: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Lock only semantic game-resource boundaries, never texture-derived edges.

    A Canny-style edge map from the 256-colour original captures its dithering
    and brick texture. Compositing those pixels back after generation makes a
    photoreal redraw look contaminated, while providing no extra gameplay
    guarantee beyond the extracted resource geometry.
    """
    lock = Image.new("L", original.size, 0)
    protect_path = scene_dir / "protect_mask.png"
    if protect_path.exists():
        lock = ImageChops.lighter(lock, _boundary(Image.open(protect_path).convert("L"), 3))
    metadata_path = scene_dir / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        objects = Image.new("L", original.size, 0)
        draw = ImageDraw.Draw(objects)
        for obj in metadata.get("objects", []):
            bounds = obj.get("bounds")
            if not bounds or obj.get("action_type") in EXCLUDED_ACTION_TYPES:
                continue
            x, y, width, height = (bounds.get(key, 0) for key in ("x", "y", "w", "h"))
            if 0 <= x < original.width and 0 <= y < original.height and 1 <= width <= original.width and 1 <= height <= original.height:
                draw.rectangle((x, y, x + width - 1, y + height - 1), fill=255)
        lock = ImageChops.lighter(lock, _boundary(objects, 8))
    return lock.resize(target_size, Image.NEAREST)


def restore_geometry(redraw: Image.Image, scene_dir: Path, original: Image.Image) -> Image.Image:
    source = original.resize(redraw.size, Image.LANCZOS)
    return Image.composite(source, redraw.convert("RGB"), geometry_lock(scene_dir, original, redraw.size))


def save_scene(output_dir: Path, backend: str, scene_dir: Path, image: Image.Image, settings: dict, scale: float = 2.0) -> Path:
    target = output_dir / scene_dir.name
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"background_{backend}@{scale:g}x.png"
    image.save(path)
    (target / "generation.json").write_text(json.dumps(settings, indent=2, default=str) + "\n", encoding="utf-8")
    return path
