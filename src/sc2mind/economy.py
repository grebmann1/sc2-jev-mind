"""SC2-independent observations and the dedicated Jev base/worker brain."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .actions import (
    INVEST_DESCRIPTIONS,
    WORKER_DESCRIPTIONS,
    Invest,
    WorkerMode,
    baseline_worker_mode,
    legal_economy_priorities,
    legal_worker_modes,
)
from .state import Snapshot


@dataclass(frozen=True)
class MiningSite:
    tag: int
    assigned: int
    ideal: int


@dataclass(frozen=True)
class EconomyObservation:
    snap: Snapshot
    bases: tuple[MiningSite, ...]
    gas_sites: tuple[MiningSite, ...] = ()
    damaged_structures: tuple[int, ...] = ()
    protected_workers: int = 0
    idle_workers: int = 0
    plan: str = ""
    experience: str = ""

    @property
    def identity(self) -> tuple:
        # Policies apply to currently eligible workers, never saved individual tags.
        return tuple(s.tag for s in self.bases), tuple(s.tag for s in self.gas_sites)

    def jev_state(self) -> dict[str, str]:
        s = self.snap
        return {
            "role": "base_and_workers",
            "resources": f"minerals={s.minerals} gas={s.gas} supply={s.supply_used}/{s.supply_cap}",
            "workers": f"total={s.workers} idle={self.idle_workers} protected={self.protected_workers}",
            "mining_bases": "; ".join(f"{b.tag}: {b.assigned}/{b.ideal}" for b in self.bases)[:450],
            "gas_sites": "; ".join(f"{b.tag}: {b.assigned}/{b.ideal}" for b in self.gas_sites)[
                :350
            ],
            "growth": f"bases_pending={s.townhalls_pending} gas_pending={s.refineries_pending}",
            "maintenance": f"damaged_structures={len(self.damaged_structures)} threat={s.base_threatened}",
            "plan": self.plan[:400],
            "intelligence": str(s.intelligence)[:350],
            "experience_memory": self.experience[:650],
        }


@dataclass(frozen=True)
class EconomyDecision:
    mode: WorkerMode
    priority: Invest
    confidence: float
    request_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mode, WorkerMode)
            or self.priority not in (Invest.NONE, Invest.EXPAND, Invest.REFINERY)
            or not isinstance(self.priority, Invest)
        ):
            raise ValueError(
                "economy decisions must use typed worker modes and economic priorities"
            )
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("economy confidence must be between zero and one")


class EconomyReflex(Protocol):
    async def decide(self, observation: EconomyObservation) -> EconomyDecision: ...


def baseline_economy(o: EconomyObservation) -> EconomyDecision:
    priorities = legal_economy_priorities(o)
    priority = Invest.EXPAND if Invest.EXPAND in priorities else Invest.NONE
    if priority is Invest.NONE and Invest.REFINERY in priorities and o.snap.gas < 150:
        priority = Invest.REFINERY
    return EconomyDecision(baseline_worker_mode(o), priority, 1)


class OfflineEconomyReflex:
    async def decide(self, observation: EconomyObservation) -> EconomyDecision:
        return baseline_economy(observation)


class JevEconomyReflex:
    def __init__(self, model: str, *, timeout_s: float = 3, client=None) -> None:
        self._owns_client = client is None
        if client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            client = AsyncTypeSafeClient(model=model)
        self.client, self.timeout_s = client, timeout_s

    async def decide(self, observation: EconomyObservation) -> EconomyDecision:
        from typesafe_sdk import Choice

        result = await self.client.system_one(
            state=observation.jev_state(),
            questions={
                "workers": Choice(
                    instructions="Choose a worker allocation priority for the observed bases and plan.",
                    criteria={
                        m.value: WORKER_DESCRIPTIONS[m] for m in legal_worker_modes(observation)
                    },
                ),
                "growth": Choice(
                    instructions="Which available economic investment deserves priority?",
                    criteria={
                        a.value: INVEST_DESCRIPTIONS[a]
                        for a in legal_economy_priorities(observation)
                    },
                ),
            },
            timeout=self.timeout_s,
        )
        mode, growth = result.choices["workers"], result.choices["growth"]
        return EconomyDecision(
            WorkerMode(mode.choice),
            Invest(growth.choice),
            min(mode.probabilities[mode.choice], growth.probabilities[growth.choice]),
            result.__dict__.get("_request_id"),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
