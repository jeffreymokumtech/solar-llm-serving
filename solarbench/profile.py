"""Samples Prometheus text endpoints while a benchmark runs: the engine's own
metrics (vLLM / SGLang / Triton) and DCGM's GPU metrics. Produces a CSV with
one row per second so memory, KV-cache usage and queue depth can be plotted
against the load."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import re
import time
from pathlib import Path

import httpx

# metric name -> column. vLLM names carry a "vllm:" prefix; SGLang uses "sglang:"; DCGM "DCGM_FI_DEV_*".
WATCH = {
    "vllm:num_requests_running": "running",
    "vllm:num_requests_waiting": "waiting",
    "vllm:gpu_cache_usage_perc": "kv_cache_usage",
    "vllm:kv_cache_usage_perc": "kv_cache_usage",
    "vllm:num_preemptions_total": "preemptions_total",
    "vllm:prefix_cache_hits_total": "prefix_hits_total",
    "vllm:prefix_cache_queries_total": "prefix_queries_total",
    "vllm:spec_decode_num_accepted_tokens_total": "spec_accepted_total",
    "vllm:spec_decode_num_draft_tokens_total": "spec_draft_total",
    "sglang:num_running_reqs": "running",
    "sglang:num_queue_reqs": "waiting",
    "sglang:token_usage": "kv_cache_usage",
    "sglang:cache_hit_rate": "prefix_hit_rate",
    "nv_inference_queue_duration_us": "triton_queue_us_total",
    "nv_inference_pending_request_count": "waiting",
    "DCGM_FI_DEV_FB_USED": "gpu_mem_used_mib",
    "DCGM_FI_DEV_FB_FREE": "gpu_mem_free_mib",
    "DCGM_FI_DEV_GPU_UTIL": "gpu_util_pct",
    "DCGM_FI_DEV_POWER_USAGE": "gpu_power_w",
    "DCGM_FI_DEV_GPU_TEMP": "gpu_temp_c",
    "DCGM_FI_DEV_MEM_COPY_UTIL": "gpu_mem_bw_util_pct",
}
LINE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN)")


def parse_prometheus(text: str) -> dict[str, float]:
    """Sum every sample of a watched metric (across labels, e.g. multiple GPUs) into one value."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if not m:
            continue
        name, _labels, value = m.groups()
        col = WATCH.get(name)
        if col is None:
            continue
        try:
            out[col] = out.get(col, 0.0) + float(value)
        except ValueError:
            continue
    return out


class Sampler:
    """`async with Sampler([...urls], path)` samples every `interval_s` until exit."""

    def __init__(self, urls: list[str], path: Path, interval_s: float = 1.0, client: httpx.AsyncClient | None = None) -> None:
        self.urls = [u for u in urls if u]
        self.path = path
        self.interval_s = interval_s
        self.client = client
        self._task: asyncio.Task | None = None
        self.rows: list[dict[str, float]] = []

    async def _loop(self) -> None:
        t0 = time.perf_counter()
        client = self.client or httpx.AsyncClient(timeout=5.0)
        try:
            while True:
                row: dict[str, float] = {"t_s": round(time.perf_counter() - t0, 3)}
                for url in self.urls:
                    try:
                        r = await client.get(url, timeout=5.0)
                        row.update(parse_prometheus(r.text))
                    except httpx.HTTPError:
                        pass
                self.rows.append(row)
                await asyncio.sleep(self.interval_s)
        finally:
            if self.client is None:
                await client.aclose()

    async def __aenter__(self) -> Sampler:
        if self.urls:
            self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.write()

    def write(self) -> None:
        if not self.rows:
            return
        cols = ["t_s"] + sorted({k for r in self.rows for k in r} - {"t_s"})
        with self.path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in self.rows:
                w.writerow(r)

    def peaks(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for col in ("gpu_mem_used_mib", "kv_cache_usage", "waiting", "running", "gpu_util_pct", "gpu_power_w"):
            vals = [r[col] for r in self.rows if col in r]
            if vals:
                out[f"peak_{col}"] = max(vals)
        return out
