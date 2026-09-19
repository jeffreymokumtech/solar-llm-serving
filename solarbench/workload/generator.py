"""Generates the five solar O&M task classes as OpenAI-style chat requests.

Every request is a dict:
    id, task, messages, schema (JSON schema or None), max_tokens, expected (ground truth or None), meta
The generator is deterministic for a given seed, so datasets are reproducible
and can be committed. Lengths are controlled in characters (roughly 4 chars per
token for English + JSON); real token counts come back from the server's usage
field at run time.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import schemas
from .world import (
    ALARM_CODES,
    ALARMS_BY_CODE,
    CODE_CATEGORY,
    SEVERITY_PRIORITY,
    SITE_VISIT_CODES,
    Plant,
    make_fleet,
)

TASKS = ["alert_narration", "ticket_triage", "fault_log_extraction", "daily_report_summary", "agent_tool_turn"]
DEFAULT_MIX = {
    "alert_narration": 0.30,
    "ticket_triage": 0.25,
    "fault_log_extraction": 0.15,
    "daily_report_summary": 0.10,
    "agent_tool_turn": 0.20,
}
TOOLS_PATH = Path(__file__).with_name("nuravolt_tools.json")
T0 = datetime(2026, 6, 1, 5, 30)

SYSTEM_OPS = (
    "You are the operations assistant for a fleet of utility-scale solar plants. "
    "You write for site technicians and asset managers: concrete, short, no marketing language. "
    "Never invent measurements that are not in the input."
)


def _ts(rng: random.Random, day: int, hour_lo: int = 6, hour_hi: int = 19) -> datetime:
    return T0 + timedelta(days=day, hours=rng.randint(hour_lo, hour_hi) - 5, minutes=rng.randint(0, 59))


# ----------------------------------------------------------------------------- alert_narration
def gen_alert_narration(rng: random.Random, plant: Plant, i: int) -> dict[str, Any]:
    code, name, sev, meaning, _cause, _action = rng.choice(ALARM_CODES)
    inv = rng.choice(plant.inverters)
    alarm = {
        "plant": plant.name,
        "inverter": inv,
        "inverter_model": plant.inverter_model,
        "code": code,
        "name": name,
        "severity": sev,
        "raised_at": _ts(rng, rng.randint(0, 30)).isoformat(timespec="minutes"),
        "ac_power_kw": round(rng.uniform(0, 250), 1),
        "dc_voltage_v": round(rng.uniform(600, 1450), 0),
        "heatsink_temp_c": round(rng.uniform(35, 92), 1),
        "repeat_count_24h": rng.choice([1, 1, 1, 2, 3, 7, 15]),
    }
    return {
        "id": f"alert_narration-{i:04d}",
        "task": "alert_narration",
        "messages": [
            {"role": "system", "content": SYSTEM_OPS},
            {
                "role": "user",
                "content": "Explain this alarm to the on-site technician in two or three sentences: what it means, "
                "the most likely cause, and the first thing to check.\n\n" + json.dumps(alarm, indent=2),
            },
        ],
        "schema": None,
        "max_tokens": 120,
        "expected": {"code": code, "meaning": meaning},
        "meta": {"plant_id": plant.plant_id, "inverter": inv},
    }


# ----------------------------------------------------------------------------- ticket_triage
NOTE_TEMPLATES = [
    "Got a call from the site. {inv_list} on {plant} {verb} {name} ({code}) since {when}. {extra}",
    "Morning check: {plant}, {inv_list} showing {code} {name}. {extra} Please advise.",
    "{plant} - {inv_list}: repeated {name} alarms ({code}) overnight, {extra}",
    "Operator note ({plant}): {inv_list} {verb} {code}. {extra} Crew availability tomorrow afternoon.",
]
EXTRAS = [
    "Production on the affected units is down about {pct}%.",
    "No visible damage from the camera. Weather clear.",
    "It rained heavily yesterday evening.",
    "Same inverters had this last month.",
    "Grid operator confirmed a disturbance at that time.",
    "Ambient temperature reached {temp} C at 14:00.",
]


def gen_ticket_triage(rng: random.Random, plant: Plant, i: int) -> dict[str, Any]:
    code, name, sev, *_ = rng.choice(ALARM_CODES)
    n_inv = rng.choice([1, 1, 1, 2, 2, 3])
    invs = sorted(rng.sample(plant.inverters, n_inv))
    extra = rng.choice(EXTRAS).format(pct=rng.randint(5, 60), temp=rng.randint(30, 44))
    note = rng.choice(NOTE_TEMPLATES).format(
        inv_list=", ".join(invs),
        plant=plant.name,
        verb=rng.choice(["is showing", "has been raising", "tripped on"]),
        name=name,
        code=code,
        when=rng.choice(["yesterday", "this morning", "06:40", "the weekend"]),
        extra=extra,
    )
    context = {
        "plant": {"name": plant.name, "capacity_mw": plant.capacity_mw, "inverter_model": plant.inverter_model},
        "open_tickets": rng.randint(0, 6),
        "alarm_reference": {"code": code, "name": name, "severity": sev},
    }
    expected = {
        "priority": SEVERITY_PRIORITY[sev],
        "category": CODE_CATEGORY[code],
        "affected_inverters": invs,
        "needs_site_visit": code in SITE_VISIT_CODES,
    }
    return {
        "id": f"ticket_triage-{i:04d}",
        "task": "ticket_triage",
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_OPS + " Triage operator notes into tickets. Priority follows alarm severity "
                "(info=LOW, warning/minor=MEDIUM, major=HIGH, critical=CRITICAL). A site visit is needed for "
                "insulation, fan, open-string, arc, ground, tracker-motor and battery-temperature faults. "
                "Answer with JSON only.",
            },
            {"role": "user", "content": f"Operator note:\n{note}\n\nContext:\n{json.dumps(context, indent=2)}"},
        ],
        "schema": schemas.TICKET_TRIAGE,
        "max_tokens": 160,
        "expected": expected,
        "meta": {"plant_id": plant.plant_id, "code": code},
    }


# ----------------------------------------------------------------------------- fault_log_extraction
def gen_fault_log_extraction(rng: random.Random, plant: Plant, i: int) -> dict[str, Any]:
    n_lines = rng.randint(90, 280)
    day = rng.randint(0, 30)
    hot = rng.sample(plant.inverters, rng.randint(2, 5))
    hot_codes = rng.sample([a[0] for a in ALARM_CODES], rng.randint(2, 4))
    lines: list[tuple[datetime, str]] = []
    counts: dict[tuple[str, str], list[Any]] = {}
    for _ in range(n_lines):
        t = _ts(rng, day)
        if rng.random() < 0.7:
            inv, code = rng.choice(hot), rng.choice(hot_codes)
            key = (inv, code)
            entry = counts.setdefault(key, [0, t])
            entry[0] += 1
            entry[1] = min(entry[1], t)
            lines.append((t, f"{t.isoformat(timespec='seconds')} {inv} ALARM {code} {ALARMS_BY_CODE[code][1]} state=RAISED"))
        else:
            inv = rng.choice(plant.inverters)
            kind = rng.choice(["INFO heartbeat ok", "INFO setpoint P=100% Q=0", "INFO curtailment cleared", "INFO logger sync"])
            lines.append((t, f"{t.isoformat(timespec='seconds')} {inv} {kind}"))
    lines.sort()
    log = "\n".join(line for _, line in lines)
    expected = {
        "events": sorted(
            [{"inverter": inv, "code": code, "count": c, "first_seen": t.isoformat(timespec="seconds")} for (inv, code), (c, t) in counts.items()],
            key=lambda e: (e["inverter"], e["code"]),
        )
    }
    return {
        "id": f"fault_log_extraction-{i:04d}",
        "task": "fault_log_extraction",
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_OPS + " Extract every distinct (inverter, alarm code) pair with state=RAISED from the "
                "log, with how many times it occurred and the timestamp of the first occurrence. Ignore INFO lines. "
                "Answer with JSON only.",
            },
            {"role": "user", "content": f"Inverter event log for {plant.name}:\n\n{log}"},
        ],
        "schema": schemas.FAULT_LOG_EXTRACTION,
        "max_tokens": 700,
        "expected": expected,
        "meta": {"plant_id": plant.plant_id, "log_lines": n_lines},
    }


# ----------------------------------------------------------------------------- daily_report_summary
def gen_daily_report_summary(rng: random.Random, plant: Plant, i: int) -> dict[str, Any]:
    day = rng.randint(0, 30)
    date = (T0 + timedelta(days=day)).date().isoformat()
    ghi = round(rng.uniform(3.5, 8.2), 2)
    rows = []
    for inv in plant.inverters:
        exp = plant.capacity_mw / plant.inverter_count * ghi * 1000 * rng.uniform(0.78, 0.86)
        act = exp * rng.choice([1, 1, 1, 1, 0.97, 0.9, 0.6, 0.0])
        rows.append(f"| {inv} | {act:,.0f} | {exp:,.0f} | {act / exp * 100 if exp else 0:.1f} | {rng.uniform(0, 12):.1f} |")
    events = "\n".join(
        f"- {_ts(rng, day).strftime('%H:%M')} {rng.choice(plant.inverters)} {c} {ALARMS_BY_CODE[c][1]}"
        for c in rng.sample([a[0] for a in ALARM_CODES], rng.randint(2, 6))
    )
    report = (
        f"# Daily production report: {plant.name} ({plant.capacity_mw} MWp), {date}\n\n"
        f"Weather: GHI {ghi} kWh/m2, ambient max {rng.randint(24, 43)} C, wind avg {rng.uniform(1, 9):.1f} m/s, "
        f"rain {rng.choice([0, 0, 0, 2.5, 11.0])} mm.\n"
        f"Plant availability {rng.uniform(93, 100):.1f}%. Grid curtailment {rng.choice([0, 0, 45, 120])} minutes.\n"
        f"Soiling ratio estimate {rng.uniform(0.90, 0.995):.3f}. Performance ratio {rng.uniform(0.70, 0.86):.3f}.\n\n"
        "| Inverter | Energy kWh | Expected kWh | Ratio % | Max derate % |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        f"Events:\n{events}\n"
    )
    return {
        "id": f"daily_report_summary-{i:04d}",
        "task": "daily_report_summary",
        "messages": [
            {"role": "system", "content": SYSTEM_OPS},
            {
                "role": "user",
                "content": "Write the asset manager's morning summary of this report in 250 to 350 words: overall "
                "production vs expectation, the underperforming inverters and the most likely reasons, the events "
                "that need follow-up, and one recommended action for today.\n\n" + report,
            },
        ],
        "schema": None,
        "max_tokens": 450,
        "expected": None,
        "meta": {"plant_id": plant.plant_id},
    }


# ----------------------------------------------------------------------------- agent_tool_turn
AGENT_QUESTIONS: list[tuple[str, str]] = [
    ("How dirty are the panels at {plant} right now and when should we clean?", "nuravolt_get_soiling_forecast"),
    ("Show me the 30 day soiling forecast for {plant}.", "nuravolt_get_soiling_forecast"),
    ("List the inverters at {plant}.", "nuravolt_list_inverters"),
    ("Which plants can I see?", "nuravolt_list_plants"),
    ("What did the battery at {plant} earn last week?", "nuravolt_get_bess_revenue"),
    ("Diagnose {inv} at {plant}, it has been underperforming.", "nuravolt_diagnose_inverter"),
    ("Is {inv} at {plant} classified as soiled or faulty?", "nuravolt_get_inverter_classification"),
    ("Plot the AC power of {inv} at {plant} for the last 7 days.", "nuravolt_get_chart"),
    ("How good is the irradiance data at {plant}?", "nuravolt_get_irradiance_quality"),
    ("Where do we stand on the battery warranty at {plant}?", "nuravolt_get_warranty_position"),
    ("Audit the battery optimizer at {plant}.", "nuravolt_get_optimizer_audit"),
    ("Show open tickets for {plant}.", "nuravolt_list_tickets"),
    ("What does the manual say about {code} on a {model}?", "nuravolt_search_knowledge_base"),
    ("Open a HIGH priority ticket on {plant} {inv} for the string fault.", "nuravolt_create_ticket"),
    ("Mark ticket t-{n} as done.", "nuravolt_update_ticket_status"),
    ("Comment on ticket t-{n} that the crew is booked for Monday.", "nuravolt_comment_on_ticket"),
    ("Email me the weekly report for {plant} every Monday.", "nuravolt_schedule_report"),
]


def load_tools() -> dict[str, Any]:
    return json.loads(TOOLS_PATH.read_text())


def agent_system_prompt(tools: dict[str, Any]) -> str:
    """One long, identical system prompt for every agent turn: this is what makes
    the task a prefix-caching benchmark (about 2.5k tokens shared)."""
    lines = [
        "You are Shams, the planning agent of a solar and storage operations platform. You do not answer the user "
        "directly; you pick exactly one tool call that makes progress on the request, with arguments taken from the "
        "request or left for later binding. Never call a write tool for a question. Prefer resolving ids with "
        "nuravolt_list_plants before calling plant-specific tools if the plant id is not known.",
        "",
        "Tools (name, description, JSON parameter schema):",
    ]
    for name, spec in tools.items():
        lines.append(f"- {name}{' [WRITE, requires approval]' if spec.get('is_write') else ''}: {spec['description']}")
        lines.append("  parameters: " + json.dumps({"properties": spec["properties"], "required": spec.get("required", [])}))
    lines += [
        "",
        "Operating rules:",
        "1. Plant ids are opaque slugs returned by nuravolt_list_plants; never invent one from a plant's display name.",
        "2. Inverter ids look like INV-07 and are only valid for the plant they were listed under.",
        "3. Soiling questions (dirty, dust, cleaning, wash) map to nuravolt_get_soiling_forecast; classification questions",
        "   (soiled vs faulty) map to nuravolt_get_inverter_classification; underperformance with no cause named maps to",
        "   nuravolt_diagnose_inverter.",
        "4. Battery revenue, earnings and arbitrage questions map to nuravolt_get_bess_revenue; warranty state of health and",
        "   throughput limits map to nuravolt_get_warranty_position; dispatch quality maps to nuravolt_get_optimizer_audit.",
        "5. Requests for a plot, chart, trend or time series map to nuravolt_get_chart with a metric and a range.",
        "6. Questions about what a manual, datasheet or procedure says map to nuravolt_search_knowledge_base.",
        "7. Creating, closing, updating or commenting on tickets and scheduling reports are WRITE tools. A write is only",
        "   selected when the user explicitly asks for the change; a question about tickets uses nuravolt_list_tickets.",
        "8. Every write tool needs an idempotency_key; leave it empty, the executor fills it in.",
        "9. If the request is outside operations (poetry, general coding, chit-chat) select nuravolt_list_plants with no",
        "   arguments so the planner can respond that the request is out of scope.",
        "10. Priorities are LOW, MEDIUM, HIGH or CRITICAL. Ticket statuses are NEW, VALIDATED, ASSIGNED, IN_PROGRESS, DONE.",
        "11. Report schedules are daily, weekly or monthly; weekly schedules need a dayOfWeek.",
        "12. Never combine two tools in one answer; the planner calls you again for the next step.",
        "",
        "Worked examples:",
        'User: "How dirty are the panels at Ebro Valley PV right now?"',
        'Answer: {"tool": "nuravolt_get_soiling_forecast", "args": {"plantId": "<from list_plants>", "days": 30}}',
        'User: "Plot the DC voltage of INV-03 at Thar Basin Park for the last week."',
        'Answer: {"tool": "nuravolt_get_chart", "args": {"plantId": "<from list_plants>", "inverterId": "INV-03", "metric": "dc_voltage", "range": "7d"}}',  # noqa: E501
        'User: "Close ticket t-41, the fan was replaced."',
        'Answer: {"tool": "nuravolt_update_ticket_status", "args": {"idempotency_key": "", "ticketId": "t-41", "newStatus": "DONE", "reason": "fan replaced"}}',  # noqa: E501
        'User: "Email me the monthly report for Gulf Mesa Solar."',
        'Answer: {"tool": "nuravolt_schedule_report", "args": {"idempotency_key": "", "name": "Gulf Mesa monthly", "plantId": "<from list_plants>", "schedule": "monthly", "recipient_emails": ["<user>"]}}',  # noqa: E501
        "",
        'Answer with JSON only: {"tool": <tool name>, "args": {<arguments>}}.',
    ]
    return "\n".join(lines)


def gen_agent_tool_turn(rng: random.Random, plant: Plant, i: int, tools: dict[str, Any], system: str) -> dict[str, Any]:
    q, tool = rng.choice(AGENT_QUESTIONS)
    code = rng.choice(ALARM_CODES)[0]
    question = q.format(plant=plant.name, inv=rng.choice(plant.inverters), code=code, model=plant.inverter_model, n=rng.randint(1, 99))
    return {
        "id": f"agent_tool_turn-{i:04d}",
        "task": "agent_tool_turn",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
        "schema": schemas.agent_tool_turn(list(tools)),
        "max_tokens": 120,
        "expected": {"tool": tool},
        "meta": {"plant_id": plant.plant_id, "shared_prefix_chars": len(system)},
    }


# ----------------------------------------------------------------------------- driver
def generate(seed: int = 42, n: int = 200, mix: dict[str, float] | None = None) -> Iterator[dict[str, Any]]:
    """Yield `n` requests drawn from `mix` (defaults to DEFAULT_MIX), deterministic in `seed`."""
    rng = random.Random(seed)
    fleet = make_fleet(rng)
    mix = mix or DEFAULT_MIX
    tools = load_tools()
    system = agent_system_prompt(tools)
    names = list(mix)
    weights = [mix[k] for k in names]
    counters = dict.fromkeys(names, 0)
    for _ in range(n):
        task = rng.choices(names, weights)[0]
        plant = rng.choice(fleet)
        i = counters[task]
        counters[task] += 1
        if task == "alert_narration":
            yield gen_alert_narration(rng, plant, i)
        elif task == "ticket_triage":
            yield gen_ticket_triage(rng, plant, i)
        elif task == "fault_log_extraction":
            yield gen_fault_log_extraction(rng, plant, i)
        elif task == "daily_report_summary":
            yield gen_daily_report_summary(rng, plant, i)
        elif task == "agent_tool_turn":
            yield gen_agent_tool_turn(rng, plant, i, tools, system)
        else:
            raise ValueError(f"unknown task {task}")


def write_dataset(path: Path, seed: int = 42, n: int = 200, mix: dict[str, float] | None = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as f:
        for req in generate(seed, n, mix):
            f.write(json.dumps(req, sort_keys=True) + "\n")
            count += 1
    return count


def read_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]
