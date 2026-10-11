from __future__ import annotations

import math
import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


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
    # Wall-clock budgets, distinct from game-time cadence and freshness.
    s1_timeout_s: float = 3.0
    s2_timeout_s: float = 30.0
    s1_max_age_s: float = 8.0
    s2_max_age_s: float = 120.0
    squad_interval_s: float = 3.0
    squad_max_age_s: float = 6.0
    multi_squad: bool = True
    offline: bool = False

    def __post_init__(self) -> None:
        for name in (
            "squad_interval_s",
            "squad_max_age_s",
            "s1_interval_s",
            "s2_interval_s",
            "s2_min_interval_s",
            "s1_timeout_s",
            "s2_timeout_s",
            "s1_max_age_s",
            "s2_max_age_s",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        for name in ("s1_min_confidence", "s1_replan_threshold"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.s2_min_interval_s > self.s2_interval_s:
            raise ValueError("s2_min_interval_s cannot exceed s2_interval_s")
        if not self.jev_model.strip() or not self.openai_model.strip():
            raise ValueError("model names cannot be empty")

    @classmethod
    def from_env(cls, offline: bool = False) -> Config:
        return cls(
            jev_model=os.environ.get("JEV_MODEL", cls.jev_model),
            openai_model=os.environ.get("OPENAI_MODEL", cls.openai_model),
            s1_interval_s=_f("S1_INTERVAL_S", cls.s1_interval_s),
            s1_min_confidence=_f("S1_MIN_CONFIDENCE", cls.s1_min_confidence),
            s1_replan_threshold=_f("S1_REPLAN_THRESHOLD", cls.s1_replan_threshold),
            s2_interval_s=_f("S2_INTERVAL_S", cls.s2_interval_s),
            s2_min_interval_s=_f("S2_MIN_INTERVAL_S", cls.s2_min_interval_s),
            s1_timeout_s=_f("S1_TIMEOUT_S", cls.s1_timeout_s),
            s2_timeout_s=_f("S2_TIMEOUT_S", cls.s2_timeout_s),
            s1_max_age_s=_f("S1_MAX_AGE_S", cls.s1_max_age_s),
            s2_max_age_s=_f("S2_MAX_AGE_S", cls.s2_max_age_s),
            squad_interval_s=_f("SQUAD_INTERVAL_S", cls.squad_interval_s),
            squad_max_age_s=_f("SQUAD_MAX_AGE_S", cls.squad_max_age_s),
            multi_squad=_bool("SC2MIND_MULTI_SQUAD", cls.multi_squad),
            offline=offline,
        )
