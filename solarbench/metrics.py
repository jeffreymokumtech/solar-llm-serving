"""Latency and throughput statistics over a list of RequestResult."""

from __future__ import annotations

from typing import Any

import numpy as np

from .client import RequestResult

PCTS = (50, 90, 95, 99)


def pct(values: list[float], p: float) -> float | None:
    return float(np.percentile(values, p)) if values else None


def stats(values: list[float]) -> dict[str, float | None]:
    return {"mean": float(np.mean(values)) if values else None, **{f"p{p}": pct(values, p) for p in PCTS}}


def summarize(
    results: list[RequestResult],
    wall_s: float,
    *,
    slo_ttft_s: float = 1.0,
    slo_tpot_s: float = 0.05,
    price_per_hour: float | None = None,
) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    ttft = [r.ttft_s for r in ok if r.ttft_s is not None]
    tpot = [r.tpot_s for r in ok if r.tpot_s is not None]
    itl = [x for r in ok for x in r.itl_s]
    e2e = [r.e2e_s for r in ok if r.e2e_s is not None]
    out_tokens = sum((r.completion_tokens or r.chunks) for r in ok)
    in_tokens = sum((r.prompt_tokens or 0) for r in ok)
    cached = [r.cached_tokens for r in ok if r.cached_tokens is not None]
    good = [r for r in ok if r.ttft_s is not None and r.ttft_s <= slo_ttft_s and (r.tpot_s is None or r.tpot_s <= slo_tpot_s)]
    out_tps = out_tokens / wall_s if wall_s > 0 else 0.0
    total_tps = (out_tokens + in_tokens) / wall_s if wall_s > 0 else 0.0
    summary: dict[str, Any] = {
        "requests": len(results),
        "ok": len(ok),
        "errors": len(results) - len(ok),
        "wall_s": wall_s,
        "req_per_s": len(ok) / wall_s if wall_s > 0 else 0.0,
        "output_tok_per_s": out_tps,
        "total_tok_per_s": total_tps,
        "prompt_tokens": in_tokens,
        "completion_tokens": out_tokens,
        "prefix_cached_tokens": sum(cached) if cached else None,
        "prefix_cache_hit_ratio": (sum(cached) / in_tokens) if cached and in_tokens else None,
        "ttft_s": stats(ttft),
        "tpot_s": stats(tpot),
        "itl_s": stats(itl),
        "e2e_s": stats(e2e),
        "slo": {
            "ttft_s": slo_ttft_s,
            "tpot_s": slo_tpot_s,
            "goodput_req_per_s": len(good) / wall_s if wall_s > 0 else 0.0,
            "good_fraction": len(good) / len(ok) if ok else None,
        },
        "finish_reasons": _count(r.finish_reason for r in ok),
        "by_task": {},
    }
    if price_per_hour is not None:
        summary["cost"] = cost_per_million(out_tps, total_tps, price_per_hour)
    for task in sorted({r.task for r in results}):
        rs = [r for r in ok if r.task == task]
        summary["by_task"][task] = {
            "n": len(rs),
            "ttft_s": stats([r.ttft_s for r in rs if r.ttft_s is not None]),
            "tpot_s": stats([r.tpot_s for r in rs if r.tpot_s is not None]),
            "e2e_s": stats([r.e2e_s for r in rs if r.e2e_s is not None]),
            "prompt_tokens_mean": float(np.mean([r.prompt_tokens for r in rs if r.prompt_tokens])) if any(r.prompt_tokens for r in rs) else None,
            "completion_tokens_mean": float(np.mean([r.completion_tokens or r.chunks for r in rs])) if rs else None,
        }
    return summary


def cost_per_million(output_tps: float, total_tps: float, price_per_hour: float) -> dict[str, float | None]:
    """$ per million tokens at the observed throughput. Two views: output tokens only
    (what API vendors mostly bill on) and all tokens processed."""
    per_s = price_per_hour / 3600.0
    return {
        "price_per_hour": price_per_hour,
        "usd_per_m_output_tokens": (per_s / output_tps * 1e6) if output_tps > 0 else None,
        "usd_per_m_total_tokens": (per_s / total_tps * 1e6) if total_tps > 0 else None,
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[str(it)] = out.get(str(it), 0) + 1
    return dict(sorted(out.items()))
