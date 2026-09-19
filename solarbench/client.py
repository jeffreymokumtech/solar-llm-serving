"""Streaming OpenAI-compatible chat client that records per-request timings.

Works against vLLM, SGLang, Triton's OpenAI frontend, the gateway and the mock
server, because they all speak the same `/v1/chat/completions` SSE format.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


@dataclass
class RequestResult:
    id: str
    task: str
    ok: bool
    status: int
    t_send: float  # perf_counter seconds
    ttft_s: float | None = None
    e2e_s: float | None = None
    itl_s: list[float] = field(default_factory=list)  # inter-token latencies after the first token
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    chunks: int = 0
    text: str = ""
    finish_reason: str | None = None
    error: str | None = None
    cached_tokens: int | None = None  # prompt tokens served from the prefix cache, when the server reports it

    @property
    def tpot_s(self) -> float | None:
        """Time per output token after the first one (mean inter-token latency)."""
        n = (self.completion_tokens or self.chunks or 0) - 1
        if self.ttft_s is None or self.e2e_s is None or n <= 0:
            return None
        return (self.e2e_s - self.ttft_s) / n

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["tpot_s"] = self.tpot_s
        return d


def build_body(req: dict[str, Any], model: str, guided: bool = True, temperature: float = 0.0) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": req["messages"],
        "max_tokens": req["max_tokens"],
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if guided and req.get("schema"):
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": req["task"], "schema": req["schema"], "strict": True},
        }
    # Qwen3: disable thinking so measured output tokens are the answer, not a reasoning trace.
    body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


async def chat_stream(
    client: httpx.AsyncClient,
    base_url: str,
    req: dict[str, Any],
    model: str,
    *,
    guided: bool = True,
    headers: dict[str, str] | None = None,
    timeout_s: float = 120.0,
) -> RequestResult:
    body = build_body(req, model, guided=guided)
    res = RequestResult(id=req["id"], task=req["task"], ok=False, status=0, t_send=time.perf_counter())
    last_token_t: float | None = None
    try:
        async with client.stream("POST", f"{base_url.rstrip('/')}/v1/chat/completions", json=body, headers=headers, timeout=timeout_s) as resp:
            res.status = resp.status_code
            if resp.status_code != 200:
                res.error = (await resp.aread())[:500].decode(errors="replace")
                return res
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                now = time.perf_counter()
                usage = chunk.get("usage")
                if usage:
                    res.prompt_tokens = usage.get("prompt_tokens")
                    res.completion_tokens = usage.get("completion_tokens")
                    details = usage.get("prompt_tokens_details") or {}
                    res.cached_tokens = details.get("cached_tokens")
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        res.chunks += 1
                        res.text += content
                        if res.ttft_s is None:
                            res.ttft_s = now - res.t_send
                        elif last_token_t is not None:
                            res.itl_s.append(now - last_token_t)
                        last_token_t = now
                    if choice.get("finish_reason"):
                        res.finish_reason = choice["finish_reason"]
            res.e2e_s = time.perf_counter() - res.t_send
            res.ok = res.ttft_s is not None
            if not res.ok:
                res.error = "no content tokens received"
    except (httpx.HTTPError, json.JSONDecodeError) as exc:  # noqa: PERF203
        res.error = f"{type(exc).__name__}: {exc}"
        res.e2e_s = time.perf_counter() - res.t_send
    return res
