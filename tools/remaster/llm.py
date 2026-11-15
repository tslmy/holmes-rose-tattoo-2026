"""Minimal LLM chat client: OpenAI-compatible endpoints, plus native Ollama.

Standard library only. `provider="openai"` talks to any /v1/chat/completions
server (LM Studio at http://localhost:1234/v1, Ollama at
http://127.0.0.1:11434/v1, llama.cpp, vLLM). `provider="ollama"` uses
Ollama's native /api/chat, which can switch thinking off reliably
(`think: false`) and set the context size per request.

Images are sent as data URLs (OpenAI) or base64 strings (Ollama). Thinking
text is stripped from replies whichever way the server returns it.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    stripped = THINK_RE.sub("", text or "")
    if "</think>" in stripped:  # opening tag eaten by the template
        stripped = stripped.split("</think>", 1)[1]
    return stripped.strip()


def post_json(url: str, payload: dict[str, Any], timeout: float, api_key: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code} from {url}: {body[:800]}") from err


def get_json(url: str, timeout: float = 10, api_key: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


@dataclass
class ChatResult:
    text: str
    seconds: float
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatClient:
    base_url: str
    model: str
    provider: str = "openai"  # "openai" | "ollama"
    api_key: str | None = None
    timeout: float = 600
    context_tokens: int = 8192
    disable_thinking: bool = True

    @classmethod
    def from_config(cls, llm: dict[str, Any], role: str = "text", *, probe: bool = True) -> "ChatClient":
        """Build from the [llm] table of remaster.toml; role is "text" or "vision".

        If the primary server is unreachable or does not list the model (the
        remote laptop is asleep, say), fall back to `fallback_base_url` with
        `fallback_<role>_model`, so unattended batches keep going.
        """
        def build(base: str, model: str) -> "ChatClient":
            provider = llm.get(f"{role}_provider") or ("ollama" if base.rstrip("/").endswith(":11434") else "openai")
            return cls(base_url=base, model=model, provider=provider, api_key=llm.get("api_key"),
                       timeout=float(llm.get("timeout_s", 600)), context_tokens=int(llm.get("context_tokens", 8192)),
                       disable_thinking=bool(llm.get("disable_thinking", True)))

        primary = build(llm.get(f"{role}_base_url") or llm.get("base_url", "http://localhost:1234/v1"),
                        llm[f"{role}_model"])
        fallback_base, fallback_model = llm.get("fallback_base_url"), llm.get(f"fallback_{role}_model")
        if not probe or not (fallback_base and fallback_model):
            return primary
        try:
            if primary.model in primary.models():
                return primary
        except (OSError, ValueError, KeyError):
            pass
        return build(fallback_base, fallback_model)

    def models(self) -> list[str]:
        if self.provider == "ollama":
            return [m["name"] for m in get_json(self.base_url.rstrip("/") + "/api/tags").get("models", [])]
        return [m["id"] for m in get_json(self.base_url.rstrip("/") + "/models", api_key=self.api_key).get("data", [])]

    def chat(self, system: str, user: str, *, images: list[Path] | None = None, temperature: float = 0.0,
             max_tokens: int = 512, top_p: float = 0.8) -> ChatResult:
        started = time.monotonic()
        if self.provider == "ollama":
            text, usage = self._ollama(system, user, images, temperature, max_tokens, top_p)
        else:
            text, usage = self._openai(system, user, images, temperature, max_tokens, top_p)
        text = strip_thinking(text)
        if not text:
            raise RuntimeError(f"{self.model} returned no text (thinking budget exhausted?)")
        return ChatResult(text=text, seconds=round(time.monotonic() - started, 2), usage=usage)

    def _openai(self, system, user, images, temperature, max_tokens, top_p):
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}]
            for path in images:
                mime = mimetypes.guess_type(str(path))[0] or "image/png"
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{_b64(path)}"}})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": temperature, "top_p": top_p, "max_tokens": max_tokens, "stream": False,
        }
        if self.disable_thinking:
            # Honoured by LM Studio / vLLM / llama.cpp Qwen templates and by Ollama's /v1; others ignore it.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            payload["reasoning_effort"] = "none"
        response = post_json(self.base_url.rstrip("/") + "/chat/completions", payload, self.timeout, self.api_key)
        message = response["choices"][0]["message"]
        return message.get("content") or "", response.get("usage", {})

    def _ollama(self, system, user, images, temperature, max_tokens, top_p):
        message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            message["images"] = [_b64(path) for path in images]
        root = self.base_url.rstrip("/")
        root = root[:-3] if root.endswith("/v1") else root
        payload = {
            "model": self.model, "stream": False, "think": not self.disable_thinking,
            "messages": [{"role": "system", "content": system}, message],
            "options": {"temperature": temperature, "top_p": top_p, "num_ctx": self.context_tokens,
                        "num_predict": max_tokens},
        }
        response = post_json(root + "/api/chat", payload, self.timeout)
        usage = {k: response.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration")}
        return response.get("message", {}).get("content", ""), usage
