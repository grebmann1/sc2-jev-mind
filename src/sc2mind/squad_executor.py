"""Engine-side reconnaissance, exclusive squad ownership and local unit micro."""

from __future__ import annotations

from collections import Counter

from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2

from .actions import (
    INTEL_FRESH_S,
    MICRO_HEALTH_FLOOR,
    SCOUT_REVISIT_S,
    Focus,
    MicroMode,
    Role,
    Stance,
    local_enemy_power,
    pressure_stance,
)
from .executor import ARMY_TYPES
from .intelligence import BASE_NAMES, distance
from .squads import SquadObservation, SquadRoster
from .state import WORKER_NAMES


def targetable(unit) -> bool:
    return unit.is_visible and not unit.is_hallucination and unit.can_be_attacked


class SquadExecutor:
    def __init__(self, bot, executor, memory, trace) -> None:
        self.bot, self.executor, self.memory, self.trace = bot, executor, memory, trace
        self.roster = SquadRoster()
        self.goals: dict[Role, tuple[str, Point2]] = {}
        self._scout_target: Point2 | None = None
        self._last_orders: dict[Role, float] = {}
        self._last_traces: dict[Role, float] = {}
        self._modes: dict[Role, MicroMode] = {}
        self._unsafe: dict[Point2, float] = {}

    def assign(self, snap) -> None:
        bot = self.bot
        old_workers = self.executor.reserved_workers
        old_roster = self.roster.members
        eligible_workers = [
            w.tag
            for w in bot.workers
            if w.health_percentage >= MICRO_HEALTH_FLOOR
            and (w.is_gathering or w.tag in old_workers)
        ]
        self.roster.assign(
            [u.tag for u in bot.units(U.MARINE)],
            [u.tag for u in bot.units(U.MARAUDER)],
            eligible_workers,
            snap,
        )
        for role, tags in self.roster.members.items():
            if tags != old_roster[role]:
                self.trace(
                    "squad_roster",
                    {"t": bot.time, "role": role.value, "tags": [str(tag) for tag in tags]},
                )
        workers = {w.tag for w in bot.workers}
        self.executor.reserved_workers = workers.intersection(self.roster.members[Role.SCOUT])
        for worker in bot.workers:
            if worker.tag in old_workers - self.executor.reserved_workers and bot.mineral_field:
                worker.gather(bot.mineral_field.closest_to(worker))
        self.executor.scan_target = self.scan_request()
        main = self.group(Role.MAIN)
        self.executor.scan_reserve = bool(
            main and bot.townhalls and bot.townhalls.closest_distance_to(main.center) > 20
        )

    def group(self, role):
        tags = set(self.roster.members[role])
        return self.bot.units.filter(lambda u: u.tag in tags)

    def _risk(self, point) -> float:
        return sum(
            local_enemy_power(c.supply, c.structure)
            for c in self.memory.intel.contacts.values()
            if c.dangerous
            and not c.worker
            and self.bot.time - c.seen_at <= INTEL_FRESH_S
            and distance(c.position, (point.x, point.y)) < 14
        )

    def _scout_goal(self, group):
        bot = self.bot
        if self._scout_target is not None:
            p = self._scout_target
            if self._risk(p) > 0:
                self._unsafe[p] = bot.time + SCOUT_REVISIT_S
                self._scout_target = None
            elif group.closest_to(p).distance_to(p) < 6 and bot.is_visible(p):
                self.memory.intel.visit((p.x, p.y), bot.time)
                self.trace("scout_visit", {"t": bot.time, "position": [p.x, p.y]})
                self._scout_target = None
        if self._scout_target is None:
            points = list(
                dict.fromkeys([*bot.enemy_start_locations, *bot.expansion_locations_list])
            )
            points = [
                p
                for p in points
                if not bot.townhalls.closer_than(12, p)
                and self._unsafe.get(p, 0) <= bot.time
                and bot.time - self.memory.intel.scouted.get((p.x, p.y), -1e9) >= SCOUT_REVISIT_S
            ]
            if points:
                # Enemy starts first, then least recently inspected safe expansions.
                self._scout_target = min(
                    points,
                    key=lambda p: (
                        self._risk(p),
                        self.memory.intel.scouted.get((p.x, p.y), -1e9),
                        0 if p in bot.enemy_start_locations else 1,
                        group.closest_to(p).distance_to(p),
                    ),
                )
        return self._scout_target

    def _main_goal(self, group, stance):
        if stance not in (Stance.ATTACK, Stance.DEFEND):
            return "rally", self.executor._rally_point()
        if stance is Stance.DEFEND:
            threats = self.bot.enemy_units.filter(
                lambda u: u.is_visible and u.can_attack_ground
            ).filter(
                lambda u: self.bot.townhalls and self.bot.townhalls.closest_distance_to(u) < 25
            )
            if threats:
                enemy = threats.closest_to(group.center)
                return f"defend:{enemy.tag}", enemy.position
            return "defend_home", self.executor._rally_point()
        visible = self.bot.enemy_structures.filter(targetable)
        if visible:
            enemy = visible.closest_to(group.center)
            return f"structure:{enemy.tag}", enemy.position
        known = [c for c in self.memory.intel.contacts.values() if c.structure]
        if known:
            c = min(known, key=lambda c: distance(c.position, (group.center.x, group.center.y)))
            return f"investigate:{c.tag}", Point2(c.position)
        # No remembered buildings: clear successive expansion locations, not a single empty start.
        return "search", self.executor._attack_target(group)

    def _harass_goal(self, group):
        bases = [
            c for c in self.memory.intel.contacts.values() if c.structure and c.name in BASE_NAMES
        ]
        if bases:
            main = self.goals.get(Role.MAIN)
            c = min(
                bases,
                key=lambda c: (
                    self._risk(Point2(c.position)),
                    1 if main and distance(c.position, (main[1].x, main[1].y)) < 15 else 0,
                    distance(c.position, (group.center.x, group.center.y)),
                ),
            )
            point = Point2(c.position)
            minerals = self.bot.mineral_field.closer_than(10, point)
            if minerals:
                point = minerals.center
            # Take a lateral approach instead of following the main army through its front.
            if group.center.distance_to(point) > 24:
                toward = point.towards(self.bot.game_info.map_center, 12)
                dx, dy = toward.x - point.x, toward.y - point.y
                flank = Point2((point.x - dy, point.y + dx))
                flank = self._bounded(flank)
                if self.bot.in_pathing_grid(flank):
                    point = flank
            return f"harass_base:{c.tag}", point
        # Until an enemy economy is observed, provide additional expansion reconnaissance.
        scout = self._scout_goal(group)
        return ("harass_recon", scout) if scout else ("harass_rally", self.executor._rally_point())

    def observations(self, snap, stance, plan) -> list[SquadObservation]:
        observations = []
        stance = pressure_stance(snap, stance, plan.attack_at_army_supply)
        self.goals = {}
        for role in Role:
            group = self.group(role)
            if not group:
                continue
            if role is Role.SCOUT:
                point = self._scout_goal(group)
                goal = (
                    ("scout" + str(tuple(point)), point)
                    if point
                    else ("scout_wait", self.executor._rally_point())
                )
            elif role is Role.MAIN:
                goal = self._main_goal(group, stance)
            else:
                goal = self._harass_goal(group)
            self.goals[role] = goal
            nearby = [
                u
                for u in (*self.bot.enemy_units, *self.bot.enemy_structures)
                if u.is_visible
                and not u.is_hallucination
                and group.closest_to(u).distance_to(u) < 14
            ]
            available = [u for u in nearby if targetable(u)]
            focuses = []
            if any(
                u.can_attack_ground or u.can_attack_air
                for u in available
                if u.name not in WORKER_NAMES
            ):
                focuses.append(Focus.THREATS)
            if any(u.name in WORKER_NAMES for u in available):
                focuses.append(Focus.WORKERS)
            if any(u.is_structure for u in available):
                focuses.append(Focus.STRUCTURES)
            power = sum(
                local_enemy_power(self.bot.calculate_supply_cost(u.type_id), u.is_structure)
                for u in nearby
                if u.name not in WORKER_NAMES and (u.can_attack_ground or u.can_attack_air)
            )
            away = (
                not self.bot.townhalls or self.bot.townhalls.closest_distance_to(group.center) > 20
            )
            observations.append(
                SquadObservation(
                    role,
                    self.roster.members[role],
                    self.bot.time,
                    sum(max(1, self.bot.calculate_supply_cost(u.type_id)) for u in group),
                    sum(u.health_percentage for u in group) / group.amount,
                    power,
                    tuple(focuses),
                    goal[0],
                    role is not Role.MAIN or stance is Stance.ATTACK,
                    snap.base_threatened,
                    away,
                    any(not u.can_be_attacked and u.can_attack_ground for u in nearby),
                    str(dict(Counter(u.name for u in nearby))),
                    self.memory.intel.short_report(self.bot.time),
                    plan.for_system1(),
                    group.center.distance_to(goal[1]),
                )
            )
        return observations

    def _bounded(self, point):
        area = self.bot.game_info.playable_area
        return Point2(
            (
                min(area.x + area.width - 1, max(area.x + 1, point.x)),
                min(area.y + area.height - 1, max(area.y + 1, point.y)),
            )
        )

    def scan_request(self):
        army = self.bot.units.of_type(ARMY_TYPES)
        if not army:
            return None
        unseen = [
            u
            for u in self.bot.enemy_units
            if u.is_visible
            and not u.can_be_attacked
            and u.can_attack_ground
            and army.closest_to(u).distance_to(u) < 14
        ]
        if unseen:
            return unseen[0].position
        goal = self.goals.get(Role.MAIN)
        if (
            goal
            and goal[0].startswith(("structure:", "investigate:"))
            and army.closest_to(goal[1]).distance_to(goal[1]) < 14
        ):
            if not any(
                u.is_visible and u.distance_to(goal[1]) < 2 for u in self.bot.enemy_structures
            ):
                return goal[1]
        return None

    def execute(self, observations, decisions) -> None:
        bot = self.bot
        for observation in observations:
            role = observation.role
            d = decisions[role]
            changed = d.mode != self._modes.get(role)
            if not changed and bot.time - self._last_orders.get(role, -1e9) < 0.3:
                continue
            self._modes[role], self._last_orders[role] = d.mode, bot.time
            group = self.group(role)
            if not group:
                continue
            if role is not Role.SCOUT and d.mode is not MicroMode.RETREAT:
                self.executor._stim(group)
            for unit in group:
                if unit.tag in bot.unit_tags_received_action:
                    continue
                target = self.goals[role][1]
                if d.mode is MicroMode.RETREAT or (
                    unit.health_percentage < MICRO_HEALTH_FLOOR and observation.away_from_home
                ):
                    unit.move(bot.start_location)
                    continue
                if d.mode is MicroMode.HOLD:
                    unit.hold_position()
                    continue
                if role is Role.SCOUT:
                    unit.move(target)
                    continue
                enemies = [
                    e
                    for e in (*bot.enemy_units, *bot.enemy_structures)
                    if targetable(e)
                    and (unit.can_attack_air if e.is_flying else unit.can_attack_ground)
                    and unit.distance_to(e) < 14
                ]
                if enemies and (
                    d.mode in (MicroMode.ENGAGE, MicroMode.KITE) or observation.advancing
                ):
                    focus = d.focus
                    if focus is Focus.ANY:
                        focus = Focus.WORKERS if role is Role.HARASS else Focus.THREATS
                    selected = [
                        e
                        for e in enemies
                        if (focus is Focus.WORKERS and e.name in WORKER_NAMES)
                        or (focus is Focus.STRUCTURES and e.is_structure)
                        or (
                            focus is Focus.THREATS
                            and e.name not in WORKER_NAMES
                            and (e.can_attack_ground or e.can_attack_air)
                        )
                    ]
                    enemy = min(
                        selected or enemies,
                        key=lambda e: (
                            unit.distance_to(e) > unit.ground_range + unit.radius + e.radius,
                            e.health_percentage,
                            unit.distance_to(e),
                        ),
                    )
                    threats = [
                        e
                        for e in enemies
                        if e.can_attack_ground
                        and unit.distance_to(e) < e.ground_range + e.radius + unit.radius + 2
                    ]
                    if (
                        threats
                        and unit.weapon_cooldown > 0
                        and (d.mode is MicroMode.KITE or role is Role.HARASS)
                    ):
                        danger = min(threats, key=unit.distance_to)
                        unit.move(self._bounded(unit.position.towards(danger.position, -3)))
                    else:
                        unit.attack(enemy)
                elif observation.advancing and role is not Role.SCOUT:
                    unit.attack(target)
                else:
                    unit.move(target)
            if bot.time - self._last_traces.get(role, -1e9) >= 5 or changed:
                self._last_traces[role] = bot.time
                self.trace(
                    "squad_order",
                    {
                        "t": bot.time,
                        "role": role.value,
                        "mode": d.mode.value,
                        "focus": d.focus.value,
                        "tags": [str(tag) for tag in observation.tags],
                        "goal": observation.goal,
                        "position": list(self.goals[role][1]),
                    },
                )

    def lost(self, tag):
        for role, tags in self.roster.members.items():
            if tag in tags:
                self.trace("squad_loss", {"t": self.bot.time, "role": role.value, "tag": tag})
                return role
        return None
