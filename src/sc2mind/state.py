"""Compact, SC2-independent snapshot of the game that both systems reason over."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI

WORKER_NAMES = {"SCV", "Probe", "Drone", "MULE"}


@dataclass
class Snapshot:
    time_s: float
    minerals: int
    gas: int
    supply_used: int
    supply_cap: int
    workers: int
    townhalls: int
    townhalls_pending: int
    barracks: int
    barracks_pending: int
    barracks_bare: int  # ready barracks without an add-on
    techlabs: int
    reactors: int
    refineries: int
    engbays: int
    stim: str  # "none" | "researching" | "done"
    weapons1: str
    army: dict[str, int] = field(default_factory=dict)
    army_supply: int = 0
    enemy_race: str = "Unknown"
    enemy_army_seen: dict[str, int] = field(default_factory=dict)
    enemy_army_supply_est: int = 0
    enemy_structures_seen: int = 0
    enemies_near_base: int = 0
    # Completed prerequisites and work in progress, kept distinct from supply cap.
    depots: int = 1
    depots_pending: int = 0
    refineries_pending: int = 0
    techlabs_pending: int = 0
    reactors_pending: int = 0
    engbays_pending: int = 0
    enemy_supply_near_base: float | None = None

    @property
    def base_threatened(self) -> bool:
        return self.enemies_near_base > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_jev_state(self, plan_summary: str, memory_summary: str = "") -> dict[str, str]:
        """Flat, short text fields: Jev works best on small, legible state."""

        def fmt(d: dict[str, int]) -> str:
            return ", ".join(f"{k}={v}" for k, v in sorted(d.items())) or "none"

        return {
            "time": f"{int(self.time_s // 60)}:{int(self.time_s % 60):02d}",
            "economy": (
                f"minerals={self.minerals} gas={self.gas} workers={self.workers} "
                f"bases={self.townhalls}(+{self.townhalls_pending}) refineries={self.refineries}"
            ),
            "supply": f"{self.supply_used}/{self.supply_cap}",
            "production": (
                f"barracks={self.barracks}(+{self.barracks_pending}) techlabs={self.techlabs} "
                f"reactors={self.reactors} engbays={self.engbays} stim={self.stim} weapons1={self.weapons1}"
            ),
            "our_army": f"{fmt(self.army)} (supply {self.army_supply})",
            "enemy": (
                f"race={self.enemy_race} army_seen={fmt(self.enemy_army_seen)} "
                f"(est supply {self.enemy_army_supply_est}) structures_seen={self.enemy_structures_seen}"
            ),
            "threat": f"{self.enemies_near_base} enemy units near our bases",
            "strategic_plan": plan_summary,
            "experience_memory": memory_summary[:650],
        }


class EnemyMemory:
    """Track individual enemy tags, expire old sightings, and forget confirmed deaths."""

    def __init__(self, max_age_s: float = 120.0) -> None:
        self.max_age_s = max_age_s
        self.units: dict[int, tuple[str, float, float]] = {}
        self.structures: set[int] = set()
        self.race = "Unknown"

    def update(self, bot: BotAI) -> None:
        for unit in (*bot.enemy_units, *bot.enemy_structures):
            race = getattr(getattr(unit, "race", None), "name", "Unknown")
            if race in {"Terran", "Protoss", "Zerg"}:
                self.race = race
                break
        for u in bot.enemy_units:
            if u.name in WORKER_NAMES or u.is_hallucination:
                continue
            self.units[u.tag] = (u.name, bot.calculate_supply_cost(u.type_id), bot.time)
        self.units = {
            tag: sighting
            for tag, sighting in self.units.items()
            if bot.time - sighting[2] <= self.max_age_s
        }
        self.structures.update(s.tag for s in bot.enemy_structures)

    def forget(self, tag: int) -> None:
        self.units.pop(tag, None)
        self.structures.discard(tag)

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name, _, _ in self.units.values():
            counts[name] = counts.get(name, 0) + 1
        return counts

    @property
    def supply_est(self) -> int:
        return int(sum(supply for _, supply, _ in self.units.values()))


def snapshot_from_bot(bot: BotAI, memory: EnemyMemory) -> Snapshot:
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.upgrade_id import UpgradeId

    def upgrade_state(up: UpgradeId) -> str:
        if up in bot.state.upgrades:
            return "done"
        return "researching" if bot.already_pending_upgrade(up) > 0 else "none"

    memory.update(bot)
    rax = bot.structures(U.BARRACKS)
    army_types = {U.MARINE: 1, U.MARAUDER: 2}
    army = {t.name.title(): bot.units(t).amount for t in army_types if bot.units(t).amount}
    near_base = 0
    local_supply = 0.0
    if bot.townhalls:
        threats = bot.enemy_units.filter(
            lambda u: (
                not u.is_hallucination
                and (u.can_attack_ground or u.can_attack_air)
                and bot.townhalls.closest_distance_to(u) < 20
            )
        )
        near_base = threats.amount
        local_supply = sum(bot.calculate_supply_cost(u.type_id) for u in threats)

    return Snapshot(
        time_s=bot.time,
        minerals=bot.minerals,
        gas=bot.vespene,
        supply_used=int(bot.supply_used),
        supply_cap=int(bot.supply_cap),
        workers=bot.workers.amount,
        townhalls=bot.townhalls.ready.amount,
        townhalls_pending=int(bot.already_pending(U.COMMANDCENTER)),
        barracks=rax.ready.amount,
        barracks_pending=int(bot.already_pending(U.BARRACKS)),
        barracks_bare=rax.ready.filter(lambda r: not r.has_add_on).amount,
        techlabs=bot.structures(U.BARRACKSTECHLAB).ready.amount,
        reactors=bot.structures(U.BARRACKSREACTOR).ready.amount,
        refineries=bot.gas_buildings.ready.amount,
        engbays=bot.structures(U.ENGINEERINGBAY).ready.amount,
        stim=upgrade_state(UpgradeId.STIMPACK),
        weapons1=upgrade_state(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
        army=army,
        army_supply=sum(bot.units(t).amount * s for t, s in army_types.items()),
        enemy_race=memory.race if bot.enemy_race.name == "Random" else bot.enemy_race.name,
        enemy_army_seen=memory.counts,
        enemy_army_supply_est=memory.supply_est,
        enemy_structures_seen=len(memory.structures),
        enemies_near_base=near_base,
        depots=bot.structures.of_type({U.SUPPLYDEPOT, U.SUPPLYDEPOTLOWERED}).ready.amount,
        depots_pending=int(bot.already_pending(U.SUPPLYDEPOT)),
        refineries_pending=int(bot.already_pending(U.REFINERY)),
        techlabs_pending=int(bot.already_pending(U.BARRACKSTECHLAB)),
        reactors_pending=int(bot.already_pending(U.BARRACKSREACTOR)),
        engbays_pending=int(bot.already_pending(U.ENGINEERINGBAY)),
        enemy_supply_near_base=local_supply,
    )
