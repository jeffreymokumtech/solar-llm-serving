"""Load generation: closed-loop (fixed concurrency) and open-loop (Poisson
arrivals at a target request rate). Open-loop is the honest one for serving
systems: it keeps sending at the offered load even when the server falls
behind, so queueing shows up in TTFT instead of being hidden by back-pressure.
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .client import RequestResult, chat_stream
from .metrics import summarize
from .profile import Sampler


@dataclass
class RunConfig:
    stack: str  # e.g. vllm-fp16, vllm-awq, sglang-fp16, trtllm-int4, gateway
    base_url: str
    model: str
    dataset: str
    mode: str  # closed | open
    concurrency: int = 8  # closed-loop
    rps: float = 2.0  # open-loop
    duration_s: float = 120.0  # open-loop
    num_requests: int | None = None  # closed-loop total; default = len(dataset)
    warmup: int = 4
    guided: bool = True
    instance_type: str | None = None
    price_per_hour: float | None = None
    metrics_urls: list[str] | None = None
    api_key: str | None = None
    notes: str = ""
    seed: int = 7
    max_conn: int = 512

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run_id(cfg: RunConfig) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    load = f"c{cfg.concurrency}" if cfg.mode == "closed" else f"rps{cfg.rps:g}"
    return f"{stamp}-{cfg.stack}-{cfg.mode}-{load}"


async def _one(client: httpx.AsyncClient, cfg: RunConfig, req: dict[str, Any], headers: dict[str, str] | None) -> RequestResult:
    return await chat_stream(client, cfg.base_url, req, cfg.model, guided=cfg.guided, headers=headers)


async def closed_loop(client: httpx.AsyncClient, cfg: RunConfig, reqs: list[dict[str, Any]], headers) -> tuple[list[RequestResult], float]:
    sem = asyncio.Semaphore(cfg.concurrency)
    total = cfg.num_requests or len(reqs)

    async def bounded(req):
        async with sem:
            return await _one(client, cfg, req, headers)

    t0 = time.perf_counter()
    results = await asyncio.gather(*(bounded(reqs[i % len(reqs)]) for i in range(total)))
    return list(results), time.perf_counter() - t0


async def open_loop(client: httpx.AsyncClient, cfg: RunConfig, reqs: list[dict[str, Any]], headers) -> tuple[list[RequestResult], float]:
    rng = random.Random(cfg.seed)
    tasks: list[asyncio.Task] = []
    t0 = time.perf_counter()
    i = 0
    next_t = 0.0
    while next_t < cfg.duration_s:
        delay = next_t - (time.perf_counter() - t0)
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(_one(client, cfg, reqs[i % len(reqs)], headers)))
        i += 1
        next_t += rng.expovariate(cfg.rps)
    results = await asyncio.gather(*tasks)
    return list(results), time.perf_counter() - t0


async def run(cfg: RunConfig, reqs: list[dict[str, Any]], out_dir: Path, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
    owns_client = client is None
    client = client or httpx.AsyncClient(limits=httpx.Limits(max_connections=cfg.max_conn, max_keepalive_connections=cfg.max_conn))
    try:
        if cfg.warmup:
            await asyncio.gather(*(_one(client, cfg, reqs[i % len(reqs)], headers) for i in range(cfg.warmup)))
        async with Sampler(cfg.metrics_urls or [], out_dir / "metrics.csv", client=client) as sampler:
            if cfg.mode == "closed":
                results, wall = await closed_loop(client, cfg, reqs, headers)
            elif cfg.mode == "open":
                results, wall = await open_loop(client, cfg, reqs, headers)
            else:
                raise ValueError(f"mode must be closed or open, got {cfg.mode}")
    finally:
        if owns_client:
            await client.aclose()

    with (out_dir / "raw.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r.to_json()) + "\n")
    summary = summarize(results, wall, price_per_hour=cfg.price_per_hour)
    summary["engine_peaks"] = sampler.peaks()
    summary["run_id"] = out_dir.name
    summary["stack"] = cfg.stack
    summary["mode"] = cfg.mode
    summary["load"] = cfg.concurrency if cfg.mode == "closed" else cfg.rps
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (out_dir / "config.json").write_text(json.dumps({**cfg.to_json(), "git_sha": _git_sha(), "started": datetime.now(UTC).isoformat()}, indent=2))
    return summary
