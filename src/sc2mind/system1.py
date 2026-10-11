"""System 1: fast, typed tactical judgments from TypeSafe Jev."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .actions import (
    INVEST_DESCRIPTIONS,
    STANCE_DESCRIPTIONS,
    Invest,
    Stance,
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


class Reflex(Protocol):
    async def decide(self, snap: Snapshot, plan: Plan) -> Decision: ...


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

    def __init__(self, model: str, client=None) -> None:
        if client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            client = AsyncTypeSafeClient(model=model)
        self.client = client

    async def decide(self, snap: Snapshot, plan: Plan) -> Decision:
        result = await self.client.system_one(
            state=snap.to_jev_state(plan.for_system1()),
            questions=build_questions(snap),
            timeout=self.TIMEOUT_S,
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
    """No-API stand-in: first legal plan priority, stance from army size."""

    async def decide(self, snap: Snapshot, plan: Plan) -> Decision:
        legal = legal_investments(snap)
        invest = next((p for p in plan.investment_priorities if p in legal), Invest.NONE)
        stances = legal_stances(snap)
        if Stance.DEFEND in stances:
            stance = Stance.DEFEND
        elif snap.army_supply >= plan.attack_at_army_supply:
            stance = Stance.ATTACK
        else:
            stance = Stance.RALLY
        return Decision(invest, 1.0, stance, 1.0, replan_p=0.0)
