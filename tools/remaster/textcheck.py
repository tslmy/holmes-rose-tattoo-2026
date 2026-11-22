"""Read and compare the writing in the original against the redraw.

The ESRGAN baseline's lettering is often garbled, so `geometry` no longer
pastes a writing region blindly: it reads the sign's words in both images
(vision model, upscaled crop) and only pastes when the redraw's text does not
match. `qa` independently asks a side-by-side review for the legible text of
both pictures and gates on the two staying in sync (text_gate).

Pure helpers here (normalise, text_matches, text_gate) need no LLM; read_text
does and takes the client as an argument so tests can pass a fake.
"""

from __future__ import annotations

import difflib
import json
import re
import tempfile
from pathlib import Path

from PIL import Image

SYSTEM = "You are an OCR specialist for old video game screenshots. Answer with JSON only."
PROMPT = """Transcribe exactly the words written in the picture (a sign, poster or printed text),
preserving their spelling and order. Answer JSON: {"text": "the words"}
Use "" when nothing is legible or there is no writing."""

_KEEP = re.compile(r"[A-Z0-9]")


def normalise(s: str) -> str:
    """Uppercase and keep only A-Z and 0-9, so layout and case do not matter."""
    return "".join(_KEEP.findall((s or "").upper()))


def text_matches(original: str, other: str, threshold: float = 0.8) -> bool | None:
    """True when the redraw's words are the same as the original's (fuzzy, case-insensitive).

    None when the original could not be read at all: unknown, so the caller
    falls back to pasting the known-good baseline.
    """
    a, b = normalise(original), normalise(other)
    if not a:
        return None
    if len(a) != len(b):  # different lengths cannot be the same sign
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    # A garbled sign (ESRGAN hallucinating letters) differs in several scattered
    # places, so even one stray character per word still looks "close" on a
    # plain ratio (e.g. NETBOPOLITAN POLICF vs METROPOLITAN POLICE scores 0.83).
    # Require both the overall ratio and one long unbroken run of agreement:
    # a true near-match keeps a single run covering almost the whole sign,
    # while a garbled one breaks into several short runs.
    if sm.ratio() < threshold:
        return False
    longest = max(block.size for block in sm.get_matching_blocks())
    return longest >= 0.9 * len(a)


def read_text(client, image: Image.Image, box: list[float], min_side: int = 512) -> str:
    """Crop `box` [x, y, w, h] from `image`, upscale it, and ask `client` to read the words.

    Returns the transcribed text, or "" when nothing is legible or the reply
    does not parse. Raises whatever `client.chat` raises (the caller decides
    whether a read failure means "paste the baseline").
    """
    W, H = image.size
    x, y, w, h = (float(v) for v in box)
    x0, y0 = max(0, int(x) - 4), max(0, int(y) - 4)
    x1, y1 = min(W, int(x + w) + 4), min(H, int(y + h) + 4)
    crop = image.crop((x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)))
    long_side = max(crop.size)
    if long_side < min_side:
        factor = min_side / long_side
        crop = crop.resize((int(crop.width * factor) + 1, int(crop.height * factor) + 1), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = Path(fh.name)
    crop.save(path)
    try:
        reply = client.chat(SYSTEM, PROMPT, images=[path], max_tokens=200)
        match = re.search(r"\{.*\}", reply.text, re.DOTALL)
        if not match:
            return ""
        data = json.loads(match.group(0))
        text = data.get("text")
        return text if isinstance(text, str) else ""
    except (ValueError, TypeError, KeyError):
        return ""


def text_gate(original_texts: list[str], remaster_texts: list[str]) -> tuple[bool, list[str]]:
    """Every substantial sign of the original must still be legible in the remaster.

    Originals whose normalised form has < 4 characters ("No", "24h") are too
    short to check reliably and are ignored. A match is a fuzzy equality or a
    substring of the remaster's text (a longer sign may contain it).
    Returns (ok, missing) where missing lists the unmatched originals as given.
    """
    remasters = [normalise(t) for t in remaster_texts]
    missing = []
    for original in original_texts:
        a = normalise(original)
        if len(a) < 4:
            continue
        if any(text_matches(original, b) is True or (b and a in b) for b in remasters):
            continue
        missing.append(original)
    return not missing, missing
