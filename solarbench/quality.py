"""Scores structured outputs against the ground truth embedded in the dataset.
Used to show what quantization (or a different engine) does to answer
quality, not just to speed."""

from __future__ import annotations

import json
from typing import Any

import jsonschema


def _parse(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def score(req: dict[str, Any], text: str) -> dict[str, Any]:
    """Return {valid_json, schema_valid, fields: {name: 0/1}, score: mean of fields}."""
    task = req["task"]
    expected = req.get("expected")
    out: dict[str, Any] = {"task": task, "valid_json": False, "schema_valid": False, "fields": {}, "score": 0.0}
    if task in ("alert_narration", "daily_report_summary"):
        # free text: only a cheap grounding check (mentions the alarm code) for narration
        if task == "alert_narration" and expected:
            out["fields"]["mentions_code"] = float(expected["code"] in text)
            out["score"] = out["fields"]["mentions_code"]
        return out
    obj = _parse(text)
    if obj is None:
        return out
    out["valid_json"] = True
    if req.get("schema"):
        try:
            jsonschema.validate(obj, req["schema"])
            out["schema_valid"] = True
        except jsonschema.ValidationError:
            out["schema_valid"] = False
    if task == "ticket_triage" and expected:
        f = out["fields"]
        f["priority"] = float(obj.get("priority") == expected["priority"])
        f["category"] = float(obj.get("category") == expected["category"])
        f["affected_inverters"] = float(sorted(obj.get("affected_inverters") or []) == expected["affected_inverters"])
        f["needs_site_visit"] = float(obj.get("needs_site_visit") == expected["needs_site_visit"])
    elif task == "fault_log_extraction" and expected:
        got = {(e.get("inverter"), e.get("code")): e for e in obj.get("events") or [] if isinstance(e, dict)}
        exp = {(e["inverter"], e["code"]): e for e in expected["events"]}
        tp = len(set(got) & set(exp))
        precision = tp / len(got) if got else 0.0
        recall = tp / len(exp) if exp else 0.0
        counts_ok = sum(1 for k in set(got) & set(exp) if got[k].get("count") == exp[k]["count"])
        out["fields"] = {
            "pair_precision": precision,
            "pair_recall": recall,
            "pair_f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
            "count_accuracy": counts_ok / len(exp) if exp else 0.0,
        }
    elif task == "agent_tool_turn" and expected:
        out["fields"]["tool"] = float(obj.get("tool") == expected["tool"])
    if out["fields"]:
        out["score"] = sum(out["fields"].values()) / len(out["fields"])
    return out


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, dict[str, Any]] = {}
    for s in scores:
        agg = by_task.setdefault(s["task"], {"n": 0, "valid_json": 0, "schema_valid": 0, "score_sum": 0.0, "fields": {}})
        agg["n"] += 1
        agg["valid_json"] += int(s["valid_json"])
        agg["schema_valid"] += int(s["schema_valid"])
        agg["score_sum"] += s["score"]
        for k, v in s["fields"].items():
            agg["fields"][k] = agg["fields"].get(k, 0.0) + v
    out = {}
    for task, agg in sorted(by_task.items()):
        n = agg["n"]
        out[task] = {
            "n": n,
            "valid_json_rate": agg["valid_json"] / n,
            "schema_valid_rate": agg["schema_valid"] / n,
            "score": agg["score_sum"] / n,
            "fields": {k: v / n for k, v in agg["fields"].items()},
        }
    return out
