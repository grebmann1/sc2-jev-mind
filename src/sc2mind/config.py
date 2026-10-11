from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    jev_model: str = "jev-latest"
    openai_model: str = "gpt-5"
    # Game-time seconds between System 1 decisions.
    s1_interval_s: float = 2.0
    # Below this probability, System 1 keeps its previous decision.
    s1_min_confidence: float = 0.45
    # Jev's "replan" Noul above this wakes System 2 early.
    s1_replan_threshold: float = 0.7
    # System 2 cadence: a regular replan, plus a floor for early wake-ups.
    s2_interval_s: float = 45.0
    s2_min_interval_s: float = 15.0
    offline: bool = False

    @classmethod
    def from_env(cls, offline: bool = False) -> "Config":
        return cls(
            jev_model=os.environ.get("JEV_MODEL", cls.jev_model),
            openai_model=os.environ.get("OPENAI_MODEL", cls.openai_model),
            s1_interval_s=_f("S1_INTERVAL_S", cls.s1_interval_s),
            s1_min_confidence=_f("S1_MIN_CONFIDENCE", cls.s1_min_confidence),
            s1_replan_threshold=_f("S1_REPLAN_THRESHOLD", cls.s1_replan_threshold),
            s2_interval_s=_f("S2_INTERVAL_S", cls.s2_interval_s),
            s2_min_interval_s=_f("S2_MIN_INTERVAL_S", cls.s2_min_interval_s),
            offline=offline,
        )
