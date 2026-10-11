"""System 2: slow, deliberate strategy from an OpenAI reasoning model."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .actions import Invest
from .state import Snapshot

INSTRUCTIONS = """\
You are the strategic commander (System 2) of a Terran StarCraft II bot.
A fast System 1 model makes tactical calls every ~2 game seconds within your plan;
deterministic code handles SCVs, supply depots, unit production, and micro.

Units available: Marine, Marauder. Investments available: expand, barracks, refinery,
techlab, reactor, engbay, stim, weapons1.

Read the game snapshot, the previous plan, and System 1's recent decisions, then return
an updated plan. Be concrete and short; System 1 sees only `summary` and the fields.
Plan around the enemy race and what has been scouted. Do not wait for perfect information.
"""


class Plan(BaseModel):
    summary: str = Field(description="One or two sentences System 1 will read every tick.")
    aggression: Literal["turtle", "balanced", "aggressive"]
    investment_priorities: list[Invest] = Field(
        description="Ordered: what System 1 should favour when choosing investments."
    )
    marauder_ratio: float = Field(ge=0, le=1, description="Share of production that is marauders.")
    attack_at_army_supply: int = Field(ge=0, description="Army supply at which to start attacking.")
    target_workers: int = Field(ge=12, le=80)
    reasoning: str = Field(description="Brief private reasoning, logged but not sent to System 1.")

    def for_system1(self) -> str:
        prios = ", ".join(p.value for p in self.investment_priorities[:4])
        return (
            f"{self.summary} Aggression: {self.aggression}. Priorities: {prios}. "
            f"Attack once army supply >= {self.attack_at_army_supply}."
        )


DEFAULT_PLAN = Plan(
    summary="Standard bio opener: two barracks, expand, stim, then pressure.",
    aggression="balanced",
    investment_priorities=[Invest.BARRACKS, Invest.REFINERY, Invest.EXPAND, Invest.TECHLAB, Invest.STIM],
    marauder_ratio=0.25,
    attack_at_army_supply=30,
    target_workers=44,
    reasoning="Default plan before System 2 has spoken.",
)


class Reasoner(Protocol):
    async def plan(self, snap: Snapshot, prev: Plan, recent: list[dict[str, Any]], why: str) -> Plan: ...


class OpenAIReasoner:
    def __init__(self, model: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI()
        self.model = model

    async def plan(self, snap: Snapshot, prev: Plan, recent: list[dict[str, Any]], why: str) -> Plan:
        payload = {
            "trigger": why,
            "snapshot": snap.to_dict(),
            "previous_plan": prev.model_dump(mode="json"),
            "recent_system1_decisions": recent[-10:],
        }
        resp = await self.client.responses.parse(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=json.dumps(payload),
            text_format=Plan,
        )
        if resp.output_parsed is None:
            return prev
        return resp.output_parsed


class OfflineReasoner:
    """No-API stand-in: adapts the default plan with simple rules."""

    async def plan(self, snap: Snapshot, prev: Plan, recent: list[dict[str, Any]], why: str) -> Plan:
        plan = DEFAULT_PLAN.model_copy()
        if snap.enemy_army_supply_est > snap.army_supply + 10:
            plan.aggression = "turtle"
            plan.attack_at_army_supply = snap.enemy_army_supply_est + 15
        plan.reasoning = f"offline rules ({why})"
        return plan
