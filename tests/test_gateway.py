import httpx
import pytest

from gateway.app import create_app
from gateway.config import GatewayConfig, Upstream
from gateway.ratelimit import TokenBucket
from gateway.router import Router
from solarbench.client import chat_stream
from solarbench.mock_server import app as mock_app
from solarbench.workload.generator import generate


def _cfg(**kw) -> GatewayConfig:
    cfg = GatewayConfig(
        upstreams={"awq": Upstream("awq", "http://mock", "mock-awq", "http://mock/metrics"), "fp16": Upstream("fp16", "http://mock", "mock-fp16")},
        routes={"ticket_triage": "awq", "daily_report_summary": "fp16"},
        default="fp16",
        api_keys={"k1": 1000},
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_token_bucket():
    b = TokenBucket(2)
    assert b.take() and b.take() and not b.take()


def test_router_routes_by_task_and_skips_saturated():
    r = Router(_cfg())
    assert r.pick("ticket_triage").name == "awq"
    assert r.pick("daily_report_summary").name == "fp16"
    assert r.pick(None).name == "fp16"
    r.health["awq"].waiting = 99
    assert r.pick("ticket_triage").name == "fp16"
    r.health["fp16"].up = False
    assert r.pick("ticket_triage") is None


@pytest.fixture
async def gateway_client(monkeypatch):
    app = create_app(_cfg())
    # Point the gateway's outbound client at the in-process mock instead of the network.
    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url="http://mock")
        await app.state.router.poll(app.state.client)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
            yield c


async def test_gateway_streams_and_tags_upstream(gateway_client):
    req = next(r for r in generate(seed=1, n=40) if r["task"] == "ticket_triage")
    r = await chat_stream(gateway_client, "http://gw", req, "any", headers={"Authorization": "Bearer k1"})
    assert r.ok and r.completion_tokens
    resp = await gateway_client.get("/healthz")
    assert resp.json()["upstreams"]["awq"]["up"] is True
    m = (await gateway_client.get("/metrics")).text
    assert "gateway_requests 1" in m and "gateway_completion_tokens" in m


async def test_gateway_rejects_bad_key(gateway_client):
    r = await gateway_client.post("/v1/chat/completions", json={"messages": []}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_gateway_503_without_fallback(gateway_client):
    router = gateway_client._transport.app.state.router
    for h in router.health.values():
        h.up = False
    r = await gateway_client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": False}, headers={"Authorization": "Bearer k1"}
    )
    assert r.status_code == 503
