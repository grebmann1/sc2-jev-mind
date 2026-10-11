"""Deterministic execution: turns chosen intents into SC2 orders, and owns the reflexes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from .actions import Invest, Stance, legal_investments
from .state import EnemyMemory, Snapshot, snapshot_from_bot
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
    def __init__(self, bot: BotAI) -> None:
        self.bot = bot
        self._last_army_order = -1e9
        self._last_stance: Stance | None = None
        self._scouted = False
        self._search_target: Point2 | None = None
        self._searched: set[Point2] = set()
        self._addon_rax: int | None = None

    def _reserve_addon(self, a: Invest):
        if a not in (Invest.TECHLAB, Invest.REACTOR):
            self._addon_rax = None
            return None
        bare = self.bot.structures(U.BARRACKS).ready.filter(lambda r: not r.has_add_on)
        target = next((r for r in bare if r.tag == self._addon_rax), None)
        if target is None and bare:
            target = min(bare, key=lambda r: (len(r.orders), r.tag))
        self._addon_rax = target.tag if target else None
        return target

    def cost(self, a: Invest) -> tuple[int, int]:
        if a is Invest.NONE:
            return 0, 0
        c = self.bot.calculate_cost(INVEST_IDS[a])
        return c.minerals, c.vespene

    def _affordable(self, m: int, g: int, reserve: tuple[int, int]) -> bool:
        return self.bot.minerals - reserve[0] >= m and self.bot.vespene - reserve[1] >= g

    def _build_spot(self) -> Point2:
        return self.bot.start_location.towards(self.bot.game_info.map_center, 7)

    def _builder(self, near):
        available = self.bot.workers.filter(
            lambda w: (
                w.tag not in self.bot.unit_tags_received_action and (w.is_idle or w.is_gathering)
            )
        )
        return available.closest_to(near) if available else None

    async def _build(self, building: U, near: Point2, **kwargs) -> bool:
        worker = self._builder(near)
        if worker is None:
            return False
        return await self.bot.build(building, near=near, build_worker=worker, **kwargs)

    async def invest(self, a: Invest, snap: Snapshot | None = None) -> bool:
        """Issue the order if possible now. True means done; False means keep saving."""
        bot = self.bot
        addon_target = self._reserve_addon(a)
        snap = snap or snapshot_from_bot(bot, EnemyMemory())
        if a not in legal_investments(snap):
            return False
        if a is Invest.NONE or not bot.can_afford(INVEST_IDS[a]):
            return a is Invest.NONE

        if a is Invest.EXPAND:
            location = await bot.get_next_expansion()
            if location is None or bot.enemy_units.closer_than(12, location):
                return False
            return await self._build(
                U.COMMANDCENTER,
                near=location,
                max_distance=3,
                random_alternative=False,
                placement_step=1,
            )
        if a in (Invest.BARRACKS, Invest.ENGBAY):
            if bot.already_pending(INVEST_IDS[a]):
                return False
            if a is Invest.BARRACKS:
                position = await bot.find_placement(
                    U.BARRACKS, self._build_spot(), addon_place=True
                )
                if position is None:
                    return False
                return await self._build(U.BARRACKS, near=position, max_distance=0)
            return await self._build(INVEST_IDS[a], near=self._build_spot())
        if a is Invest.REFINERY:
            for th in bot.townhalls.ready:
                for geyser in bot.vespene_geyser.closer_than(10, th):
                    if bot.gas_buildings.closer_than(1, geyser):
                        continue
                    worker = self._builder(geyser.position)
                    if worker:
                        return bool(worker.build_gas(geyser))
            return False
        if a in (Invest.TECHLAB, Invest.REACTOR):
            if (
                addon_target
                and not addon_target.orders
                and addon_target.tag not in bot.unit_tags_received_action
            ):
                # Both add-ons use a 2x2 footprint; query a placeable 2x2 building.
                if await bot.can_place_single(U.SUPPLYDEPOT, addon_target.add_on_position):
                    return bool(addon_target.build(INVEST_IDS[a]))
            return False
        if a is Invest.STIM:
            return bot.research(UpgradeId.STIMPACK)
        if a is Invest.WEAPONS1:
            return bot.research(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)
        return False

    def _train(self, producer, unit: U, *, queue: bool = False) -> bool:
        """Unit.train accounts for cost; burnysc2 requires explicit supply accounting."""
        if not producer.train(unit, queue=queue, can_afford_check=True):
            return False
        supply = self.bot.calculate_supply_cost(unit)
        self.bot.supply_left -= supply
        self.bot.supply_used += supply
        return True

    async def economy(self, plan: Plan) -> None:
        """Supply and essential worker growth run before discretionary investment."""
        bot = self.bot

        # Supply depots ahead of production.
        production = bot.townhalls.amount + bot.structures(U.BARRACKS).ready.amount
        if (
            bot.supply_cap < 200
            and bot.supply_left < 2 + 2 * production
            and bot.already_pending(U.SUPPLYDEPOT) < (1 if bot.supply_cap < 60 else 2)
            and bot.can_afford(U.SUPPLYDEPOT)
        ):
            await self._build(
                U.SUPPLYDEPOT, near=bot.start_location.towards(bot.game_info.map_center, -5)
            )

        # Workers up to the plan's target.
        workers = bot.workers.amount + bot.already_pending(U.SCV)
        target = min(plan.target_workers, 22 * bot.townhalls.ready.amount)
        for th in bot.townhalls.ready.idle:
            if th.tag in bot.unit_tags_received_action:
                continue
            if (
                th.type_id is U.COMMANDCENTER
                and bot.structures(U.BARRACKS).ready
                and bot.can_afford(U.ORBITALCOMMAND)
            ):
                th(AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND, subtract_cost=True)
            elif workers < target and bot.can_afford(U.SCV) and bot.supply_left > 0:
                if self._train(th, U.SCV):
                    workers += 1

        for orbital in bot.townhalls(U.ORBITALCOMMAND).ready:
            minerals = bot.mineral_field.closer_than(10, orbital)
            if orbital.energy >= 50 and minerals:
                orbital(
                    AbilityId.CALLDOWNMULE_CALLDOWNMULE,
                    max(minerals, key=lambda m: m.mineral_contents),
                )

        # Raised depots obstruct our army; raise only when an enemy is close.
        for depot in bot.structures.of_type({U.SUPPLYDEPOT, U.SUPPLYDEPOTLOWERED}).ready:
            danger = bot.enemy_units.filter(lambda u: not u.is_flying).closer_than(8, depot)
            if depot.type_id is U.SUPPLYDEPOT and not danger:
                depot(AbilityId.MORPH_SUPPLYDEPOT_LOWER)
            elif depot.type_id is U.SUPPLYDEPOTLOWERED and danger:
                depot(AbilityId.MORPH_SUPPLYDEPOT_RAISE)

    def production(
        self, plan: Plan, reserve: tuple[int, int], pending: Invest = Invest.NONE
    ) -> None:
        bot = self.bot
        self._reserve_addon(pending)

        # Infantry from every free barracks slot, toward the plan's marauder ratio.
        marines = bot.units(U.MARINE).amount + bot.already_pending(U.MARINE)
        marauders = bot.units(U.MARAUDER).amount + bot.already_pending(U.MARAUDER)
        for rax in bot.structures(U.BARRACKS).ready:
            if rax.tag in bot.unit_tags_received_action or rax.tag == self._addon_rax:
                continue
            slots = (2 if rax.has_reactor else 1) - len(rax.orders)
            for slot in range(max(slots, 0)):
                queue = bool(rax.orders) or slot > 0
                total = marines + marauders
                want_marauder = (
                    rax.has_techlab
                    and plan.marauder_ratio > 0
                    and (total == 0 or marauders / total < plan.marauder_ratio)
                )
                if want_marauder and self._affordable(100, 25, reserve) and bot.supply_left >= 2:
                    if self._train(rax, U.MARAUDER, queue=queue):
                        marauders += 1
                elif self._affordable(50, 0, reserve) and bot.supply_left >= 1:
                    if self._train(rax, U.MARINE, queue=queue):
                        marines += 1

    def scout(self) -> None:
        """One early SCV visits the enemy start, then queues a return to mining."""
        bot = self.bot
        if (
            self._scouted
            or bot.time < 80
            or bot.workers.amount < 16
            or not bot.enemy_start_locations
        ):
            return
        candidates = bot.workers.filter(
            lambda w: w.is_gathering and w.tag not in bot.unit_tags_received_action
        )
        if not candidates or not bot.mineral_field:
            return
        worker = candidates.closest_to(bot.enemy_start_locations[0])
        worker.move(bot.enemy_start_locations[0])
        worker.gather(bot.mineral_field.closest_to(bot.start_location), queue=True)
        self._scouted = True

    def _attack_target(self, army) -> Point2:
        bot = self.bot
        if bot.enemy_structures:
            return bot.enemy_structures.closest_to(army.center).position
        if self._search_target is None:
            self._search_target = bot.enemy_start_locations[0]
        if army.center.distance_to(self._search_target) < 8:
            self._searched.add(self._search_target)
            candidates = [
                p
                for p in bot.expansion_locations_list
                if p not in self._searched and not bot.townhalls.closer_than(12, p)
            ]
            if not candidates:
                self._searched.clear()
                candidates = list(bot.expansion_locations_list)
            if candidates:
                self._search_target = min(candidates, key=lambda p: army.center.distance_to(p))
        return self._search_target

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

        if stance is not Stance.RETREAT:
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
            target = self._attack_target(army)
        else:
            target = self._rally_point()

        for u in army:
            nearby = bot.enemy_units.filter(
                lambda e, unit=u: (
                    not e.is_hallucination
                    and (
                        (e.is_flying and unit.can_attack_air)
                        or (not e.is_flying and unit.can_attack_ground)
                    )
                )
            ).closer_than(12, u)
            u.attack(nearby.closest_to(u) if nearby and stance is not Stance.RALLY else target)

    def _stim(self, army) -> None:
        if UpgradeId.STIMPACK not in self.bot.state.upgrades:
            return
        for u in army:
            if u.health_percentage < 0.6 or not self.bot.enemy_units.filter(
                lambda e, unit=u: (
                    (e.is_flying and unit.can_attack_air)
                    or (not e.is_flying and unit.can_attack_ground)
                )
            ).closer_than(8, u):
                continue
            if u.type_id is U.MARINE and not u.has_buff(BuffId.STIMPACK):
                u(AbilityId.EFFECT_STIM_MARINE)
            elif u.type_id is U.MARAUDER and not u.has_buff(BuffId.STIMPACKMARAUDER):
                u(AbilityId.EFFECT_STIM_MARAUDER)
