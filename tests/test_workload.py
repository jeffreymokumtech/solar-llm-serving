import json

import jsonschema

from solarbench.workload import generator
from solarbench.workload.generator import DEFAULT_MIX, TASKS, agent_system_prompt, generate, load_tools


def test_generation_is_deterministic():
    a = [json.dumps(r, sort_keys=True) for r in generate(seed=1, n=40)]
    b = [json.dumps(r, sort_keys=True) for r in generate(seed=1, n=40)]
    assert a == b
    assert a != [json.dumps(r, sort_keys=True) for r in generate(seed=2, n=40)]


def test_every_task_class_appears_and_has_shape():
    reqs = list(generate(seed=3, n=300))
    seen = {r["task"] for r in reqs}
    assert seen == set(TASKS)
    for r in reqs:
        assert r["messages"][0]["role"] == "system" and r["messages"][-1]["role"] == "user"
        assert r["max_tokens"] > 0
        if r["schema"]:
            jsonschema.Draft202012Validator.check_schema(r["schema"])


def test_structured_ground_truth_validates_against_schema():
    for r in generate(seed=4, n=200):
        if r["task"] in ("ticket_triage", "fault_log_extraction"):
            exp = dict(r["expected"])
            if r["task"] == "ticket_triage":
                exp["summary"] = "x"
            jsonschema.validate(exp, r["schema"])


def test_agent_prompt_is_shared_prefix_and_names_only_real_tools():
    tools = load_tools()
    system = agent_system_prompt(tools)
    assert len(system) > 6000  # ~2k+ tokens of identical prefix across requests
    reqs = [r for r in generate(seed=5, n=200) if r["task"] == "agent_tool_turn"]
    assert all(r["messages"][0]["content"] == system for r in reqs)
    assert {r["expected"]["tool"] for r in reqs} <= set(tools)
    assert {t for _, t in generator.AGENT_QUESTIONS} <= set(tools)


def test_mix_weights_sum_to_one():
    assert abs(sum(DEFAULT_MIX.values()) - 1.0) < 1e-9


def test_fault_log_expected_matches_log_lines():
    r = next(x for x in generate(seed=6, n=50) if x["task"] == "fault_log_extraction")
    log = r["messages"][-1]["content"]
    for ev in r["expected"]["events"]:
        assert log.count(f"{ev['inverter']} ALARM {ev['code']}") == ev["count"]
