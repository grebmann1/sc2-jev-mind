"""Bounded opening selection from comparable completed matches, not weight training."""

from __future__ import annotations

from .actions import Invest
from .system2 import DEFAULT_PLAN, Plan

POLICY_VERSION = "terran-bio-v2"
OPENINGS = ("balanced", "pressure", "macro")
MIN_TRIALS = 3


def opening_plan(name: str) -> Plan:
    if name not in OPENINGS:
        raise ValueError(f"Unknown opening: {name}")
    updates = {}
    if name == "pressure":
        updates = {
            "summary": "Build bio and stim, then apply early pressure with a smaller army.",
            "aggression": "aggressive",
            "attack_at_army_supply": 24,
            "marauder_ratio": 0.2,
        }
    elif name == "macro":
        updates = {
            "summary": "Grow the economy and build a larger bio army before attacking.",
            "target_workers": 66,
            "attack_at_army_supply": 42,
            "marauder_ratio": 0.3,
            "investment_priorities": [Invest.EXPAND, Invest.BARRACKS, Invest.TECHLAB, Invest.STIM],
        }
    return Plan.model_validate(
        {
            **DEFAULT_PLAN.model_dump(),
            **updates,
            "reasoning": f"Code-selected {name} initial profile; adapt using current observations.",
        }
    )


def choose_opening(stats: dict[str, dict], *, enabled: bool = True) -> tuple[str, str]:
    if not enabled:
        return "balanced", "automatic learning disabled"
    counts = {name: stats.get(name, {}).get("games", 0) for name in OPENINGS}
    total = sum(counts.values())
    for name in OPENINGS:
        if counts[name] < MIN_TRIALS:
            return name, f"exploring {name}: {counts[name]}/{MIN_TRIALS} comparable trials"
    if total % 5 == 0:
        name = min(OPENINGS, key=lambda n: counts[n])
        return name, "periodic exploration of the least sampled opening"

    def score(name: str) -> float:
        data = stats.get(name, {})
        return (data.get("wins", 0) + 0.5 * data.get("ties", 0) + 1) / (counts[name] + 2)

    name = max(OPENINGS, key=score)
    return name, "highest smoothed observed score; association, not proof of causation"
