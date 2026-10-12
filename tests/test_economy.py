import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import make_snapshot

from sc2mind.actions import Invest, MicroMode, Role, WorkerMode, economy_investment
from sc2mind.config import Config
from sc2mind.economy import EconomyDecision, EconomyObservation, JevEconomyReflex, MiningSite
from sc2mind.mind import Mind
from sc2mind.squads import SquadDecision, SquadObservation


def observation(**changes):
    return replace(
        EconomyObservation(
            make_snapshot(time_s=0, gas=250), (MiningSite(10, 16, 16),), (MiningSite(20, 2, 3),)
        ),
        **changes,
    )


class WorkerBrain:
    def __init__(self, blocked=False):
        self.blocked, self.calls, self.closed = blocked, 0, 0
        self.gate = asyncio.Event()

    async def decide(self, o):
        self.calls += 1
        if self.blocked:
            await self.gate.wait()
        return EconomyDecision(WorkerMode.MINERALS, Invest.NONE, 0.9)

    async def close(self):
        self.closed += 1


async def test_worker_brain_is_parallel_and_does_not_overlap_requests():
    class Army:
        async def decide(self, o):
            return SquadDecision(MicroMode.ADVANCE, 0.9)

    worker = WorkerBrain(blocked=True)
    mind = Mind(
        Config(), object(), object(), economy_reflex=worker, squad_reflexes={Role.MAIN: Army()}
    )
    try:
        mind.tick_economy(observation())
        squad = SquadObservation(Role.MAIN, (1,), 0, 10, 1, 0, (), "push", True)
        mind.tick_squads([squad])
        await asyncio.sleep(0)
        assert mind.tick_squads([replace(squad, time_s=0.1)])[Role.MAIN].mode is MicroMode.ADVANCE
        for t in (1, 4, 6):
            mind.tick_economy(observation(snap=make_snapshot(time_s=t, gas=250)))
        assert worker.calls == 1
    finally:
        await mind.close()
    assert worker.closed == 1 and mind._economy_task is None


async def test_economy_revalidates_threat_and_expires_previous_answer():
    worker = WorkerBrain()
    mind = Mind(Config(), object(), object(), economy_reflex=worker)
    try:
        mind.tick_economy(observation())
        await asyncio.sleep(0)
        assert (
            mind.tick_economy(observation(snap=make_snapshot(time_s=0.1, gas=250))).mode
            is WorkerMode.MINERALS
        )
        danger = mind.tick_economy(
            observation(snap=make_snapshot(time_s=0.2, gas=250, enemies_near_base=1))
        )
        assert danger.mode is WorkerMode.EVACUATE and danger.priority is Invest.NONE
        worker.blocked = True
        mind.tick_economy(observation(snap=make_snapshot(time_s=4, gas=250)))
        await asyncio.sleep(0)
        assert (
            mind.tick_economy(observation(snap=make_snapshot(time_s=9, gas=250))).mode
            is WorkerMode.BALANCE
        )
    finally:
        await mind.close()


async def test_base_change_and_expired_economy_responses_are_discarded():
    events, worker = [], WorkerBrain()
    mind = Mind(
        Config(),
        object(),
        object(),
        economy_reflex=worker,
        trace=lambda kind, data: events.append((kind, data)),
    )
    try:
        mind.tick_economy(observation())
        await asyncio.sleep(0)
        mind.tick_economy(observation(bases=(MiningSite(30, 16, 16),)))
        assert (
            next(d for k, d in events if k == "economy_discarded")["reason"] == "assignment_changed"
        )
        mind.tick_economy(observation(snap=make_snapshot(time_s=4, gas=250)))
        await asyncio.sleep(0)
        mind.tick_economy(observation(snap=make_snapshot(time_s=14, gas=250)))
        assert [d for k, d in events if k == "economy_discarded"][-1]["reason"] == "too_old"
    finally:
        await mind.close()


async def test_worker_timeout_falls_back_and_is_consumed_on_close():
    events, worker = [], WorkerBrain(blocked=True)
    mind = Mind(
        Config(s1_timeout_s=0.01),
        object(),
        object(),
        economy_reflex=worker,
        trace=lambda kind, data: events.append((kind, data)),
    )
    try:
        mind.tick_economy(observation())
        await asyncio.sleep(0.03)
        assert mind.tick_economy(observation()).mode is WorkerMode.BALANCE
        assert next(d for k, d in events if k == "economy_error")["error"] == "TimeoutError"
    finally:
        await mind.close()


async def test_economy_sdk_uses_flat_state_and_only_legal_choices():
    captured = []

    async def system_one(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices={
                "workers": SimpleNamespace(choice="evacuate", probabilities={"evacuate": 0.9}),
                "growth": SimpleNamespace(choice="none", probabilities={"none": 1}),
            }
        )

    brain = JevEconomyReflex("test", client=SimpleNamespace(system_one=system_one))
    answer = await brain.decide(observation(snap=make_snapshot(enemies_near_base=1)))
    assert answer.mode is WorkerMode.EVACUATE
    assert all(isinstance(v, str) for v in captured[0]["state"].values())
    assert set(captured[0]["questions"]["workers"].criteria) == {"evacuate"}
    assert set(captured[0]["questions"]["growth"].criteria) == {"none"}


def test_one_spending_path_preserves_production_and_tech_commitments():
    assert economy_investment(Invest.TECHLAB, Invest.EXPAND) is Invest.TECHLAB
    assert economy_investment(Invest.NONE, Invest.EXPAND) is Invest.EXPAND
    assert economy_investment(Invest.EXPAND, Invest.REFINERY) is Invest.REFINERY


@pytest.mark.parametrize("value", [-1, float("nan")])
def test_worker_intervals_are_positive_and_finite(value):
    with pytest.raises(ValueError):
        Config(economy_interval_s=value)
