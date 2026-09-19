"""Chooses an upstream for a request and knows when to fall back.

Routing order:
  1. explicit `x-task-class` header -> GATEWAY_ROUTES
  2. otherwise the default upstream
Health: an upstream is *saturated* when its last-polled `num_requests_waiting`
exceeds `max_queue`, and *down* when its last probe failed. Saturated or down
upstreams are skipped; if none is left and Bedrock is configured, the request
goes to Bedrock and the response is tagged `x-served-by: bedrock`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from solarbench.profile import parse_prometheus

from .config import GatewayConfig, Upstream


@dataclass
class Health:
    up: bool = True
    waiting: float = 0.0
    running: float = 0.0
    checked_at: float = 0.0
    consecutive_failures: int = 0


@dataclass
class Router:
    cfg: GatewayConfig
    health: dict[str, Health] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.health = {name: Health() for name in self.cfg.upstreams}

    async def poll(self, client: httpx.AsyncClient) -> None:
        for name, up in self.cfg.upstreams.items():
            h = self.health[name]
            try:
                r = await client.get(up.metrics_url or f"{up.base_url}/health", timeout=3.0)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError("bad status", request=r.request, response=r)
                if up.metrics_url:
                    m = parse_prometheus(r.text)
                    h.waiting, h.running = m.get("waiting", 0.0), m.get("running", 0.0)
                h.up, h.consecutive_failures = True, 0
            except httpx.HTTPError:
                h.consecutive_failures += 1
                h.up = h.consecutive_failures < 2
            h.checked_at = time.monotonic()

    async def poll_forever(self, client: httpx.AsyncClient, interval_s: float = 2.0) -> None:
        while True:
            await self.poll(client)
            await asyncio.sleep(interval_s)

    def candidates(self, task_class: str | None) -> list[Upstream]:
        names: list[str] = []
        if task_class and task_class in self.cfg.routes:
            names.append(self.cfg.routes[task_class])
        if self.cfg.default and self.cfg.default not in names:
            names.append(self.cfg.default)
        names += [n for n in self.cfg.upstreams if n not in names]
        return [self.cfg.upstreams[n] for n in names if n in self.cfg.upstreams]

    def pick(self, task_class: str | None) -> Upstream | None:
        for up in self.candidates(task_class):
            h = self.health[up.name]
            if h.up and h.waiting <= up.max_queue:
                return up
        return None

    def snapshot(self) -> dict[str, dict]:
        return {
            n: {"up": h.up, "waiting": h.waiting, "running": h.running, "age_s": round(time.monotonic() - h.checked_at, 1)}
            for n, h in self.health.items()
        }
