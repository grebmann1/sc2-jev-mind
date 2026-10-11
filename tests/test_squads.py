import asyncio
from dataclasses import replace

import pytest
from conftest import make_snapshot

from sc2mind.actions import Focus, MicroMode, Role, legal_squad_modes
from sc2mind.config import Config
from sc2mind.intelligence import Contact, Intelligence
from sc2mind.mind import Mind
from sc2mind.squads import JevSquadReflex, SquadDecision, SquadObservation, SquadRoster


def observation(role=Role.MAIN, **updates):
    return replace(SquadObservation(role, (1, 2), 0, 12, 1, 1, (), "enemy_base", True), **updates)


class Brain:
    def __init__(self, mode=MicroMode.HOLD, blocked=False):
        self.mode, self.blocked = mode, blocked
        self.calls = self.closed = self.cancelled = 0
        self.gate = asyncio.Event()

    async def decide(self, o):
        self.calls += 1
        try:
            if self.blocked:
                await self.gate.wait()
            return SquadDecision(self.mode, 0.9)
        finally:
            self.cancelled += 1

    async def close(self):
        self.closed += 1


def make_mind(brains, **config):
    return Mind(Config(**config), object(), object(), squad_reflexes=brains)


async def test_parallel_roles_do_not_wait_for_slow_scout_or_overlap_themselves():
    brains = {role: Brain(blocked=role is Role.SCOUT) for role in Role}
    mind = make_mind(brains)
    obs = [observation(role, tags=(i,)) for i, role in enumerate(Role, 1)]
    try:
        result = mind.tick_squads(obs)
        assert all(d.mode is MicroMode.ADVANCE for d in result.values())
        await asyncio.sleep(0)
        result = mind.tick_squads([replace(o, time_s=0.2) for o in obs])
        assert result[Role.MAIN].mode is MicroMode.HOLD
        assert result[Role.HARASS].mode is MicroMode.HOLD
        assert result[Role.SCOUT].mode is MicroMode.ADVANCE
        mind.tick_squads([replace(o, time_s=4) for o in obs])
        await asyncio.sleep(0)
        assert brains[Role.SCOUT].calls == 1
        assert brains[Role.MAIN].calls == brains[Role.HARASS].calls == 2
    finally:
        await mind.close()
    assert all(b.closed == 1 for b in brains.values())
    assert mind._squad_tasks == {}


async def test_answer_for_old_membership_cannot_control_new_units():
    events = []
    mind = make_mind({Role.MAIN: Brain(MicroMode.RETREAT)})
    mind.trace = lambda kind, data: events.append(kind)
    try:
        mind.tick_squads([observation()])
        await asyncio.sleep(0)
        result = mind.tick_squads([observation(tags=(3,), time_s=0.2)])
        assert result[Role.MAIN].mode is MicroMode.ADVANCE
        assert "squad_discarded" in events
    finally:
        await mind.close()


async def test_accepted_answer_expires_during_provider_outage():
    brain = Brain()
    mind = make_mind({Role.MAIN: brain})
    try:
        mind.tick_squads([observation()])
        await asyncio.sleep(0)
        assert mind.tick_squads([observation(time_s=0.1)])[Role.MAIN].mode is MicroMode.HOLD
        brain.blocked = True
        mind.tick_squads([observation(time_s=3)])
        await asyncio.sleep(0)
        assert mind.tick_squads([observation(time_s=8)])[Role.MAIN].mode is MicroMode.ADVANCE
    finally:
        await mind.close()


async def test_current_danger_overrides_accepted_harassment_choice():
    mind = make_mind({Role.HARASS: Brain(MicroMode.ENGAGE)})
    o = observation(Role.HARASS, targets=(Focus.WORKERS,))
    try:
        mind.tick_squads([o])
        await asyncio.sleep(0)
        assert mind.tick_squads([replace(o, time_s=0.1)])[Role.HARASS].mode is MicroMode.ENGAGE
        for changes in [
            {"base_threatened": True},
            {"away_from_home": True, "enemy_power": 50},
            {"health": 0.2},
        ]:
            result = mind.tick_squads([replace(o, time_s=0.2, **changes)])
            assert result[Role.HARASS].mode is MicroMode.RETREAT
    finally:
        await mind.close()


async def test_error_in_one_brain_preserves_other_brains_and_fallback():
    class Failed:
        async def decide(self, o):
            raise RuntimeError("provider unavailable")

    events = []
    mind = make_mind({Role.MAIN: Failed(), Role.SCOUT: Brain()})
    mind.trace = lambda k, d: events.append((k, d))
    try:
        mind.tick_squads([observation(), observation(Role.SCOUT, tags=(3,))])
        await asyncio.sleep(0)
        result = mind.tick_squads(
            [observation(time_s=0.1), observation(Role.SCOUT, tags=(3,), time_s=0.1)]
        )
        assert result[Role.MAIN].mode is MicroMode.ADVANCE
        assert result[Role.SCOUT].mode is MicroMode.HOLD
        assert next(d for k, d in events if k == "squad_error")["role"] == "main"
    finally:
        await mind.close()


async def test_squad_timeout_and_disband_cancel_requests():
    brain = Brain(blocked=True)
    mind = make_mind({Role.SCOUT: brain}, s1_timeout_s=0.001)
    events = []
    mind.trace = lambda k, d: events.append(k)
    try:
        mind.tick_squads([observation(Role.SCOUT)])
        await asyncio.sleep(0.01)
        mind.tick_squads([observation(Role.SCOUT, time_s=0.1)])
        assert "squad_error" in events
        mind.tick_squads([observation(Role.SCOUT, time_s=3)])
        await asyncio.sleep(0)
        mind.tick_squads([])
        await asyncio.sleep(0)
        mind.tick_squads([])
        assert mind._squad_tasks == {}
    finally:
        await mind.close()


def test_scout_cannot_choose_combat_and_hidden_threat_forces_retreat():
    o = observation(Role.SCOUT, targets=(Focus.THREATS,))
    assert MicroMode.ENGAGE not in legal_squad_modes(o)
    assert legal_squad_modes(replace(o, away_from_home=True, unseen_threat=True)) == [
        MicroMode.RETREAT
    ]


async def test_jev_squad_uses_flat_state_and_only_legal_choices():
    from typesafe_sdk._core.response_types import SystemOneResponse

    class Client:
        async def system_one(self, state, questions, timeout):
            assert all(isinstance(v, str) for v in state.values())
            assert set(questions) == {"micro", "focus"}
            assert set(questions["micro"].criteria) == {"hold", "retreat", "advance"}
            return SystemOneResponse.model_validate(
                {
                    "model": "test",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "answers": {
                        "micro": {
                            "type": "choice",
                            "choice": "advance",
                            "confidence": 0.8,
                            "probabilities": {"advance": 0.8, "hold": 0.2},
                        },
                        "focus": {
                            "type": "choice",
                            "choice": "any",
                            "confidence": 1,
                            "probabilities": {"any": 1},
                        },
                    },
                }
            )

    d = await JevSquadReflex("test", client=Client()).decide(observation(Role.SCOUT))
    assert d == SquadDecision(MicroMode.ADVANCE, 0.8)


def test_roster_is_exclusive_stable_and_recalls_surplus_for_defense():
    roster = SquadRoster()
    s = make_snapshot(workers=20)
    assert roster.assign([], [], [50, 51], s)[Role.SCOUT] == (50,)
    before = roster.assign(list(range(1, 21)), [30, 31], [50, 51], s)
    assert len(before[Role.HARASS]) == 4
    assert set(before[Role.MAIN]).isdisjoint(before[Role.HARASS])
    assert len({tag for tags in before.values() for tag in tags}) == 22
    after = roster.assign(list(range(1, 22)), [30, 31], [50, 51], s)
    assert after[Role.SCOUT] == before[Role.SCOUT]
    assert after[Role.HARASS] == before[Role.HARASS]
    defended = roster.assign(
        list(range(1, 22)), [30, 31], [50, 51], make_snapshot(enemies_near_base=1)
    )
    assert defended[Role.SCOUT] == defended[Role.HARASS] == ()
    assert len(defended[Role.MAIN]) == 23


def test_observed_movement_and_staleness_are_distinct_from_intent():
    intel = Intelligence()
    intel.observe(0, [Contact(1, "Marine", (20, 0), 0, 1, dangerous=True)], [(0, 0)])
    intel.observe(2, [Contact(1, "Marine", (15, 0), 2, 1, dangerous=True)], [(0, 0)])
    assert intel.report(2)["movement"] == "toward_our_bases"
    assert intel.report(40)["movement"] == "unknown"
    assert intel.report(40)["recent_supply"] == 0
    assert "do not establish" in intel.report(40)["uncertainty"]
    intel.observe(123, [], [])
    assert intel.contacts == {}


def test_enemy_worker_movement_does_not_report_an_army_advance():
    intel = Intelligence()
    intel.observe(0, [Contact(1, "SCV", (20, 0), 0, worker=True, dangerous=True)], [(0, 0)])
    intel.observe(2, [Contact(1, "SCV", (10, 0), 2, worker=True, dangerous=True)], [(0, 0)])
    assert intel.report(2)["movement"] == "unknown"
    assert intel.report(2)["recent_army"] == {}


@pytest.mark.parametrize("value", [-1, float("nan")])
def test_squad_config_rejects_invalid_intervals(value):
    with pytest.raises(ValueError):
        Config(squad_interval_s=value)
