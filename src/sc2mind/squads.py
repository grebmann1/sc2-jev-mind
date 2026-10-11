"""SC2-independent squad observations and independent System 1 clients."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .actions import (
    MICRO_DESCRIPTIONS,
    Focus,
    MicroMode,
    Role,
    baseline_squad_mode,
    legal_squad_modes,
    squad_sizes,
)


@dataclass(frozen=True)
class SquadObservation:
    role: Role
    tags: tuple[int, ...]
    time_s: float
    supply: float
    health: float
    enemy_power: float
    targets: tuple[Focus, ...]
    goal: str
    advancing: bool
    base_threatened: bool = False
    away_from_home: bool = False
    unseen_threat: bool = False
    enemy_summary: str = "none"
    intelligence: str = "No recent sightings; enemy strength unknown."
    plan: str = ""
    goal_distance: float = 0
    experience: str = ""

    @property
    def identity(self) -> tuple:
        # A response must belong to this exact assignment and mission.
        return self.tags, self.goal, self.advancing

    def jev_state(self) -> dict[str, str]:
        return {
            "role": self.role.value,
            "squad": f"units={len(self.tags)} supply={self.supply:g} health={self.health:.0%}",
            "mission": f"{self.goal[:130]} distance={self.goal_distance:.1f}",
            "nearby_enemy": f"power={self.enemy_power:g} {self.enemy_summary}"[:240],
            "safety": (
                f"base_threat={self.base_threatened} away={self.away_from_home} "
                f"undetected_threat={self.unseen_threat}"
            ),
            "intelligence": self.intelligence[:400],
            "plan": self.plan[:400],
            "experience_memory": self.experience[:650],
        }


@dataclass(frozen=True)
class SquadDecision:
    mode: MicroMode
    confidence: float
    focus: Focus = Focus.ANY
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, MicroMode) or not isinstance(self.focus, Focus):
            raise ValueError("squad decisions must use typed modes and focus")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("squad confidence must be between 0 and 1")


class SquadReflex(Protocol):
    async def decide(self, observation: SquadObservation) -> SquadDecision: ...


class JevSquadReflex:
    """One owned client per role, with no resource or other-squad authority."""

    def __init__(self, model: str, *, timeout_s: float = 3, client=None) -> None:
        self._owns_client = client is None
        if client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            client = AsyncTypeSafeClient(model=model)
        self.client, self.timeout_s = client, timeout_s

    async def decide(self, observation: SquadObservation) -> SquadDecision:
        from typesafe_sdk import Choice

        result = await self.client.system_one(
            state=observation.jev_state(),
            questions={
                "micro": Choice(
                    instructions="Choose the next maneuver for this squad's mission and local contact.",
                    criteria={
                        mode.value: MICRO_DESCRIPTIONS[mode]
                        for mode in legal_squad_modes(observation)
                    },
                ),
                "focus": Choice(
                    instructions="Which available local target class deserves this squad's attention?",
                    criteria={f.value: f.value for f in (Focus.ANY, *observation.targets)},
                ),
            },
            timeout=self.timeout_s,
        )
        mode, focus = result.choices["micro"], result.choices["focus"]
        return SquadDecision(
            MicroMode(mode.choice),
            min(mode.probabilities[mode.choice], focus.probabilities[focus.choice]),
            Focus(focus.choice),
            result.__dict__.get("_request_id"),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class OfflineSquadReflex:
    async def decide(self, observation: SquadObservation) -> SquadDecision:
        focus = Focus.WORKERS if observation.role is Role.HARASS else Focus.THREATS
        if focus not in observation.targets:
            focus = Focus.ANY
        return SquadDecision(baseline_squad_mode(observation), 1, focus)


class SquadRoster:
    """Keep tag ownership stable, exclusive, and bounded as units die or arrive."""

    def __init__(self) -> None:
        self.members: dict[Role, tuple[int, ...]] = {role: () for role in Role}

    def assign(self, marines: list[int], marauders: list[int], workers: list[int], snap) -> dict:
        scout_count, harass_count = squad_sizes(snap, len(marines))
        available = set(marines)
        scout_pool = available if scout_count else set(workers)
        if snap.base_threatened or (not scout_count and snap.workers < 16):
            scout_pool = set()
        scout = [tag for tag in self.members[Role.SCOUT] if tag in scout_pool][:1]
        if not scout and scout_pool:
            scout = [min(scout_pool)]
        available.difference_update(scout)
        harass = [tag for tag in self.members[Role.HARASS] if tag in available][:harass_count]
        harass.extend(sorted(available - set(harass))[: harass_count - len(harass)])
        available.difference_update(harass)
        self.members = {
            Role.MAIN: tuple(sorted(available | set(marauders))),
            Role.SCOUT: tuple(scout),
            Role.HARASS: tuple(sorted(harass)),
        }
        return self.members
