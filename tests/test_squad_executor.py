from types import SimpleNamespace

from conftest import make_snapshot
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2

from sc2mind.actions import Focus, MicroMode, Role, Stance, pressure_stance
from sc2mind.executor import Executor
from sc2mind.squad_executor import SquadExecutor
from sc2mind.squads import SquadDecision
from sc2mind.state import EnemyMemory
from sc2mind.system2 import DEFAULT_PLAN


class Units(list):
    def __call__(self, kind):
        return self.filter(lambda u: u.type_id == kind)

    @property
    def amount(self):
        return len(self)

    ready = property(lambda self: self)
    idle = property(lambda self: self.filter(lambda u: not u.orders))
    center = property(
        lambda self: Point2(
            (
                sum(u.position.x for u in self) / len(self),
                sum(u.position.y for u in self) / len(self),
            )
        )
    )

    def filter(self, fn):
        return Units(u for u in self if fn(u))

    def of_type(self, kinds):
        return self.filter(lambda u: u.type_id in kinds)

    def closest_to(self, target):
        return min(self, key=lambda u: u.distance_to(target))

    def closest_distance_to(self, target):
        return self.closest_to(target).distance_to(target)

    def closer_than(self, radius, target):
        return self.filter(lambda u: u.distance_to(target) < radius)


class Unit:
    def __init__(self, tag, kind=U.MARINE, pos=(0, 0), *, visible=True, targetable=True):
        self.tag, self.type_id, self.position = tag, kind, Point2(pos)
        self.name = {
            U.SCV: "SCV",
            U.MARINE: "Marine",
            U.PROBE: "Probe",
            U.OBSERVER: "Observer",
            U.NEXUS: "Nexus",
        }.get(kind, kind.name)
        self.is_visible, self.can_be_attacked = visible, targetable
        self.is_hallucination = False
        self.is_structure = kind in {U.NEXUS, U.COMMANDCENTER, U.ORBITALCOMMAND}
        self.is_flying = kind is U.OBSERVER
        self.can_attack_ground = kind in {U.MARINE, U.SCV, U.PROBE, U.DARKTEMPLAR}
        self.can_attack_air = kind is U.MARINE
        self.health_percentage = 1
        self.ground_range, self.radius, self.weapon_cooldown = 5, 0.5, 0
        self.orders, self.calls = [], []
        self.is_idle, self.is_gathering = True, kind is U.SCV
        self.energy = 50

    def distance_to(self, other):
        return self.position.distance_to(other.position if hasattr(other, "position") else other)

    def move(self, target):
        self.calls.append(("move", target))

    def attack(self, target):
        self.calls.append(("attack", target))

    def gather(self, target):
        self.calls.append(("gather", target))

    def hold_position(self):
        self.calls.append(("hold",))

    def __call__(self, ability, target):
        self.calls.append((ability, target))


class Bot:
    def __init__(self, units):
        self.time = 100
        self.units = Units(units)
        self.workers = self.units(U.SCV)
        self.townhalls = Units([Unit(1000, U.COMMANDCENTER)])
        self.structures = Units()
        self.enemy_units, self.enemy_structures = Units(), Units()
        self.unit_tags_received_action = set()
        self.start_location = Point2((0, 0))
        self.enemy_start_locations = [Point2((80, 80))]
        self.expansion_locations_list = [Point2((0, 0)), Point2((80, 80)), Point2((50, 70))]
        self.game_info = SimpleNamespace(
            map_center=Point2((50, 50)),
            playable_area=SimpleNamespace(x=0, y=0, width=100, height=100),
        )
        self.mineral_field = Units([Unit(900, U.MINERALFIELD, (2, 0))])
        self.mineral_field[0].mineral_contents = 1000
        self.state = SimpleNamespace(upgrades=set())
        self.supply_cap, self.supply_left = 200, 10
        self.already_pending = lambda unit: 0
        self.can_afford = lambda unit: False
        self.calculate_supply_cost = lambda unit: 2 if unit in {U.MARAUDER, U.DARKTEMPLAR} else 1
        self.is_visible = lambda point: True
        self.in_pathing_grid = lambda point: True


def director(bot):
    return SquadExecutor(bot, Executor(bot), EnemyMemory(), lambda *args: None)


def test_recurring_scout_visits_new_location_and_does_not_lose_worker_to_builder():
    scout, miner = Unit(1, U.SCV), Unit(2, U.SCV, (4, 0))
    bot = Bot([scout, miner])
    d = director(bot)
    d.assign(make_snapshot())
    assert d.executor._builder(bot.start_location).tag == 2
    first = d.observations(make_snapshot(), Stance.RALLY, DEFAULT_PLAN)[0]
    assert first.role is Role.SCOUT
    point = d.goals[Role.SCOUT][1]
    assert first.goal_distance == scout.distance_to(point)
    scout.position = point
    bot.time += 30
    d.observations(make_snapshot(), Stance.RALLY, DEFAULT_PLAN)
    assert d.memory.intel.visits == 1
    assert d.goals[Role.SCOUT][1] != point
    # An infantry scout takes over; the released worker returns to mining.
    bot.units.extend(Unit(tag) for tag in range(10, 18))
    d.assign(make_snapshot())
    assert d.executor.reserved_workers == set()
    assert scout.calls[-1][0] == "gather"


def test_hidden_and_cloaked_units_are_not_direct_attack_targets():
    marine = Unit(1, pos=(70, 70))
    bot = Bot([marine])
    cloak = Unit(10, U.OBSERVER, (71, 70), targetable=False)
    snapshot = Unit(11, U.PROBE, (72, 70), visible=False)
    worker = Unit(12, U.PROBE, (73, 70))
    bot.enemy_units = Units([cloak, snapshot, worker])
    d = director(bot)
    d.roster.members[Role.HARASS] = (1,)
    obs = d.observations(make_snapshot(), Stance.ATTACK, DEFAULT_PLAN)
    d.execute(obs, {Role.HARASS: SquadDecision(MicroMode.ENGAGE, 1, Focus.WORKERS)})
    assert marine.calls[-1] == ("attack", worker)


def test_scout_can_hold_without_an_unwanted_move_order():
    scout = Unit(1, U.SCV)
    bot = Bot([scout])
    bot.enemy_units = Units([Unit(10, U.PROBE, (5, 0))])
    d = director(bot)
    d.assign(make_snapshot())
    observations = d.observations(make_snapshot(), Stance.RALLY, DEFAULT_PLAN)
    d.execute(observations, {Role.SCOUT: SquadDecision(MicroMode.HOLD, 1)})
    assert scout.calls == [("hold",)]


def test_harassment_fires_when_ready_and_kites_during_cooldown():
    marine = Unit(1, pos=(70, 70))
    enemy = Unit(10, pos=(73, 70))
    bot = Bot([marine])
    bot.enemy_units = Units([enemy])
    d = director(bot)
    d.roster.members[Role.HARASS] = (1,)
    obs = d.observations(make_snapshot(), Stance.ATTACK, DEFAULT_PLAN)
    decision = {Role.HARASS: SquadDecision(MicroMode.KITE, 1, Focus.THREATS)}
    d.execute(obs, decision)
    assert marine.calls[-1] == ("attack", enemy)
    marine.weapon_cooldown = 4
    bot.time += 1
    d.execute(obs, decision)
    action, point = marine.calls[-1]
    assert action == "move" and point.distance_to(enemy.position) > marine.position.distance_to(
        enemy.position
    )


async def test_scan_takes_priority_over_mule_for_undetected_attacker():
    marine = Unit(1, pos=(70, 70))
    enemy = Unit(10, U.DARKTEMPLAR, (71, 70), targetable=False)
    bot = Bot([marine])
    bot.enemy_units = Units([enemy])
    bot.townhalls = Units([Unit(1000, U.ORBITALCOMMAND)])
    d = director(bot)
    d.executor.scan_target = d.scan_request()
    await d.executor.economy(DEFAULT_PLAN)
    assert bot.townhalls[0].calls == [(AbilityId.SCANNERSWEEP_SCAN, enemy.position)]


def test_attack_objective_progresses_at_plan_target_without_overriding_defense():
    s = make_snapshot(army_supply=40)
    assert pressure_stance(s, Stance.RALLY, 30) is Stance.ATTACK
    assert pressure_stance(s, Stance.RETREAT, 30) is Stance.RETREAT
    assert (
        pressure_stance(make_snapshot(army_supply=40, enemies_near_base=1), Stance.RALLY, 30)
        is Stance.RALLY
    )


def test_scout_and_harassment_rejoin_main_on_base_threat():
    bot = Bot([Unit(tag) for tag in range(1, 21)])
    d = director(bot)
    d.assign(make_snapshot())
    assert d.roster.members[Role.SCOUT] and d.roster.members[Role.HARASS]
    d.assign(make_snapshot(enemies_near_base=1))
    assert not d.roster.members[Role.SCOUT] and not d.roster.members[Role.HARASS]
    assert len(d.roster.members[Role.MAIN]) == 20


async def test_only_one_orbital_holds_scan_energy_while_others_fuel_economy():
    bot = Bot([])
    first, second = Unit(1000, U.ORBITALCOMMAND), Unit(1001, U.ORBITALCOMMAND)
    bot.townhalls = Units([first, second])
    executor = Executor(bot)
    executor.scan_reserve = True
    await executor.economy(DEFAULT_PLAN)
    assert first.calls == []
    assert second.calls[0][0] is AbilityId.CALLDOWNMULE_CALLDOWNMULE
