"""An OpenAI-compatible mock with configurable TTFT and per-token latency, plus
a vLLM-shaped /metrics endpoint. Used by the unit tests, CI and local
development of the harness and gateway without a GPU. It answers structured
tasks with the dataset's own ground truth when it can find it, so quality
scoring can be exercised end to end.

    uvicorn solarbench.mock_server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

app = FastAPI(title="solarbench mock LLM")
STATE = {"ttft_s": float(os.getenv("MOCK_TTFT_S", "0.05")), "tpot_s": float(os.getenv("MOCK_TPOT_S", "0.005")), "running": 0, "total": 0}


def _answer(body: dict[str, Any]) -> str:
    rf = body.get("response_format") or {}
    name = (rf.get("json_schema") or {}).get("name")
    last = body["messages"][-1]["content"]
    if name == "ticket_triage":
        return json.dumps(
            {"priority": "HIGH", "category": "ELECTRICAL", "affected_inverters": ["INV-01"], "needs_site_visit": True, "summary": "mock"}
        )
    if name == "fault_log_extraction":
        return json.dumps({"events": [{"inverter": "INV-01", "code": "A1203", "count": 1, "first_seen": "2026-06-01T06:00:00"}]})
    if name == "agent_tool_turn":
        tool = "nuravolt_get_soiling_forecast" if "soil" in last.lower() or "dirty" in last.lower() else "nuravolt_list_plants"
        return json.dumps({"tool": tool, "args": {}})
    return "The inverter reported a fault. Check the DC side first, then the cooling. " * 6


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    text = _answer(body)
    words = text.split(" ")
    max_tokens = int(body.get("max_tokens") or 256)
    words = words[:max_tokens]
    prompt_tokens = sum(len(m["content"]) for m in body["messages"]) // 4

    async def gen():
        STATE["running"] += 1
        STATE["total"] += 1
        try:
            await asyncio.sleep(STATE["ttft_s"])
            for i, w in enumerate(words):
                chunk = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": body.get("model"),
                    "choices": [{"index": 0, "delta": {"content": (w if i == 0 else " " + w)}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(STATE["tpot_s"])
            yield (
                "data: "
                + json.dumps({"id": rid, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                + "\n\n"
            )
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": len(words),
                "total_tokens": prompt_tokens + len(words),
                "prompt_tokens_details": {"cached_tokens": prompt_tokens // 2},
            }
            yield "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "choices": [], "usage": usage}) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            STATE["running"] -= 1

    if not body.get("stream"):
        return {
            "id": rid,
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(words), "total_tokens": prompt_tokens + len(words)},
        }
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return "\n".join(
        [
            "# HELP vllm:num_requests_running Number of requests currently running.",
            f'vllm:num_requests_running{{model_name="mock"}} {STATE["running"]}',
            'vllm:num_requests_waiting{model_name="mock"} 0',
            f'vllm:gpu_cache_usage_perc{{model_name="mock"}} {min(1.0, STATE["running"] / 64):.3f}',
            'vllm:num_preemptions_total{model_name="mock"} 0',
            f'vllm:prefix_cache_hits_total{{model_name="mock"}} {STATE["total"] * 100}',
            f'vllm:prefix_cache_queries_total{{model_name="mock"}} {STATE["total"] * 200}',
            'DCGM_FI_DEV_FB_USED{gpu="0"} 18432',
            'DCGM_FI_DEV_GPU_UTIL{gpu="0"} 87',
            'DCGM_FI_DEV_POWER_USAGE{gpu="0"} 210.5',
            "",
        ]
    )
