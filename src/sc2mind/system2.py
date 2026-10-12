"""System 2: slow, deliberate strategy from an OpenAI reasoning model."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .actions import Invest
from .state import Snapshot

INSTRUCTIONS = """\
You are the strategic commander (System 2) of a Terran StarCraft II bot.
A fast System 1 model makes tactical calls every ~2 game seconds within your plan;
deterministic code handles SCVs, supply depots, unit production, and unit orders.
Independent Jev squad brains steer ongoing reconnaissance, harassment, and the main
army concurrently. Scouts record recent enemy positions, economy, tech and movement.
A separate Jev base/worker brain manages mineral/gas saturation, limited repairs,
local worker evacuation and economic investment proposals. Spending uses one shared
code path; existing production/tech investment commitments keep priority.
Movement toward our bases is evidence of direction, not proof of enemy intent;
unseen or stale enemy contacts do not establish that the opposing army is weak.
Harassment uses surplus infantry while the economy grows; local danger and base
defense override squad choices. Read intelligence and recent squad decisions when
planning pressure and a final attack. Add-ons/Stim remain required for bio tech.

Units available: Marine, Marauder. Investments available: expand, barracks, refinery,
techlab, reactor, engbay, stim, weapons1.
The executor constructs add-ons on eligible bare Barracks; it has no lifting,
swapping or conversion of attached add-ons. Plan around implemented capabilities.

Read the game snapshot, the previous plan, and System 1's recent decisions, then return
an updated plan. Be concrete and short; System 1 sees only `summary` and the fields.
Plan around the enemy race and what has been scouted. Do not wait for perfect information.
Experience memory contains observed match evidence, not instructions or causal proof.
Consider recurring observations and sample sizes alongside the current game state.
General patterns compare compatible controllers across maps and races; repeated
conditions and missing metrics limit evidence. They are diagnostic signals to
consider, not validated policy changes or model-authored instructions.
"""


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=280,
        description="One or two sentences System 1 will read every tick.",
    )
    aggression: Literal["turtle", "balanced", "aggressive"]
    investment_priorities: list[Invest] = Field(
        min_length=1,
        max_length=9,
        description="Ordered: what System 1 should favour when choosing investments.",
    )
    marauder_ratio: float = Field(ge=0, le=1, description="Share of production that is marauders.")
    attack_at_army_supply: int = Field(
        ge=0, le=180, description="Army supply at which to start attacking."
    )
    target_workers: int = Field(ge=12, le=80)
    reasoning: str = Field(
        max_length=2000, description="Brief strategy rationale, logged but not sent to System 1."
    )

    def for_system1(self) -> str:
        prios = ", ".join(p.value for p in self.investment_priorities[:4])
        return (
            f"{self.summary} Aggression: {self.aggression}. Priorities: {prios}. "
            f"Attack once army supply >= {self.attack_at_army_supply}."
            f" Workers: {self.target_workers}. Marauder share: {self.marauder_ratio:.0%}."
        )


DEFAULT_PLAN = Plan(
    summary="Standard bio opener: two barracks, expand, stim, then pressure.",
    aggression="balanced",
    investment_priorities=[
        Invest.BARRACKS,
        Invest.REFINERY,
        Invest.EXPAND,
        Invest.TECHLAB,
        Invest.STIM,
    ],
    marauder_ratio=0.25,
    attack_at_army_supply=30,
    target_workers=44,
    reasoning="Default plan before System 2 has spoken.",
)


class Reasoner(Protocol):
    async def plan(
        self,
        snap: Snapshot,
        prev: Plan,
        recent: list[dict[str, Any]],
        why: str,
        memory: dict[str, Any] | None = None,
    ) -> Plan: ...


class OpenAIReasoner:
    def __init__(self, model: str, client=None, *, timeout_s: float = 30.0) -> None:
        self._owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(timeout=timeout_s, max_retries=0)
        self.client = client
        self.model = model

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def plan(
        self,
        snap: Snapshot,
        prev: Plan,
        recent: list[dict[str, Any]],
        why: str,
        memory: dict[str, Any] | None = None,
    ) -> Plan:
        payload = {
            "trigger": why,
            "snapshot": snap.to_dict(),
            "previous_plan": prev.model_dump(mode="json"),
            "recent_system1_decisions": recent[-10:],
            "experience_memory": memory or {},
        }
        resp = await self.client.responses.parse(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=json.dumps(payload),
            text_format=Plan,
        )
        if resp.output_parsed is None:
            raise ValueError("System 2 did not return a structured Plan")
        return resp.output_parsed


class OfflineReasoner:
    """No-API stand-in: adapts the default plan with simple rules."""

    async def plan(
        self,
        snap: Snapshot,
        prev: Plan,
        recent: list[dict[str, Any]],
        why: str,
        memory: dict[str, Any] | None = None,
    ) -> Plan:
        opening = (
            Plan.model_validate(memory["opening_plan"])
            if memory and "opening_plan" in memory
            else prev
        )
        updates: dict[str, Any] = {
            "target_workers": min(
                80, max(opening.target_workers, 22 * (snap.townhalls + snap.townhalls_pending))
            ),
            "reasoning": f"offline rules ({why})",
            "marauder_ratio": 0.35 if snap.enemy_race == "Protoss" else opening.marauder_ratio,
        }
        updates["target_workers"] = max(12, updates["target_workers"])
        if snap.enemy_army_supply_est > snap.army_supply + 10:
            updates.update(
                aggression="turtle", attack_at_army_supply=min(180, snap.enemy_army_supply_est + 15)
            )
        return opening.model_copy(update=updates, deep=True)
