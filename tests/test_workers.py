from conftest import make_snapshot
from sc2.ids.unit_typeid import UnitTypeId as U
from test_squad_executor import Bot, Unit, Units

from sc2mind.actions import Invest, WorkerMode
from sc2mind.economy import EconomyDecision
from sc2mind.executor import Executor
from sc2mind.system2 import DEFAULT_PLAN
from sc2mind.workers import WorkerManager


def setup(count=16):
    workers = [Unit(tag, U.SCV, (tag % 5, 2)) for tag in range(1, count + 1)]
    bot = Bot(workers)
    bot.minerals = 200
    base = bot.townhalls[0]
    base.assigned_harvesters, base.ideal_harvesters = count, 16
    bot.structures = Units([base])
    gas = Unit(2000, U.REFINERY, (8, 0))
    gas.assigned_harvesters, gas.ideal_harvesters, gas.has_vespene = 0, 3, True
    bot.gas_buildings = Units([gas])
    for w in workers:
        w.order_target, w.is_idle = 900, False
        w.is_constructing_scv = w.is_repairing = False
        old_gather = w.gather

        def gather(target, worker=w, record=old_gather):
            record(target)
            worker.order_target, worker.is_repairing = target.tag, False

        def repair(target, worker=w):
            worker.calls.append(("repair", target))
            worker.is_repairing = True
            worker.order_target = target.tag

        w.gather, w.repair = gather, repair
    events = []
    manager = WorkerManager(bot, Executor(bot), lambda kind, data: events.append((kind, data)))
    return bot, manager, events


def decision(mode):
    return EconomyDecision(mode, Invest.NONE, 1)


def test_gas_allocation_changes_and_scout_is_never_reassigned():
    bot, manager, _ = setup()
    manager.executor.reserved_workers = {1}
    manager.step(decision(WorkerMode.GAS), Invest.NONE)
    assert sum(w.order_target == 2000 for w in bot.workers) == 3
    assert bot.workers[0].calls == []
    bot.time += 3
    manager.step(decision(WorkerMode.MINERALS), Invest.NONE)
    assert sum(w.order_target == 2000 for w in bot.workers) == 1
    assert bot.workers[0].calls == []


def test_builder_and_workers_already_commanded_are_protected():
    bot, manager, _ = setup()
    bot.workers[0].is_constructing_scv = True
    bot.unit_tags_received_action = {2}
    bot.workers[2].is_gathering = False
    manager._mining[3] = ("minerals", 1000)
    manager.step(decision(WorkerMode.GAS), Invest.NONE)
    assert bot.workers[0].calls == bot.workers[1].calls == []
    assert bot.workers[2].calls == []


def test_idle_worker_pool_keeps_mineral_income_when_gas_is_prioritized():
    bot, manager, _ = setup(12)
    second = Unit(2001, U.REFINERY, (9, 0))
    second.assigned_harvesters, second.ideal_harvesters, second.has_vespene = 0, 3, True
    bot.gas_buildings.append(second)
    for w in bot.workers:
        w.is_idle = True
    manager.step(decision(WorkerMode.GAS), Invest.NONE)
    assert sum(w.order_target in {2000, 2001} for w in bot.workers) == 4
    assert sum(w.order_target == 900 for w in bot.workers) == 8


def test_endangered_workers_leave_for_a_safe_base_without_waiting_for_model():
    bot, manager, _ = setup()
    second = Unit(1001, U.COMMANDCENTER, (80, 80))
    second.assigned_harvesters, second.ideal_harvesters = 0, 16
    bot.townhalls.append(second)
    bot.enemy_units = Units([Unit(500, U.MARINE, (1, 0))])
    manager.step(decision(WorkerMode.BALANCE), Invest.NONE)
    assert all(w.calls[-1] == ("move", second.position) for w in bot.workers)
    assert manager.executor.economy_workers == {w.tag for w in bot.workers}
    assert manager.executor._builder(bot.start_location) is None


def test_repair_is_limited_and_released_workers_return_to_mining():
    bot, manager, _ = setup()
    bot.townhalls[0].health_percentage = 0.5
    manager.step(decision(WorkerMode.REPAIR), Invest.NONE)
    repairing = [w for w in bot.workers if w.is_repairing]
    assert len(repairing) == 2
    assert manager.executor._builder(bot.start_location).tag not in manager.executor.economy_workers
    bot.townhalls[0].health_percentage = 1
    bot.time += 3
    manager.step(decision(WorkerMode.BALANCE), Invest.NONE)
    assert not manager.executor.economy_workers
    assert all(w.calls[-1][0] == "gather" for w in repairing)


def test_oversaturated_workers_fill_new_base_and_observation_records_capacity():
    bot, manager, _ = setup(20)
    bot.gas_buildings = Units()
    second = Unit(1001, U.COMMANDCENTER, (40, 40))
    second.assigned_harvesters, second.ideal_harvesters = 0, 16
    bot.townhalls.append(second)
    patch = Unit(901, U.MINERALFIELD, (42, 40))
    bot.mineral_field.append(patch)
    manager.step(decision(WorkerMode.BALANCE), Invest.NONE)
    assert sum(w.order_target == patch.tag for w in bot.workers) == 4
    observation = manager.observation(make_snapshot(townhalls=2), DEFAULT_PLAN)
    assert sum(b.ideal for b in observation.bases) == 32
