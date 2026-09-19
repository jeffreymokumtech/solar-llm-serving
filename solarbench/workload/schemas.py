"""JSON schemas for the structured tasks. Passed to the server as
`response_format={"type": "json_schema", ...}` (guided decoding) and used by the
quality evaluator to validate and score outputs."""

from __future__ import annotations

from .world import TRIAGE_CATEGORY, TRIAGE_PRIORITY

TICKET_TRIAGE = {
    "type": "object",
    "properties": {
        "priority": {"type": "string", "enum": TRIAGE_PRIORITY},
        "category": {"type": "string", "enum": TRIAGE_CATEGORY},
        "affected_inverters": {"type": "array", "items": {"type": "string", "pattern": "^INV-[0-9]{2}$"}},
        "needs_site_visit": {"type": "boolean"},
        "summary": {"type": "string", "maxLength": 200},
    },
    "required": ["priority", "category", "affected_inverters", "needs_site_visit", "summary"],
    "additionalProperties": False,
}

FAULT_LOG_EXTRACTION = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inverter": {"type": "string", "pattern": "^INV-[0-9]{2}$"},
                    "code": {"type": "string", "pattern": "^A[0-9]{4}$"},
                    "count": {"type": "integer", "minimum": 1},
                    "first_seen": {"type": "string"},
                },
                "required": ["inverter", "code", "count", "first_seen"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


def agent_tool_turn(tool_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": tool_names},
            "args": {"type": "object"},
        },
        "required": ["tool", "args"],
        "additionalProperties": False,
    }
