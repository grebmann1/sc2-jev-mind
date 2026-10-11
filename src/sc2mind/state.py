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

    @property
    def base_threatened(self) -> bool:
        return self.enemies_near_base > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_jev_state(self, plan_summary: str) -> dict[str, str]:
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
        }


class EnemyMemory:
    """Remembers the most of each enemy unit type seen at once (fog of war)."""

    def __init__(self) -> None:
        self.max_seen: dict[str, int] = {}
        self.supply_per_type: dict[str, float] = {}
        self.structures: set[int] = set()

    def update(self, bot: "BotAI") -> None:
        counts: dict[str, int] = {}
        for u in bot.enemy_units:
            if u.name in WORKER_NAMES:
                continue
            counts[u.name] = counts.get(u.name, 0) + 1
            self.supply_per_type[u.name] = bot.calculate_supply_cost(u.type_id)
        for name, n in counts.items():
            self.max_seen[name] = max(self.max_seen.get(name, 0), n)
        self.structures.update(s.tag for s in bot.enemy_structures)

    @property
    def supply_est(self) -> int:
        return int(sum(n * self.supply_per_type.get(k, 1) for k, n in self.max_seen.items()))


def snapshot_from_bot(bot: "BotAI", memory: EnemyMemory) -> Snapshot:
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
    if bot.townhalls:
        near_base = bot.enemy_units.filter(
            lambda u: bot.townhalls.closest_distance_to(u) < 20
        ).amount

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
        refineries=bot.gas_buildings.amount,
        engbays=bot.structures(U.ENGINEERINGBAY).ready.amount,
        stim=upgrade_state(UpgradeId.STIMPACK),
        weapons1=upgrade_state(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
        army=army,
        army_supply=sum(bot.units(t).amount * s for t, s in army_types.items()),
        enemy_race=bot.enemy_race.name,
        enemy_army_seen=dict(memory.max_seen),
        enemy_army_supply_est=memory.supply_est,
        enemy_structures_seen=len(memory.structures),
        enemies_near_base=near_base,
    )
