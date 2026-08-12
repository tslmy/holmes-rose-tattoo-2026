#!/usr/bin/env python3
"""Small HTTP helpers for talking to an Automatic1111/Forge Stable Diffusion
WebUI's `/sdapi/v1/...` REST API.

This project's neural *background redraw* pipeline
(`flux_redraw_rosetattoo_backgrounds.py`) no longer uses Automatic1111/Forge
at all - it runs FLUX.1 locally via `diffusers`. This module only exists
because `upscale_rosetattoo_sprites.py` still relies on Automatic1111's
`/sdapi/v1/extra-single-image` endpoint for real (non-diffusion) ESRGAN-
family super-resolution of small, silhouette-critical assets (cursors,
inventory items, walk-cycle frames, the map), where hallucinated new detail
from a generative redraw would break gameplay recognizability.
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from typing import Any

from PIL import Image


def image_to_base64(img: Image.Image) -> str:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def base64_to_image(data: str) -> Image.Image:
    if "," in data and data.split(",", 1)[0].startswith("data:"):
        data = data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_api(base_url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            get_json(f"{base_url}/sdapi/v1/options", timeout=5)
            return
        except (urllib.error.URLError, TimeoutError) as err:
            last_error = err
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {base_url}: {last_error}")


def upscale_via_api(
    api_url: str,
    api_timeout: int,
    source: Image.Image,
    scale: int,
    upscaler: str,
) -> Image.Image:
    """Upscale via Automatic1111's /sdapi/v1/extra-single-image endpoint.

    A real super-resolution model (ESRGAN/SwinIR/DAT/etc.) reconstructs
    plausible high-frequency detail and, critically, treats the source
    image's palette-dithering noise and low-res blocking as something to
    clean up rather than something to preserve - unlike a naive Lanczos
    resize, which just smoothly interpolates the existing pixels (including
    their dithering pattern) to a bigger canvas.
    """
    payload = {
        "image": image_to_base64(source),
        "upscaling_resize": scale,
        "upscaler_1": upscaler,
    }
    response = post_json(f"{api_url}/sdapi/v1/extra-single-image", payload, api_timeout)
    image = response.get("image")
    if not image:
        raise RuntimeError(f"Upscale API returned no image (upscaler={upscaler!r})")
    return base64_to_image(image)
