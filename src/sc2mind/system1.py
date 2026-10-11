"""System 1: fast, typed tactical judgments from TypeSafe Jev."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .actions import (
    INVEST_DESCRIPTIONS,
    STANCE_DESCRIPTIONS,
    Invest,
    Stance,
    baseline_investment,
    legal_investments,
    legal_stances,
)
from .state import Snapshot
from .system2 import Plan


@dataclass
class Decision:
    invest: Invest
    invest_p: float
    stance: Stance
    stance_p: float
    replan_p: float  # probability the situation has left the plan behind
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.invest, Invest) or not isinstance(self.stance, Stance):
            raise ValueError("decision must use Invest and Stance enums")
        for p in (self.invest_p, self.stance_p, self.replan_p):
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("decision probabilities must be finite and between 0 and 1")


class Reflex(Protocol):
    async def decide(self, snap: Snapshot, plan: Plan, memory: str = "") -> Decision: ...


def build_questions(snap: Snapshot):
    from typesafe_sdk import Choice, Noul

    invest = legal_investments(snap)
    stances = legal_stances(snap)
    questions = {
        "invest": Choice(
            instructions=(
                "Given the strategic plan and game state, which investment should we make next? "
                "Unaffordable options mean saving resources for them."
            ),
            criteria={a.value: INVEST_DESCRIPTIONS[a] for a in invest},
        ),
        "stance": Choice(
            instructions="What should our army do right now, given the plan and the threat?",
            criteria={s.value: STANCE_DESCRIPTIONS[s] for s in stances},
        ),
        "replan": Noul(
            instructions=(
                "Has the game diverged from the strategic plan enough (unexpected enemy units, "
                "a lost fight, a new threat) that the plan should be reconsidered now?"
            ),
        ),
    }
    return questions


class JevReflex:
    # A System 1 answer older than this is no longer fast enough to matter.
    TIMEOUT_S = 3.0

    def __init__(self, model: str, client=None, *, timeout_s: float = TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._owns_client = client is None
        if client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            client = AsyncTypeSafeClient(model=model)
        self.client = client

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def decide(self, snap: Snapshot, plan: Plan, memory: str = "") -> Decision:
        result = await self.client.system_one(
            state=snap.to_jev_state(plan.for_system1(), memory),
            questions=build_questions(snap),
            timeout=self.timeout_s,
        )
        inv, st = result.choices["invest"], result.choices["stance"]
        return Decision(
            invest=Invest(inv.choice),
            invest_p=inv.probabilities[inv.choice],
            stance=Stance(st.choice),
            stance_p=st.probabilities[st.choice],
            replan_p=result.nouls["replan"].noul,
            request_id=result.__dict__.get("_request_id"),  # the property raises when absent
        )


class OfflineReflex:
    """No-API stand-in: progressing bio build, stance from the plan's army target."""

    async def decide(self, snap: Snapshot, plan: Plan, memory: str = "") -> Decision:
        invest = baseline_investment(snap)
        stances = legal_stances(snap)
        if Stance.DEFEND in stances:
            stance = Stance.DEFEND
        elif Stance.ATTACK in stances and snap.army_supply >= plan.attack_at_army_supply:
            stance = Stance.ATTACK
        else:
            stance = Stance.RALLY
        return Decision(invest, 1.0, stance, 1.0, replan_p=0.0)
