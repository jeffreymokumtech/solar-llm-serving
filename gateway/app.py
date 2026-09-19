"""FastAPI gateway: API keys, per-key rate limits, task-class routing across
upstreams, saturation-aware fallback to Bedrock, token accounting and
OpenTelemetry spans.

    GATEWAY_UPSTREAMS=fp16=http://localhost:8000=Qwen/Qwen3-8B=http://localhost:8000/metrics \
    GATEWAY_API_KEYS=nv-demo:600 uvicorn gateway.app:app --port 8080
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .bedrock import stream_via_bedrock
from .config import GatewayConfig
from .ratelimit import TokenBucket
from .router import Router

log = logging.getLogger("gateway")


def create_app(cfg: GatewayConfig | None = None) -> FastAPI:
    cfg = cfg or GatewayConfig.from_env()
    router = Router(cfg)
    buckets = {k: TokenBucket(r) for k, r in cfg.api_keys.items()}
    counters = {"requests": 0, "bedrock_fallbacks": 0, "rejected": 0, "prompt_tokens": 0, "completion_tokens": 0, "upstream_errors": 0}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(limits=httpx.Limits(max_connections=1024, max_keepalive_connections=256), timeout=cfg.request_timeout_s)
        await router.poll(app.state.client)
        task = asyncio.create_task(router.poll_forever(app.state.client))
        try:
            yield
        finally:
            task.cancel()
            await app.state.client.aclose()

    app = FastAPI(title="solar-llm-gateway", lifespan=lifespan)
    app.state.router = router
    app.state.counters = counters
    _maybe_instrument(app)

    def authenticate(authorization: str | None) -> str:
        if not cfg.api_keys:
            return "anonymous"
        key = (authorization or "").removeprefix("Bearer ").strip()
        if key not in buckets:
            counters["rejected"] += 1
            raise HTTPException(401, "invalid API key")
        if not buckets[key].take():
            counters["rejected"] += 1
            raise HTTPException(429, "rate limit exceeded")
        return key

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "upstreams": router.snapshot()}

    @app.get("/metrics")
    async def metrics():
        lines = [f"gateway_{k} {v}" for k, v in counters.items()]
        for name, h in router.snapshot().items():
            lines.append(f'gateway_upstream_up{{upstream="{name}"}} {int(h["up"])}')
            lines.append(f'gateway_upstream_waiting{{upstream="{name}"}} {h["waiting"]}')
        return StreamingResponse(iter(["\n".join(lines) + "\n"]), media_type="text/plain")

    @app.get("/v1/models")
    async def models(authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return {"object": "list", "data": [{"id": u.model, "object": "model", "owned_by": name} for name, u in cfg.upstreams.items()]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(default=None), x_task_class: str | None = Header(default=None)):
        key = authenticate(authorization)
        body = await request.json()
        counters["requests"] += 1
        task_class = x_task_class or _infer_task_class(body)
        up = router.pick(task_class)
        t0 = time.perf_counter()
        if up is None:
            if not cfg.bedrock_model_id:
                raise HTTPException(503, "no healthy upstream and no fallback configured")
            counters["bedrock_fallbacks"] += 1
            log.warning("falling back to bedrock key=%s task=%s upstreams=%s", key, task_class, router.snapshot())
            return StreamingResponse(
                _count_usage(stream_via_bedrock(body, cfg.bedrock_model_id, cfg.bedrock_region), counters),
                media_type="text/event-stream",
                headers={"x-served-by": "bedrock", "x-task-class": task_class or ""},
            )
        body["model"] = up.model  # the client asked for "the model"; the gateway decides which replica/precision serves it
        client: httpx.AsyncClient = request.app.state.client
        if not body.get("stream"):
            r = await client.post(f"{up.base_url}/v1/chat/completions", json=body)
            if r.status_code >= 500:
                counters["upstream_errors"] += 1
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
            u = data.get("usage") or {}
            counters["prompt_tokens"] += u.get("prompt_tokens") or 0
            counters["completion_tokens"] += u.get("completion_tokens") or 0
            return JSONResponse(data, status_code=r.status_code, headers={"x-served-by": up.name, "x-task-class": task_class or ""})

        req = client.build_request("POST", f"{up.base_url}/v1/chat/completions", json=body)
        resp = await client.send(req, stream=True)
        if resp.status_code != 200:
            counters["upstream_errors"] += 1
            text = (await resp.aread()).decode(errors="replace")
            await resp.aclose()
            raise HTTPException(resp.status_code, text[:500])

        async def relay():
            try:
                async for line in resp.aiter_lines():
                    if line.startswith("data:") and '"usage"' in line:
                        try:
                            u = json.loads(line[5:]).get("usage") or {}
                            counters["prompt_tokens"] += u.get("prompt_tokens") or 0
                            counters["completion_tokens"] += u.get("completion_tokens") or 0
                        except json.JSONDecodeError:
                            pass
                    yield line + "\n"
            finally:
                await resp.aclose()
                log.info("served key=%s task=%s upstream=%s e2e=%.3fs", key, task_class, up.name, time.perf_counter() - t0)

        return StreamingResponse(relay(), media_type="text/event-stream", headers={"x-served-by": up.name, "x-task-class": task_class or ""})

    return app


def _infer_task_class(body: dict) -> str | None:
    rf = body.get("response_format") or {}
    return (rf.get("json_schema") or {}).get("name")


async def _count_usage(stream, counters):
    async for line in stream:
        if line.startswith("data:") and '"usage"' in line:
            try:
                u = json.loads(line[5:]).get("usage") or {}
                counters["prompt_tokens"] += u.get("prompt_tokens") or 0
                counters["completion_tokens"] += u.get("completion_tokens") or 0
            except json.JSONDecodeError:
                pass
        yield line


def _maybe_instrument(app: FastAPI) -> None:
    """OpenTelemetry is optional: enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set and the packages are installed."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL endpoint set but opentelemetry packages missing; install .[gateway]")
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "solar-llm-gateway"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)


app = create_app() if os.getenv("GATEWAY_UPSTREAMS") else None
