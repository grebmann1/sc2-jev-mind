import asyncio

from conftest import make_snapshot

from sc2mind.actions import Invest, Stance
from sc2mind.config import Config
from sc2mind.mind import Mind
from sc2mind.system1 import Decision, build_questions
from sc2mind.system2 import DEFAULT_PLAN


class FakeReflex:
    def __init__(self, decision: Decision | Exception) -> None:
        self.decision = decision
        self.calls = 0

    async def decide(self, snap, plan, memory=""):
        self.calls += 1
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class FakeReasoner:
    def __init__(self) -> None:
        self.whys: list[str] = []

    async def plan(self, snap, prev, recent, why, memory=None):
        self.whys.append(why)
        return DEFAULT_PLAN.model_copy(update={"summary": f"plan for {why}"})


async def run_ticks(mind: Mind, times: list[float], **snap_over):
    out = None
    for t in times:
        out = mind.tick(make_snapshot(time_s=t, **snap_over))
        await asyncio.sleep(0)  # let background tasks finish
    return out


async def test_accepts_confident_decision():
    d = Decision(Invest.EXPAND, 0.9, Stance.ATTACK, 0.8, replan_p=0.1)
    mind = Mind(Config(), FakeReflex(d), FakeReasoner())
    invest, stance = await run_ticks(mind, [0, 0.5, 1.0])
    assert (invest, stance) == (Invest.EXPAND, Stance.ATTACK)


async def test_low_confidence_uses_bootstrap_before_first_accepted_answer():
    d = Decision(Invest.EXPAND, 0.2, Stance.ATTACK, 0.3, replan_p=0.0)
    mind = Mind(Config(), FakeReflex(d), FakeReasoner())
    invest, stance = await run_ticks(mind, [0, 0.5, 1.0])
    assert (invest, stance) == (Invest.TECHLAB, Stance.RALLY)


async def test_failure_keeps_previous_and_traces():
    events = []
    mind = Mind(
        Config(),
        FakeReflex(RuntimeError("boom")),
        FakeReasoner(),
        trace=lambda k, d: events.append(k),
    )
    await run_ticks(mind, [0, 0.5])
    assert "s1_error" in events
    assert mind.stance is Stance.RALLY


async def test_s1_respects_interval():
    reflex = FakeReflex(Decision(Invest.NONE, 1, Stance.RALLY, 1, 0))
    mind = Mind(Config(s1_interval_s=2.0), reflex, FakeReasoner())
    await run_ticks(mind, [0, 0.5, 1.0, 1.5, 2.0, 2.5])
    assert reflex.calls == 2


async def test_replan_noul_wakes_system2_early():
    reasoner = FakeReasoner()
    d = Decision(Invest.NONE, 1, Stance.RALLY, 1, replan_p=0.95)
    mind = Mind(Config(s2_interval_s=100, s2_min_interval_s=10), FakeReflex(d), reasoner)
    await run_ticks(mind, [0, 1, 5, 11, 12])
    assert reasoner.whys[0] == "game start"
    assert reasoner.whys[1].startswith("System 1 flagged divergence")
    assert mind.plan.summary.startswith("plan for System 1")


async def test_safety_override_applies_on_output():
    d = Decision(Invest.NONE, 1, Stance.ATTACK, 1, 0)
    mind = Mind(Config(), FakeReflex(d), FakeReasoner())
    await run_ticks(mind, [0, 0.5])
    _, stance = mind.tick(make_snapshot(time_s=0.6, enemies_near_base=5, enemy_army_supply_est=20))
    assert stance is Stance.DEFEND
    assert mind.stance is Stance.ATTACK  # the model's decision is not overwritten


def test_questions_only_offer_legal_options():
    qs = build_questions(make_snapshot(army={}, army_supply=0))
    assert set(qs) == {"invest", "stance", "replan"}


async def test_jev_reflex_parses_sdk_response():
    from typesafe_sdk._core.response_types import SystemOneResponse

    from sc2mind.system1 import JevReflex

    resp = SystemOneResponse.model_validate(
        {
            "model": "jev-test",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "answers": {
                "invest": {
                    "type": "choice",
                    "choice": "expand",
                    "confidence": 0.8,
                    "probabilities": {"expand": 0.8, "none": 0.2},
                },
                "stance": {
                    "type": "choice",
                    "choice": "rally",
                    "confidence": 0.6,
                    "probabilities": {"rally": 0.6, "attack": 0.4},
                },
                "replan": {"type": "noul", "noul": 0.3},
            },
        }
    )

    class FakeClient:
        async def system_one(self, state, questions, timeout):
            assert "strategic_plan" in state and set(questions) == {"invest", "stance", "replan"}
            assert state["experience_memory"] == "Previous match: supply blocked."
            return resp

    d = await JevReflex("jev-test", client=FakeClient()).decide(
        make_snapshot(), DEFAULT_PLAN, "Previous match: supply blocked."
    )
    assert (d.invest, d.invest_p, d.stance, d.replan_p) == (Invest.EXPAND, 0.8, Stance.RALLY, 0.3)


async def test_illegal_answers_are_not_applied():
    decision = Decision(Invest.STIM, 1, Stance.DEFEND, 1, 0)
    mind = Mind(Config(), FakeReflex(decision), FakeReasoner())
    try:
        assert await run_ticks(mind, [0, 0.1]) == (Invest.TECHLAB, Stance.RALLY)
    finally:
        await mind.close()


async def test_previous_answers_are_revalidated_each_frame():
    mind = Mind(
        Config(), FakeReflex(Decision(Invest.EXPAND, 1, Stance.ATTACK, 1, 0)), FakeReasoner()
    )
    try:
        await run_ticks(mind, [0, 0.1])
        invest, stance = mind.tick(make_snapshot(time_s=0.2, townhalls_pending=1, army_supply=0))
        assert (invest, stance) == (Invest.NONE, Stance.RALLY)
    finally:
        await mind.close()


async def test_old_system1_answer_is_discarded():
    events = []
    mind = Mind(
        Config(s1_max_age_s=4),
        FakeReflex(Decision(Invest.EXPAND, 1, Stance.ATTACK, 1, 0)),
        FakeReasoner(),
        trace=lambda kind, data: events.append(kind),
    )
    try:
        assert await run_ticks(mind, [0, 5]) == (Invest.TECHLAB, Stance.RALLY)
        assert "s1_discarded" in events
    finally:
        await mind.close()


async def test_stalled_model_times_out_without_blocking_tick():
    class StalledReflex:
        async def decide(self, snap, plan, memory=""):
            await asyncio.Event().wait()

    events = []
    mind = Mind(
        Config(s1_timeout_s=0.001),
        StalledReflex(),
        FakeReasoner(),
        trace=lambda kind, data: events.append(kind),
    )
    try:
        assert mind.tick(make_snapshot(time_s=0)) == (Invest.TECHLAB, Stance.RALLY)
        await asyncio.sleep(0.01)
        mind.tick(make_snapshot(time_s=0.1))
        assert "s1_error" in events
        assert mind._s1 is None
    finally:
        await mind.close()


async def test_pending_calls_do_not_overlap_and_close_awaits_cancellation():
    class StalledReflex:
        calls = 0
        cancelled = False
        closed = False

        async def decide(self, snap, plan, memory=""):
            self.calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

        async def close(self):
            self.closed = True

    reflex = StalledReflex()
    mind = Mind(Config(), reflex, FakeReasoner())
    await run_ticks(mind, [0, 1, 2, 3])
    assert reflex.calls == 1
    await mind.close()
    assert reflex.cancelled and reflex.closed
    assert mind._s1 is mind._s2 is None
    await mind.close()


async def test_invalid_reasoner_plan_is_traced_and_previous_plan_retained():
    class InvalidReasoner:
        async def plan(self, *args):
            return DEFAULT_PLAN.model_copy(update={"target_workers": 500})

    events = []
    mind = Mind(
        Config(),
        FakeReflex(Decision(Invest.NONE, 1, Stance.RALLY, 1, 0)),
        InvalidReasoner(),
        trace=lambda kind, data: events.append(kind),
    )
    try:
        await run_ticks(mind, [0, 0.1])
        assert "s2_error" in events
        assert mind.plan.target_workers == DEFAULT_PLAN.target_workers
    finally:
        await mind.close()


async def test_old_system2_plan_is_discarded():
    events = []
    mind = Mind(
        Config(s2_max_age_s=4),
        FakeReflex(Decision(Invest.NONE, 1, Stance.RALLY, 1, 0)),
        FakeReasoner(),
        trace=lambda kind, data: events.append(kind),
    )
    try:
        await run_ticks(mind, [0, 5])
        assert "s2_discarded" in events
        assert mind.plan.summary == DEFAULT_PLAN.summary
    finally:
        await mind.close()


async def test_model_receives_an_isolated_snapshot():
    observed = []

    class CaptureReflex:
        async def decide(self, snap, plan, memory=""):
            observed.append(snap.workers)
            return Decision(Invest.NONE, 1, Stance.RALLY, 1, 0)

    mind = Mind(Config(), CaptureReflex(), FakeReasoner())
    snap = make_snapshot(time_s=0)
    mind.tick(snap)
    snap.workers = 99
    await asyncio.sleep(0)
    await mind.close()
    assert observed == [20]


async def test_provider_outage_after_opening_none_still_starts_barracks():
    reflex = FakeReflex(Decision(Invest.NONE, 1, Stance.RALLY, 1, 0))
    mind = Mind(Config(), reflex, FakeReasoner())
    try:
        await run_ticks(mind, [0, 0.1], barracks=0, barracks_bare=0, depots=0, army_supply=0)
        reflex.decision = RuntimeError("provider unavailable")
        await run_ticks(mind, [2, 2.1], barracks=0, barracks_bare=0, depots=1, army_supply=0)
        invest, _ = mind.tick(make_snapshot(time_s=9, barracks=0, barracks_bare=0, army_supply=0))
        assert invest is Invest.BARRACKS
    finally:
        await mind.close()


async def test_low_confidence_after_confident_answer_retains_previous():
    reflex = FakeReflex(Decision(Invest.EXPAND, 1, Stance.ATTACK, 1, 0))
    mind = Mind(Config(), reflex, FakeReasoner())
    try:
        await run_ticks(mind, [0, 0.1])
        reflex.decision = Decision(Invest.NONE, 0.1, Stance.RETREAT, 0.1, 0)
        assert await run_ticks(mind, [2, 2.1]) == (Invest.EXPAND, Stance.ATTACK)
    finally:
        await mind.close()
