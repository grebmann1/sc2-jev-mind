import json
from types import SimpleNamespace

import pytest
from conftest import make_snapshot

from sc2mind.system2 import DEFAULT_PLAN, OfflineReasoner, OpenAIReasoner, Plan


async def test_openai_uses_responses_parse_and_plan_schema():
    captured = {}

    async def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=DEFAULT_PLAN)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    reasoner = OpenAIReasoner("test-model", client=client)
    recent = [{"t": i} for i in range(20)]
    memory = {
        "comparable_games": 4,
        "episodes": [{"result": "Defeat", "facts": ["Supply blocked"]}],
    }
    result = await reasoner.plan(make_snapshot(), DEFAULT_PLAN, recent, "base lost", memory)
    assert result is DEFAULT_PLAN
    assert captured["text_format"] is Plan
    assert captured["model"] == "test-model"
    payload = json.loads(captured["input"])
    assert payload["trigger"] == "base lost"
    assert payload["recent_system1_decisions"] == recent[-10:]
    assert payload["snapshot"]["workers"] == 20
    assert payload["experience_memory"] == memory


async def test_unparsed_output_is_an_error():
    async def parse(**kwargs):
        return SimpleNamespace(output_parsed=None)

    reasoner = OpenAIReasoner(
        "test", client=SimpleNamespace(responses=SimpleNamespace(parse=parse))
    )
    with pytest.raises(ValueError, match="structured Plan"):
        await reasoner.plan(make_snapshot(), DEFAULT_PLAN, [], "test")


async def test_offline_reasoner_scales_workers_and_preserves_default():
    reasoner = OfflineReasoner()
    before = DEFAULT_PLAN.model_dump()
    plan = await reasoner.plan(
        make_snapshot(townhalls=3, enemy_race="Protoss"), DEFAULT_PLAN, [], "test"
    )
    assert plan.target_workers == 66
    assert plan.marauder_ratio == 0.35
    assert DEFAULT_PLAN.model_dump() == before
    plan.investment_priorities.clear()
    assert DEFAULT_PLAN.investment_priorities


@pytest.mark.parametrize(
    "update",
    [
        {"target_workers": 100},
        {"marauder_ratio": 1.5},
        {"attack_at_army_supply": 201},
        {"summary": "x" * 281},
        {"investment_priorities": []},
        {"unknown": "bad"},
    ],
)
def test_plan_schema_bounds(update):
    with pytest.raises(ValueError):
        Plan.model_validate({**DEFAULT_PLAN.model_dump(), **update})
