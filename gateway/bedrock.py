"""Bedrock fallback: converts an OpenAI chat request into a Converse API call
and streams the answer back in OpenAI SSE chunks so the client cannot tell the
difference (except for the x-served-by header)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any


def _to_converse(body: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    system = [{"text": m["content"]} for m in body["messages"] if m["role"] == "system"]
    messages = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in body["messages"] if m["role"] != "system"]
    inference = {"maxTokens": int(body.get("max_tokens") or 512), "temperature": float(body.get("temperature") or 0.0)}
    return system, messages, inference


async def stream_via_bedrock(body: dict[str, Any], model_id: str, region: str) -> AsyncIterator[str]:
    import boto3  # lazy: only needed when the fallback is configured

    client = boto3.client("bedrock-runtime", region_name=region)
    system, messages, inference = _to_converse(body)
    if body.get("response_format"):
        # Bedrock Converse has no JSON-schema mode for every model; ask in the prompt and let the caller validate.
        schema = body["response_format"].get("json_schema", {}).get("schema")
        system.append({"text": "Answer with JSON only matching this schema: " + json.dumps(schema)})
    rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(
        None, lambda: client.converse_stream(modelId=model_id, system=system, messages=messages, inferenceConfig=inference)
    )
    usage = None
    for event in resp["stream"]:
        if "contentBlockDelta" in event:
            text = event["contentBlockDelta"]["delta"].get("text", "")
            if text:
                chunk = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
        elif "messageStop" in event:
            yield (
                "data: "
                + json.dumps({"id": rid, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                + "\n\n"
            )
        elif "metadata" in event:
            u = event["metadata"].get("usage", {})
            usage = {"prompt_tokens": u.get("inputTokens"), "completion_tokens": u.get("outputTokens"), "total_tokens": u.get("totalTokens")}
        await asyncio.sleep(0)
    if usage:
        yield "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "choices": [], "usage": usage}) + "\n\n"
    yield "data: [DONE]\n\n"
