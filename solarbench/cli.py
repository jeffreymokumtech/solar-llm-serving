"""`solarbench` command line: gen | run | quality | budget | report | profile."""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path

import click

from . import budget as budget_mod
from . import quality as quality_mod
from .prices import INSTANCE_PRICES, price_for
from .report import load_runs, plots, render
from .runner import RunConfig, run, run_id
from .workload.generator import DEFAULT_MIX, TASKS, read_dataset, write_dataset

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "solarbench" / "datasets"
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"


@click.group()
def main() -> None:
    """Benchmark LLM serving stacks on a solar O&M workload."""


@main.command()
@click.option("--seed", default=42, show_default=True)
@click.option("--n", default=200, show_default=True, help="requests per dataset")
@click.option("--out", type=click.Path(path_type=Path), default=DATASETS / "solar_ops_v1.jsonl", show_default=True)
@click.option("--task", "only_task", type=click.Choice(TASKS), default=None, help="single-task dataset")
@click.option("--check", is_flag=True, help="regenerate and fail if the file would change (CI)")
def gen(seed: int, n: int, out: Path, only_task: str | None, check: bool) -> None:
    """Generate the synthetic workload dataset (deterministic in --seed)."""
    mix = {only_task: 1.0} if only_task else DEFAULT_MIX
    if check:
        tmp = out.with_suffix(".check.jsonl")
        write_dataset(tmp, seed, n, mix)
        same = tmp.read_bytes() == out.read_bytes() if out.exists() else False
        tmp.unlink()
        click.echo("dataset up to date" if same else "dataset differs from generator output; run `solarbench gen`")
        sys.exit(0 if same else 1)
    count = write_dataset(out, seed, n, mix)
    click.echo(f"wrote {count} requests to {out}")


@main.command(name="run")
@click.option("--stack", required=True, help="label, e.g. vllm-fp16, vllm-awq, sglang-fp16, trtllm-int4")
@click.option("--base-url", default="http://localhost:8000", show_default=True)
@click.option("--model", default="Qwen/Qwen3-8B", show_default=True)
@click.option("--dataset", type=click.Path(path_type=Path), default=DATASETS / "solar_ops_v1.jsonl", show_default=True)
@click.option("--mode", type=click.Choice(["closed", "open"]), default="open", show_default=True)
@click.option("--concurrency", default=8, show_default=True)
@click.option("--rps", default=2.0, show_default=True)
@click.option("--duration", "duration_s", default=120.0, show_default=True)
@click.option("--num-requests", default=None, type=int)
@click.option("--warmup", default=4, show_default=True)
@click.option("--no-guided", is_flag=True, help="send structured tasks without response_format (engines without guided decoding)")
@click.option("--instance-type", default=None, type=click.Choice(sorted(INSTANCE_PRICES)))
@click.option("--price-per-hour", default=None, type=float, help="what you actually pay; defaults to the typical spot price")
@click.option("--metrics-url", "metrics_urls", multiple=True, help="Prometheus text endpoints to sample (engine /metrics, DCGM)")
@click.option("--api-key", default=None, envvar="SOLARBENCH_API_KEY")
@click.option("--notes", default="")
@click.option("--out", type=click.Path(path_type=Path), default=RESULTS, show_default=True)
def run_cmd(
    stack,
    base_url,
    model,
    dataset,
    mode,
    concurrency,
    rps,
    duration_s,
    num_requests,
    warmup,
    no_guided,
    instance_type,
    price_per_hour,
    metrics_urls,
    api_key,
    notes,
    out,
) -> None:
    """Run a benchmark and write results/<run_id>/{config,summary}.json + raw.jsonl + metrics.csv."""
    if price_per_hour is None and instance_type:
        price_per_hour = price_for(instance_type)
    cfg = RunConfig(
        stack=stack,
        base_url=base_url,
        model=model,
        dataset=str(dataset.name),
        mode=mode,
        concurrency=concurrency,
        rps=rps,
        duration_s=duration_s,
        num_requests=num_requests,
        warmup=warmup,
        guided=not no_guided,
        instance_type=instance_type,
        price_per_hour=price_per_hour,
        metrics_urls=list(metrics_urls) or None,
        api_key=api_key,
        notes=notes,
    )
    reqs = read_dataset(dataset)
    out_dir = out / run_id(cfg)
    summary = asyncio.run(run(cfg, reqs, out_dir))
    click.echo(json.dumps({k: summary[k] for k in ("run_id", "ok", "errors", "req_per_s", "output_tok_per_s")}, indent=2))
    click.echo(
        f"TTFT p50/p99: {summary['ttft_s']['p50']:.3f}/{summary['ttft_s']['p99']:.3f} s; "
        f"TPOT p50: {(summary['tpot_s']['p50'] or 0) * 1000:.1f} ms; goodput {summary['slo']['goodput_req_per_s']:.2f} req/s"
    )
    if summary.get("cost"):
        click.echo(f"cost: ${summary['cost']['usd_per_m_output_tokens']:.3f} per M output tokens at ${price_per_hour}/h")


@main.command()
@click.argument("run_dir", type=click.Path(path_type=Path, exists=True))
@click.option("--dataset", type=click.Path(path_type=Path), default=DATASETS / "solar_ops_v1.jsonl", show_default=True)
def quality(run_dir: Path, dataset: Path) -> None:
    """Score the structured outputs of a run against ground truth -> quality.json."""
    reqs = {r["id"]: r for r in read_dataset(dataset)}
    scores = []
    with (run_dir / "raw.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("ok") and r["id"] in reqs:
                scores.append(quality_mod.score(reqs[r["id"]], r["text"]))
    agg = quality_mod.aggregate(scores)
    (run_dir / "quality.json").write_text(json.dumps(agg, indent=2, sort_keys=True))
    for task, q in agg.items():
        click.echo(f"{task:24s} n={q['n']:4d} valid_json={q['valid_json_rate']:.2f} schema={q['schema_valid_rate']:.2f} score={q['score']:.3f}")


@main.command(name="budget")
@click.option("--model", default="Qwen/Qwen3-8B", type=click.Choice(sorted(budget_mod.MODELS)))
@click.option("--dtype", default="fp16", type=click.Choice(sorted(budget_mod.BYTES)))
@click.option("--gpu", default="A10G", type=click.Choice(sorted(budget_mod.GPUS_GIB)))
@click.option("--max-seqs", default=64, show_default=True)
@click.option("--max-len", default=8192, show_default=True)
@click.option("--kv-dtype", default="fp16", type=click.Choice(["fp16", "fp8", "int8"]))
@click.option("--util", default=0.90, show_default=True, help="gpu_memory_utilization")
@click.option("--tp", default=1, show_default=True)
def budget_cmd(model, dtype, gpu, max_seqs, max_len, kv_dtype, util, tp) -> None:
    """Predict GPU memory use and how many concurrent tokens fit in the KV cache."""
    b = budget_mod.budget(model, dtype, gpu, max_seqs, max_len, kv_dtype, util, tp)
    for k, v in b.items():
        click.echo(f"{k:32s} {v}")


@main.command()
@click.option("--results", type=click.Path(path_type=Path), default=RESULTS, show_default=True)
@click.option("--out", type=click.Path(path_type=Path), default=DOCS / "results.md", show_default=True)
@click.option("--plots-dir", type=click.Path(path_type=Path), default=DOCS / "plots", show_default=True)
@click.option("--check", is_flag=True, help="fail if docs/results.md is stale (CI)")
def report(results: Path, out: Path, plots_dir: Path, check: bool) -> None:
    """Render docs/results.md and docs/plots from results/."""
    runs = load_runs(results) if results.exists() else []
    md = render(runs)
    if check:
        same = out.exists() and out.read_text() == md
        click.echo("results.md up to date" if same else "results.md is stale; run `solarbench report`")
        sys.exit(0 if same else 1)
    out.write_text(md)
    written = plots(runs, plots_dir) if runs else []
    click.echo(f"wrote {out} and {len(written)} plots")


@main.command()
@click.argument("metrics_csv", type=click.Path(path_type=Path, exists=True))
def profile(metrics_csv: Path) -> None:
    """Summarise a metrics.csv from a run: peaks and the time at which queueing started."""
    with metrics_csv.open() as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in rows[0] if c != "t_s"]
    for c in cols:
        vals = [float(r[c]) for r in rows if r.get(c) not in (None, "")]
        if vals:
            click.echo(f"{c:24s} min={min(vals):10.2f} max={max(vals):10.2f} last={vals[-1]:10.2f}")
    waits = [(float(r["t_s"]), float(r["waiting"])) for r in rows if r.get("waiting")]
    first = next((t for t, w in waits if w > 0), None)
    click.echo(f"first queueing at t={first}s" if first is not None else "no queueing observed")


if __name__ == "__main__":
    main()
