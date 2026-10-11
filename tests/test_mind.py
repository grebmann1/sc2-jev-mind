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

    async def decide(self, snap, plan):
        self.calls += 1
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class FakeReasoner:
    def __init__(self) -> None:
        self.whys: list[str] = []

    async def plan(self, snap, prev, recent, why):
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


async def test_low_confidence_keeps_previous():
    d = Decision(Invest.EXPAND, 0.2, Stance.ATTACK, 0.3, replan_p=0.0)
    mind = Mind(Config(), FakeReflex(d), FakeReasoner())
    invest, stance = await run_ticks(mind, [0, 0.5, 1.0])
    assert (invest, stance) == (Invest.NONE, Stance.RALLY)


async def test_failure_keeps_previous_and_traces():
    events = []
    mind = Mind(Config(), FakeReflex(RuntimeError("boom")), FakeReasoner(), trace=lambda k, d: events.append(k))
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

    resp = SystemOneResponse.model_validate({
        "model": "jev-test",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "answers": {
            "invest": {"type": "choice", "choice": "expand", "confidence": 0.8,
                       "probabilities": {"expand": 0.8, "none": 0.2}},
            "stance": {"type": "choice", "choice": "rally", "confidence": 0.6,
                       "probabilities": {"rally": 0.6, "attack": 0.4}},
            "replan": {"type": "noul", "noul": 0.3},
        },
    })

    class FakeClient:
        async def system_one(self, state, questions, timeout):
            assert "strategic_plan" in state and set(questions) == {"invest", "stance", "replan"}
            return resp

    d = await JevReflex("jev-test", client=FakeClient()).decide(make_snapshot(), DEFAULT_PLAN)
    assert (d.invest, d.invest_p, d.stance, d.replan_p) == (Invest.EXPAND, 0.8, Stance.RALLY, 0.3)
