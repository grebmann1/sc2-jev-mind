"""Exercise order scheduling with game-free fakes, using the installed SC2 vocabulary."""

from types import SimpleNamespace

from conftest import make_snapshot
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2

from sc2mind.actions import Invest
from sc2mind.executor import Executor
from sc2mind.system2 import DEFAULT_PLAN


class Units(list):
    @property
    def amount(self):
        return len(self)

    @property
    def ready(self):
        return self

    @property
    def idle(self):
        return self.filter(lambda u: not u.orders)

    def __call__(self, kind):
        return self.filter(lambda u: u.type_id == kind)

    def filter(self, predicate):
        return Units(u for u in self if predicate(u))

    def of_type(self, kinds):
        return self.filter(lambda u: u.type_id in kinds)

    def closer_than(self, distance, target):
        return self.filter(
            lambda u: (
                u.position.distance_to(target.position if hasattr(target, "position") else target)
                < distance
            )
        )

    def closest_to(self, target):
        point = target.position if hasattr(target, "position") else target
        return min(self, key=lambda u: u.position.distance_to(point))


COSTS = {
    U.MARINE: (50, 0, 1),
    U.MARAUDER: (100, 25, 2),
    U.SCV: (50, 0, 1),
    U.SUPPLYDEPOT: (100, 0, 0),
    U.BARRACKS: (150, 0, 0),
    U.BARRACKSTECHLAB: (50, 25, 0),
    U.BARRACKSREACTOR: (50, 50, 0),
}


class Producer:
    def __init__(self, bot, tag, *, reactor=False, techlab=False, orders=0):
        self.bot, self.tag = bot, tag
        self.type_id = U.BARRACKS
        self.has_reactor, self.has_techlab = reactor, techlab
        self.has_add_on = reactor or techlab
        self.orders = [object() for _ in range(orders)]
        self.calls = []

    def train(self, unit, queue=False, can_afford_check=False):
        m, g, _ = COSTS[unit]
        assert can_afford_check
        if self.bot.minerals < m or self.bot.vespene < g:
            return False
        self.calls.append((unit, queue))
        self.bot.minerals -= m
        self.bot.vespene -= g
        self.bot.unit_tags_received_action.add(self.tag)
        return True


class Bot:
    def __init__(self, minerals=500, supply=10):
        self.minerals = minerals
        self.vespene = 100
        self.supply_left, self.supply_used, self.supply_cap = supply, 15, 23
        self.units = Units()
        self.structures = Units()
        self.townhalls = Units()
        self.workers = Units(
            [SimpleNamespace(tag=10, is_idle=True, is_gathering=False, position=Point2((0, 0)))]
        )
        self.enemy_units = Units()
        self.mineral_field = Units()
        self.unit_tags_received_action = set()
        self.already_pending = lambda unit: 0
        self.start_location = Point2((0, 0))
        self.game_info = SimpleNamespace(map_center=Point2((50, 50)))
        self.build_calls = []
        self.expansion = None
        self.build_succeeds = True

    def calculate_supply_cost(self, unit):
        return COSTS[unit][2]

    def can_afford(self, unit):
        m, g, _ = COSTS.get(unit, (400, 0, 0))
        return self.minerals >= m and self.vespene >= g

    async def build(self, unit, **kwargs):
        self.build_calls.append(unit)
        if self.build_succeeds:
            self.minerals -= COSTS.get(unit, (400, 0, 0))[0]
        return self.build_succeeds

    async def get_next_expansion(self):
        return self.expansion


def test_reactor_orders_are_queued_and_supply_is_spent():
    bot = Bot()
    rax = Producer(bot, 1, reactor=True)
    bot.structures.append(rax)
    Executor(bot).production(DEFAULT_PLAN, (0, 0))
    assert rax.calls == [(U.MARINE, False), (U.MARINE, True)]
    assert (bot.supply_left, bot.supply_used, bot.minerals) == (8, 17, 400)


def test_existing_reactor_order_gets_only_one_queued_marine():
    bot = Bot()
    rax = Producer(bot, 1, reactor=True, orders=1)
    bot.structures.append(rax)
    Executor(bot).production(DEFAULT_PLAN, (0, 0))
    assert rax.calls == [(U.MARINE, True)]


def test_producers_do_not_overcommit_shared_supply():
    bot = Bot(supply=1)
    producers = [Producer(bot, 1, reactor=True), Producer(bot, 2)]
    bot.structures.extend(producers)
    Executor(bot).production(DEFAULT_PLAN, (0, 0))
    assert sum(len(p.calls) for p in producers) == 1
    assert bot.supply_left == 0


def test_investment_reservation_is_respected():
    bot = Bot(minerals=440)
    rax = Producer(bot, 1, reactor=True)
    bot.structures.append(rax)
    Executor(bot).production(DEFAULT_PLAN, (400, 0))
    assert rax.calls == []
    assert bot.minerals == 440


def test_addon_order_is_not_overwritten_by_production():
    bot = Bot()
    rax = Producer(bot, 1)
    bot.structures.append(rax)
    bot.unit_tags_received_action.add(1)
    Executor(bot).production(DEFAULT_PLAN, (0, 0))
    assert rax.calls == []


def test_zero_marauder_target_produces_only_marines():
    bot = Bot()
    rax = Producer(bot, 1, techlab=True)
    bot.structures.append(rax)
    Executor(bot).production(DEFAULT_PLAN.model_copy(update={"marauder_ratio": 0}), (0, 0))
    assert rax.calls == [(U.MARINE, False)]


async def test_supply_emergency_spends_before_investment():
    bot = Bot(minerals=100, supply=0)
    await Executor(bot).economy(DEFAULT_PLAN)
    assert bot.build_calls == [U.SUPPLYDEPOT]
    assert bot.minerals == 0


async def test_unavailable_expansion_does_not_report_success():
    bot = Bot()
    assert not await Executor(bot).invest(Invest.EXPAND, make_snapshot())
    assert bot.build_calls == []


async def test_failed_expansion_order_does_not_report_success():
    bot = Bot()
    bot.expansion = Point2((20, 20))
    bot.build_succeeds = False
    assert not await Executor(bot).invest(Invest.EXPAND, make_snapshot())
    assert bot.build_calls == [U.COMMANDCENTER]


async def test_illegal_investment_is_rejected_before_ordering():
    bot = Bot()
    assert not await Executor(bot).invest(Invest.STIM, make_snapshot())
    assert bot.build_calls == []


async def test_builder_with_an_order_this_frame_is_not_reused():
    bot = Bot(minerals=100, supply=0)
    bot.unit_tags_received_action.add(10)
    await Executor(bot).economy(DEFAULT_PLAN)
    assert bot.build_calls == []


async def test_addon_checks_footprint_then_orders_only_the_addon():
    bot = Bot()
    rax = Producer(bot, 1)
    rax.add_on_position = Point2((2.5, -0.5))
    orders = []
    rax.build = lambda unit: orders.append(unit) or True
    bot.structures.append(rax)

    async def place(unit, position):
        assert unit is U.SUPPLYDEPOT
        assert position == rax.add_on_position
        return True

    bot.can_place_single = place
    assert await Executor(bot).invest(Invest.TECHLAB, make_snapshot())
    assert orders == [U.BARRACKSTECHLAB]


async def test_pending_addon_drains_one_busy_barracks_without_stopping_others():
    bot = Bot()
    busy = Producer(bot, 1, orders=1)
    busy.add_on_position = Point2((2.5, -0.5))
    other = Producer(bot, 2, techlab=True)
    bot.structures.extend([busy, other])
    executor = Executor(bot)
    assert not await executor.invest(Invest.REACTOR, make_snapshot())
    executor.production(DEFAULT_PLAN, (50, 50), Invest.REACTOR)
    assert not busy.calls and other.calls
    bot.unit_tags_received_action.clear()
    busy.orders.clear()  # existing marine completes on the next observation
    orders = []
    busy.build = lambda unit: orders.append(unit) or True

    async def place(*args):
        return True

    bot.can_place_single = place
    assert await executor.invest(Invest.REACTOR, make_snapshot())
    assert orders == [U.BARRACKSREACTOR]


def test_addon_reservation_survives_frames_and_is_released_when_intent_changes():
    bot = Bot()
    a, b = Producer(bot, 1, orders=1), Producer(bot, 2, orders=1)
    bot.structures.extend([a, b])
    executor = Executor(bot)
    executor.production(DEFAULT_PLAN, (0, 0), Invest.TECHLAB)
    b.orders.clear()  # another barracks finishing does not move the reservation
    executor.production(DEFAULT_PLAN, (0, 0), Invest.TECHLAB)
    assert executor._addon_rax == 1 and b.calls
    a.orders.clear()
    executor.production(DEFAULT_PLAN, (0, 0), Invest.NONE)
    assert executor._addon_rax is None and a.calls
