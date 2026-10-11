"""Deterministic execution: turns chosen intents into SC2 orders, and owns the reflexes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from .actions import Invest, Stance
from .state import Snapshot
from .system2 import Plan

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI

INVEST_IDS: dict[Invest, U | UpgradeId] = {
    Invest.EXPAND: U.COMMANDCENTER,
    Invest.BARRACKS: U.BARRACKS,
    Invest.REFINERY: U.REFINERY,
    Invest.TECHLAB: U.BARRACKSTECHLAB,
    Invest.REACTOR: U.BARRACKSREACTOR,
    Invest.ENGBAY: U.ENGINEERINGBAY,
    Invest.STIM: UpgradeId.STIMPACK,
    Invest.WEAPONS1: UpgradeId.TERRANINFANTRYWEAPONSLEVEL1,
}

ARMY_TYPES = {U.MARINE, U.MARAUDER}
ARMY_ORDER_INTERVAL_S = 1.0


class Executor:
    def __init__(self, bot: "BotAI") -> None:
        self.bot = bot
        self._last_army_order = -1e9
        self._last_stance: Stance | None = None

    def cost(self, a: Invest) -> tuple[int, int]:
        if a is Invest.NONE:
            return 0, 0
        c = self.bot.calculate_cost(INVEST_IDS[a])
        return c.minerals, c.vespene

    def _affordable(self, m: int, g: int, reserve: tuple[int, int]) -> bool:
        return self.bot.minerals - reserve[0] >= m and self.bot.vespene - reserve[1] >= g

    def _build_spot(self) -> Point2:
        return self.bot.start_location.towards(self.bot.game_info.map_center, 7)

    async def invest(self, a: Invest) -> bool:
        """Issue the order if possible now. True means done; False means keep saving."""
        bot = self.bot
        if a is Invest.NONE or not bot.can_afford(INVEST_IDS[a]):
            return a is Invest.NONE

        if a is Invest.EXPAND:
            await bot.expand_now()
            return True
        if a in (Invest.BARRACKS, Invest.ENGBAY):
            return await bot.build(INVEST_IDS[a], near=self._build_spot())
        if a is Invest.REFINERY:
            for th in bot.townhalls.ready:
                for geyser in bot.vespene_geyser.closer_than(10, th):
                    if bot.gas_buildings.closer_than(1, geyser):
                        continue
                    worker = bot.select_build_worker(geyser.position)
                    if worker:
                        worker.build_gas(geyser)
                        return True
            return False
        if a in (Invest.TECHLAB, Invest.REACTOR):
            bare = bot.structures(U.BARRACKS).ready.idle.filter(lambda r: not r.has_add_on)
            if bare:
                bare.first.build(INVEST_IDS[a])
                return True
            return False
        if a is Invest.STIM:
            labs = bot.structures(U.BARRACKSTECHLAB).ready.idle
            if labs:
                labs.first.research(UpgradeId.STIMPACK)
                return True
            return False
        if a is Invest.WEAPONS1:
            bays = bot.structures(U.ENGINEERINGBAY).ready.idle
            if bays:
                bays.first.research(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)
                return True
            return False
        return False

    async def reflexes(self, plan: Plan, reserve: tuple[int, int]) -> None:
        """Rules simple enough that no model should spend a call on them."""
        bot = self.bot

        # Supply depots ahead of production.
        production = bot.townhalls.amount + bot.structures(U.BARRACKS).ready.amount
        if (
            bot.supply_cap < 200
            and bot.supply_left < 2 + 2 * production
            and bot.already_pending(U.SUPPLYDEPOT) < (1 if bot.supply_cap < 60 else 2)
            and self._affordable(100, 0, reserve)
        ):
            await bot.build(U.SUPPLYDEPOT, near=bot.start_location.towards(bot.game_info.map_center, -5))

        # Workers up to the plan's target.
        workers = bot.workers.amount + bot.already_pending(U.SCV)
        for th in bot.townhalls.ready.idle:
            if workers < plan.target_workers and self._affordable(50, 0, reserve) and bot.supply_left > 0:
                th.train(U.SCV)
                workers += 1

        # Infantry from every free barracks slot, toward the plan's marauder ratio.
        marines = bot.units(U.MARINE).amount + bot.already_pending(U.MARINE)
        marauders = bot.units(U.MARAUDER).amount + bot.already_pending(U.MARAUDER)
        for rax in bot.structures(U.BARRACKS).ready:
            slots = (2 if rax.has_reactor else 1) - len(rax.orders)
            for _ in range(max(slots, 0)):
                total = marines + marauders
                want_marauder = rax.has_techlab and (total == 0 or marauders / total < plan.marauder_ratio)
                if want_marauder and self._affordable(100, 25, reserve) and bot.supply_left >= 2:
                    rax.train(U.MARAUDER)
                    marauders += 1
                elif self._affordable(50, 0, reserve) and bot.supply_left >= 1:
                    rax.train(U.MARINE)
                    marines += 1

    def _rally_point(self) -> Point2:
        bot = self.bot
        if not bot.townhalls:
            return bot.start_location
        front = bot.townhalls.closest_to(bot.enemy_start_locations[0])
        return front.position.towards(bot.game_info.map_center, 8)

    def army(self, stance: Stance, snap: Snapshot) -> None:
        bot = self.bot
        army = bot.units.of_type(ARMY_TYPES)
        if not army:
            return

        self._stim(army)

        changed = stance is not self._last_stance
        if not changed and bot.time - self._last_army_order < ARMY_ORDER_INTERVAL_S:
            return
        self._last_army_order, self._last_stance = bot.time, stance

        if stance is Stance.RETREAT:
            for u in army:
                u.move(bot.start_location)
            return

        if stance is Stance.DEFEND and bot.townhalls:
            threats = bot.enemy_units.filter(lambda e: bot.townhalls.closest_distance_to(e) < 25)
            target = threats.closest_to(army.center).position if threats else self._rally_point()
        elif stance is Stance.ATTACK:
            target = (
                bot.enemy_structures.closest_to(army.center).position
                if bot.enemy_structures
                else bot.enemy_start_locations[0]
            )
        else:
            target = self._rally_point()

        for u in army:
            nearby = bot.enemy_units.closer_than(12, u)
            u.attack(nearby.closest_to(u) if nearby and stance is not Stance.RALLY else target)

    def _stim(self, army) -> None:
        if UpgradeId.STIMPACK not in self.bot.state.upgrades:
            return
        for u in army:
            if u.health_percentage < 0.6 or not self.bot.enemy_units.closer_than(8, u):
                continue
            if u.type_id is U.MARINE and not u.has_buff(BuffId.STIMPACK):
                u(AbilityId.EFFECT_STIM_MARINE)
            elif u.type_id is U.MARAUDER and not u.has_buff(BuffId.STIMPACKMARAUDER):
                u(AbilityId.EFFECT_STIM_MARAUDER)
