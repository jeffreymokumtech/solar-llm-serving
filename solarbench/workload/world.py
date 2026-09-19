"""A small synthetic solar fleet. Every name, code and number here is invented;
the point is realistic *shape* (vocabulary, lengths, structure), not real data."""

from __future__ import annotations

import random
from dataclasses import dataclass

PLANT_NAMES = [
    "Kigali North PV",
    "Atacama Ridge Solar",
    "Thar Basin Park",
    "Ebro Valley PV",
    "Namib Coast Solar",
    "Anatolia East Park",
    "Gulf Mesa Solar",
    "Sahel Gate PV",
    "Cerrado Sul Park",
    "Karoo Flats Solar",
    "Punjab Link PV",
    "Sonoran Edge Park",
]
REGIONS = ["Region A", "Region B", "Region C", "Region D", "Region E"]
INVERTER_MODELS = ["SG250HX", "SUN2000-215KTL", "PVS-175-TL", "CPS-SCH275KTL"]
STRING_COUNTS = [12, 14, 16, 18, 20]

# code, name, severity, one-line meaning, typical cause, recommended action
ALARM_CODES = [
    (
        "A1203",
        "DC insulation resistance low",
        "major",
        "Insulation resistance on the DC side fell below the inverter's start threshold",
        "moisture ingress in a combiner box or a damaged string cable",
        "isolate the affected strings, megger-test cables, check combiner box seals",
    ),
    (
        "A1301",
        "AC grid over-voltage",
        "minor",
        "Grid voltage at the inverter terminals exceeded the configured limit",
        "weak grid or a transformer tap set too high",
        "review grid code settings, check transformer tap, log voltage profile",
    ),
    (
        "A1302",
        "AC grid under-frequency",
        "minor",
        "Grid frequency dropped below the ride-through band",
        "grid disturbance",
        "no action unless recurring; confirm ride-through parameters",
    ),
    (
        "A1410",
        "Inverter over-temperature derate",
        "major",
        "Internal heat-sink temperature triggered power derating",
        "blocked fans, dirty filters or failed cooling fan",
        "clean air filters, verify fan operation, check enclosure ventilation",
    ),
    ("A1411", "Fan failure", "major", "A cooling fan reported zero RPM", "fan bearing failure", "replace fan; expect derating until fixed"),
    (
        "A1520",
        "String current low",
        "warning",
        "One string is producing markedly less current than its siblings",
        "shading, soiling, a blown string fuse or a failed connector",
        "inspect fuse and connectors, compare with IV curve, check for soiling or shading",
    ),
    (
        "A1521",
        "String open circuit",
        "major",
        "A string reports zero current in daylight",
        "blown fuse, open connector or a disconnected string",
        "check fuse, reconnect and torque connectors, inspect for damage",
    ),
    (
        "A1530",
        "Arc fault detected",
        "critical",
        "The arc-fault detector tripped the DC side",
        "loose or corroded DC connector",
        "do not reset before inspection; locate and repair the arc source",
    ),
    (
        "A1601",
        "Communication loss",
        "warning",
        "The inverter stopped responding on the plant network",
        "switch port failure, cable damage or a power cycle",
        "check switch port and fibre, verify inverter is powered",
    ),
    (
        "A1602",
        "Data logger buffer overflow",
        "info",
        "The logger dropped samples because the upstream link was slow",
        "backhaul congestion",
        "check backhaul bandwidth; no plant impact",
    ),
    (
        "A1710",
        "Ground fault",
        "critical",
        "Residual current monitoring detected leakage to ground",
        "damaged module or cable insulation",
        "isolate, locate the leakage path with a fault locator, repair before reconnecting",
    ),
    (
        "A1720",
        "DC over-voltage",
        "major",
        "Open-circuit voltage exceeded the inverter's maximum DC input",
        "cold morning with high Voc or wrong string length",
        "verify string design against Voc at minimum site temperature",
    ),
    (
        "A1810",
        "Anti-islanding trip",
        "minor",
        "Inverter disconnected because it detected an island condition",
        "grid outage",
        "auto-reconnects when the grid returns",
    ),
    (
        "A1820",
        "Reactive power setpoint unreachable",
        "warning",
        "Plant controller requested more reactive power than available",
        "controller settings or inverters at capacity",
        "review Q/U curve and controller limits",
    ),
    ("A1901", "Tracker stow", "info", "Trackers moved to stow position", "high wind or snow forecast", "no action; production reduced during stow"),
    (
        "A1902",
        "Tracker motor fault",
        "major",
        "A tracker row failed to reach its target angle",
        "motor, gearbox or controller fault",
        "dispatch tracker technician; row stuck off-optimal",
    ),
    (
        "A2001",
        "Soiling ratio below threshold",
        "warning",
        "Estimated soiling losses exceed the cleaning trigger",
        "dust accumulation without rain",
        "schedule cleaning; compare against forecast rain",
    ),
    (
        "A2002",
        "Performance ratio drop",
        "warning",
        "Daily performance ratio fell more than 5 points below the fleet median",
        "soiling, shading, derating or curtailment",
        "check for open alarms, curtailment and soiling first",
    ),
    (
        "A2101",
        "Meter reading gap",
        "info",
        "Revenue meter readings missing for more than an hour",
        "meter communication outage",
        "check meter modem; backfill from inverter totals",
    ),
    (
        "A2201",
        "Battery cell temperature high",
        "critical",
        "A BESS rack reported a cell above the safe temperature band",
        "HVAC failure or cell fault",
        "reduce dispatch, inspect HVAC, isolate rack if rising",
    ),
]
ALARMS_BY_CODE = {a[0]: a for a in ALARM_CODES}

TRIAGE_PRIORITY = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
TRIAGE_CATEGORY = ["ELECTRICAL", "MECHANICAL", "COMMUNICATIONS", "PERFORMANCE", "SAFETY", "DATA"]
CODE_CATEGORY = {
    "A1203": "ELECTRICAL",
    "A1301": "ELECTRICAL",
    "A1302": "ELECTRICAL",
    "A1410": "MECHANICAL",
    "A1411": "MECHANICAL",
    "A1520": "ELECTRICAL",
    "A1521": "ELECTRICAL",
    "A1530": "SAFETY",
    "A1601": "COMMUNICATIONS",
    "A1602": "DATA",
    "A1710": "SAFETY",
    "A1720": "ELECTRICAL",
    "A1810": "ELECTRICAL",
    "A1820": "PERFORMANCE",
    "A1901": "MECHANICAL",
    "A1902": "MECHANICAL",
    "A2001": "PERFORMANCE",
    "A2002": "PERFORMANCE",
    "A2101": "DATA",
    "A2201": "SAFETY",
}
SEVERITY_PRIORITY = {"info": "LOW", "warning": "MEDIUM", "minor": "MEDIUM", "major": "HIGH", "critical": "CRITICAL"}
SITE_VISIT_CODES = {"A1203", "A1411", "A1521", "A1530", "A1710", "A1902", "A2201"}


@dataclass(frozen=True)
class Plant:
    plant_id: str
    name: str
    region: str
    capacity_mw: float
    inverter_model: str
    inverter_count: int
    strings_per_inverter: int

    @property
    def inverters(self) -> list[str]:
        return [f"INV-{i:02d}" for i in range(1, self.inverter_count + 1)]


def make_fleet(rng: random.Random) -> list[Plant]:
    fleet = []
    for i, name in enumerate(PLANT_NAMES):
        slug = name.lower().replace(" ", "-")
        count = rng.choice([8, 12, 16, 20, 24])
        fleet.append(
            Plant(
                plant_id=f"plt-{i + 1:03d}-{slug}",
                name=name,
                region=rng.choice(REGIONS),
                capacity_mw=round(count * 0.25 * rng.uniform(0.8, 1.2), 1),
                inverter_model=rng.choice(INVERTER_MODELS),
                inverter_count=count,
                strings_per_inverter=rng.choice(STRING_COUNTS),
            )
        )
    return fleet
