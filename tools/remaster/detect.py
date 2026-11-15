"""Open-vocabulary detection (OWLv2) and image embeddings (DINOv2) for the gates.

Both run locally through `transformers` on MPS when available (the `bakeoff`
extra). Weights: google/owlv2-base-patch16-ensemble (~0.6 GB) and
facebook/dinov2-small (~0.09 GB), in the Hugging Face cache.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image

OWL = "google/owlv2-base-patch16-ensemble"
DINO = "facebook/dinov2-small"


def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=1)
def _owl():
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    return Owlv2Processor.from_pretrained(OWL), Owlv2ForObjectDetection.from_pretrained(OWL).to(_device()).eval()


def _tiles(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Near-square overlapping tiles for wide images (OWLv2 pads to a square)."""
    if width <= height * 1.5:
        return [(0, 0, width, height)]
    tile = int(height * 4 / 3)
    count = int(np.ceil((width - tile) / (tile * 0.75))) + 1
    starts = np.linspace(0, width - tile, count).astype(int)
    return [(int(x), 0, tile, height) for x in starts]


def detect(image: Image.Image, queries: list[str], threshold: float = 0.05) -> list[dict]:
    """Boxes [x, y, w, h] in `image` pixels with score and query index; NMS per query."""
    import torch

    processor, model = _owl()
    image = image.convert("RGB")
    found: list[dict] = []
    for tx, ty, tw, th in _tiles(*image.size):
        crop = image.crop((tx, ty, tx + tw, ty + th))
        inputs = processor(text=[queries], images=crop, return_tensors="pt").to(_device())
        with torch.no_grad():
            outputs = model(**inputs)
        side = max(tw, th)
        logits = outputs.logits[0].sigmoid().cpu().numpy()  # (boxes, queries)
        boxes = outputs.pred_boxes[0].cpu().numpy()          # cx, cy, w, h in [0, 1] of the padded square
        for qi in range(len(queries)):
            keep = np.where(logits[:, qi] >= threshold)[0]
            for bi in keep:
                cx, cy, bw, bh = boxes[bi] * side
                x0, y0 = max(0.0, cx - bw / 2), max(0.0, cy - bh / 2)
                x1, y1 = min(float(tw), cx + bw / 2), min(float(th), cy + bh / 2)
                if x1 - x0 < 2 or y1 - y0 < 2:
                    continue
                found.append({"query": qi, "score": float(logits[bi, qi]),
                              "box": [x0 + tx, y0 + ty, x1 - x0, y1 - y0]})
    return _nms(found)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _nms(found: list[dict], iou: float = 0.5, per_query: int = 25) -> list[dict]:
    out = []
    for qi in sorted({f["query"] for f in found}):
        cands = sorted((f for f in found if f["query"] == qi), key=lambda f: -f["score"])
        kept: list[dict] = []
        for cand in cands:
            if all(_iou(cand["box"], k["box"]) < iou for k in kept):
                kept.append(cand)
            if len(kept) >= per_query:
                break
        out += kept
    return out


@lru_cache(maxsize=1)
def _dino():
    from transformers import AutoImageProcessor, AutoModel

    return AutoImageProcessor.from_pretrained(DINO), AutoModel.from_pretrained(DINO).to(_device()).eval()


def embed(images: list[Image.Image]) -> np.ndarray:
    """L2-normalised DINOv2 CLS embeddings, one row per image."""
    import torch

    processor, model = _dino()
    inputs = processor(images=[i.convert("RGB") for i in images], return_tensors="pt").to(_device())
    with torch.no_grad():
        cls = model(**inputs).last_hidden_state[:, 0].cpu().numpy()
    return cls / np.linalg.norm(cls, axis=1, keepdims=True)
