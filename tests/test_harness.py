import csv
import json

import pytest

from solarbench import budget, quality
from solarbench.client import RequestResult, build_body, chat_stream
from solarbench.metrics import cost_per_million, summarize
from solarbench.profile import parse_prometheus
from solarbench.report import load_runs, render
from solarbench.runner import RunConfig, run
from solarbench.workload.generator import generate


async def test_stream_records_ttft_tokens_and_usage(mock_client):
    req = next(generate(seed=1, n=5))
    async with mock_client as c:
        r = await chat_stream(c, "http://mock", req, "mock-model")
    assert r.ok and r.status == 200
    assert r.ttft_s is not None and r.ttft_s >= 0.004
    assert r.completion_tokens == r.chunks > 1
    assert r.prompt_tokens and r.cached_tokens == r.prompt_tokens // 2
    assert r.tpot_s is not None and r.tpot_s > 0
    assert len(r.itl_s) == r.chunks - 1
    assert r.finish_reason == "stop"


def test_build_body_uses_guided_only_for_structured_tasks():
    reqs = list(generate(seed=2, n=60))
    triage = next(r for r in reqs if r["task"] == "ticket_triage")
    free = next(r for r in reqs if r["task"] == "alert_narration")
    assert build_body(triage, "m")["response_format"]["type"] == "json_schema"
    assert "response_format" not in build_body(free, "m")
    assert "response_format" not in build_body(triage, "m", guided=False)
    assert build_body(free, "m")["chat_template_kwargs"] == {"enable_thinking": False}


async def test_closed_and_open_loop_runs_write_artifacts(mock_client, tmp_path):
    reqs = list(generate(seed=3, n=12))
    async with mock_client as c:
        cfg = RunConfig(
            stack="mock",
            base_url="http://mock",
            model="mock-model",
            dataset="x",
            mode="closed",
            concurrency=4,
            num_requests=12,
            warmup=1,
            price_per_hour=0.4,
            metrics_urls=["http://mock/metrics"],
        )
        s1 = await run(cfg, reqs, tmp_path / "closed", client=c)
        cfg2 = RunConfig(stack="mock", base_url="http://mock", model="mock-model", dataset="x", mode="open", rps=200, duration_s=0.1, warmup=0)
        s2 = await run(cfg2, reqs, tmp_path / "open", client=c)
    assert s1["ok"] == 12 and s1["errors"] == 0
    assert s1["cost"]["usd_per_m_output_tokens"] > 0
    assert s1["engine_peaks"]["peak_gpu_mem_used_mib"] == 18432
    assert (tmp_path / "closed" / "summary.json").exists() and (tmp_path / "closed" / "config.json").exists()
    with (tmp_path / "closed" / "metrics.csv").open() as f:
        assert "gpu_mem_used_mib" in csv.DictReader(f).fieldnames
    assert s2["ok"] >= 5
    assert set(s1["by_task"]) <= {"alert_narration", "ticket_triage", "fault_log_extraction", "daily_report_summary", "agent_tool_turn"}


def test_summary_math_and_goodput():
    rs = [RequestResult(id=str(i), task="t", ok=True, status=200, t_send=0, ttft_s=0.2 * (i + 1), e2e_s=2.0, completion_tokens=41) for i in range(5)]
    s = summarize(rs, wall_s=10.0, slo_ttft_s=0.5)
    assert s["output_tok_per_s"] == pytest.approx(5 * 41 / 10)
    assert s["ttft_s"]["p50"] == pytest.approx(0.6)
    assert s["slo"]["goodput_req_per_s"] == pytest.approx(2 / 10)  # ttft 0.2, 0.4 pass; tpot (2-ttft)/40 = 0.045 < 0.05
    c = cost_per_million(output_tps=100.0, total_tps=1000.0, price_per_hour=0.36)
    assert c["usd_per_m_output_tokens"] == pytest.approx(1.0)


def test_prometheus_parser_sums_across_labels():
    text = 'DCGM_FI_DEV_FB_USED{gpu="0"} 1000\nDCGM_FI_DEV_FB_USED{gpu="1"} 500\nvllm:num_requests_waiting{m="a"} 3\n# comment\nother 9\n'
    out = parse_prometheus(text)
    assert out == {"gpu_mem_used_mib": 1500.0, "waiting": 3.0}


def test_budget_calculator_qwen3_8b_on_a10g():
    b = budget.budget("Qwen/Qwen3-8B", "fp16", "A10G", max_num_seqs=64, max_model_len=8192)
    assert 15 < b["weights_gib_per_gpu"] < 16
    assert b["kv_bytes_per_token_per_gpu"] == 2 * 36 * 8 * 128 * 2  # 147456
    assert 0 < b["kv_tokens_that_fit"] < 40000
    b4 = budget.budget("Qwen/Qwen3-8B-AWQ", "awq", "A10G")
    assert b4["weights_gib_per_gpu"] < 7 and b4["kv_tokens_that_fit"] > b["kv_tokens_that_fit"]
    tp = budget.budget("Qwen/Qwen3-8B", "fp16", "A10G", tensor_parallel=4)
    assert tp["weights_gib_per_gpu"] == pytest.approx(b["weights_gib_per_gpu"] / 4, rel=0.01)


def test_quality_scoring():
    reqs = list(generate(seed=9, n=120))
    triage = next(r for r in reqs if r["task"] == "ticket_triage")
    perfect = {**triage["expected"], "summary": "ok"}
    s = quality.score(triage, json.dumps(perfect))
    assert s["valid_json"] and s["schema_valid"] and s["score"] == 1.0
    wrong = {**perfect, "priority": "LOW" if perfect["priority"] != "LOW" else "HIGH"}
    assert quality.score(triage, json.dumps(wrong))["score"] == 0.75
    assert quality.score(triage, "not json")["score"] == 0.0
    log = next(r for r in reqs if r["task"] == "fault_log_extraction")
    s2 = quality.score(log, json.dumps(log["expected"]))
    assert s2["fields"]["pair_f1"] == 1.0 and s2["fields"]["count_accuracy"] == 1.0
    agg = quality.aggregate([s, s2])
    assert set(agg) == {"ticket_triage", "fault_log_extraction"}


async def test_report_renders_deterministically(mock_client, tmp_path):
    reqs = list(generate(seed=3, n=8))
    async with mock_client as c:
        cfg = RunConfig(stack="mock", base_url="http://mock", model="mock-model", dataset="x", mode="closed", concurrency=2, num_requests=8, warmup=0)
        await run(cfg, reqs, tmp_path / "r1", client=c)
    runs = load_runs(tmp_path)
    md1, md2 = render(runs), render(load_runs(tmp_path))
    assert md1 == md2 and "## Headline" in md1 and "| mock |" in md1
    assert render([]).endswith("_No runs yet._\n")
