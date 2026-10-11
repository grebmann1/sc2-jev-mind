import sqlite3
import threading
from dataclasses import replace

import pytest
from conftest import make_snapshot

from sc2mind.config import Config
from sc2mind.learning import OPENINGS, choose_opening, opening_plan
from sc2mind.memory import Episode, MatchContext, MemoryStore, jev_summary
from sc2mind.mind import Mind
from sc2mind.system1 import OfflineReflex
from sc2mind.system2 import DEFAULT_PLAN, OfflineReasoner


def context(**changes):
    return replace(
        MatchContext("AcropolisLE", "Zerg", "Medium", "offline-v2", "Base75689", False, 1200),
        **changes,
    )


def record(match_id="match-1", opening="balanced", result="Victory", ctx=None):
    episode = Episode(match_id, ctx or context(), opening)
    episode.observe(make_snapshot(), opening_plan(opening).model_dump(mode="json"))
    return episode.record(result, DEFAULT_PLAN.model_dump(mode="json"))


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "matches.sqlite3")
    memory.initialize()
    return memory


def test_completed_match_persists_and_duplicate_finish_does_not_double_count(store):
    assert store.save(record())
    assert not store.save(record())
    restored = MemoryStore(store.path)
    experience = restored.retrieve(context())
    assert experience["comparable_games"] == 1
    assert experience["opening_stats"]["balanced"]["wins"] == 1
    assert experience["episodes"][0]["result"] == "Victory"
    assert restored.info()["matches"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"map_name": "OtherMap"},
        {"enemy_race": "Terran"},
        {"difficulty": "Hard"},
        {"controller": "live-models"},
        {"engine": "OtherBuild"},
        {"policy": "v3"},
        {"realtime": True},
        {"time_limit_s": 300},
    ],
)
def test_learning_does_not_mix_incompatible_matches(store, changes):
    store.save(record())
    assert store.retrieve(context(**changes))["comparable_games"] == 0


def test_random_race_pools_context_until_scouting_identifies_enemy(store):
    store.save(record())
    assert store.retrieve(context(enemy_race="Random"))["comparable_games"] == 1
    assert context(enemy_race="Random").for_race("Terran").enemy_race == "Terran"
    assert context(enemy_race="Zerg").for_race("Unknown").enemy_race == "Zerg"


def test_same_time_limit_and_different_seeds_remain_comparable(store):
    store.save(record(ctx=context(seed=1, time_limit_s=1200)))
    assert store.retrieve(context(seed=42, time_limit_s=1200.0))["comparable_games"] == 1


def test_retrieval_is_bounded_and_jev_remains_short_flat_text(store):
    for i in range(8):
        store.save(record(str(i)))
    experience = store.retrieve(context())
    assert len(experience["episodes"]) == 3
    summary = jev_summary(experience)
    assert len(summary) <= 650 and "8 comparable" in summary
    assert "timeline" not in experience["episodes"][0]
    state = make_snapshot().to_jev_state("plan", summary)
    assert state["experience_memory"] == summary
    assert all(isinstance(value, str) for value in state.values())


def test_episode_measures_observations_and_records_factual_lessons():
    episode = Episode("id", context(), "balanced")
    for t in range(13):
        episode.observe(make_snapshot(time_s=t, supply_used=31, enemies_near_base=2), {})
    episode.errors["system1"] += 1
    episode.investments["barracks"] += 1
    metrics = episode.metrics()
    assert metrics["observed_supply_blocked_s"] == metrics["observed_under_threat_s"] == 12
    assert metrics["first_threat_s"] == 0
    assert any("depot timing" in fact for fact in episode.facts())
    episode.observe(make_snapshot(time_s=100), {})
    assert episode.metrics()["observed_supply_blocked_s"] == 12  # gap not invented evidence
    assert episode.record("Defeat", {})["metrics"]["issued_investments"] == {"barracks": 1}


def test_timeline_and_mutable_snapshot_are_isolated_and_bounded():
    episode = Episode("id", context(), "balanced")
    snap = make_snapshot(time_s=0)
    episode.observe(snap, {})
    snap.enemy_army_seen["Fake"] = 99
    for t in range(10, 2100, 10):
        episode.observe(make_snapshot(time_s=t), {})
    assert len(episode.timeline) == 200
    assert "Fake" not in episode._previous.enemy_army_seen


def test_empty_or_unfinished_games_cannot_become_evidence():
    episode = Episode("id", context(), "balanced")
    with pytest.raises(ValueError):
        episode.record("Victory", {})
    episode.observe(make_snapshot(), {})
    with pytest.raises(ValueError):
        episode.record("Undecided", {})


def test_learning_explores_before_using_smoothed_result_scores():
    assert choose_opening({})[0] == "balanced"
    assert choose_opening({"balanced": {"games": 3, "wins": 3}})[0] == "pressure"
    stats = {name: {"games": 3, "wins": 0} for name in OPENINGS}
    stats["macro"]["wins"] = 3
    assert choose_opening(stats)[0] == "macro"
    assert choose_opening(stats, enabled=False)[0] == "balanced"
    stats["macro"]["games"] = 4
    assert choose_opening(stats)[0] == "balanced"  # tenth result schedules exploration


def test_real_persisted_results_change_next_opening(store):
    for name in OPENINGS:
        for i in range(3):
            store.save(record(f"{name}-{i}", name, "Victory" if name == "pressure" else "Defeat"))
    name, _ = choose_opening(MemoryStore(store.path).retrieve(context())["opening_stats"])
    assert name == "pressure"


def test_info_does_not_create_storage_and_future_schema_is_not_overwritten(tmp_path):
    path = tmp_path / "absent" / "memory.sqlite3"
    assert MemoryStore(path).info()["matches"] == 0
    assert not path.parent.exists()
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        MemoryStore(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


async def test_mind_retrieves_on_background_thread_and_saves_once(store):
    store.save(record("past"))
    owner_thread = threading.get_ident()
    threads = []
    retrieve = store.retrieve

    def capture(ctx):
        threads.append(threading.get_ident())
        return retrieve(ctx)

    store.retrieve = capture
    received = []

    class Reasoner:
        async def plan(self, snap, prev, recent, why, memory):
            received.append(memory)
            return prev

    episode = Episode("next", context(), "pressure")
    mind = Mind(
        Config(offline=True),
        OfflineReflex(),
        Reasoner(),
        memory=store,
        episode=episode,
        initial_plan=opening_plan("pressure"),
    )
    try:
        mind.tick(make_snapshot(time_s=0))
        await mind._s2
        assert received[0]["comparable_games"] == 1
        assert received[0]["current_match"]["metrics"]["peak_workers"] == 20
        assert received[0]["opening_plan"]["attack_at_army_supply"] == 24
        assert threads and threads[0] != owner_thread
        await mind.finish_match("Defeat")
        await mind.finish_match("Defeat")
        assert mind.memory_saved and store.info()["matches"] == 2
    finally:
        await mind.close()


async def test_memory_failure_keeps_game_and_model_running(store):
    def fail(ctx):
        raise sqlite3.DatabaseError("broken")

    store.retrieve = fail
    mind = Mind(
        Config(offline=True),
        OfflineReflex(),
        OfflineReasoner(),
        memory=store,
        episode=Episode("next", context(), "balanced"),
    )
    try:
        mind.tick(make_snapshot())
        plan = await mind._s2
        assert plan.target_workers == 44
        assert not mind.experience
    finally:
        await mind.close()


async def test_offline_reasoning_retains_learned_opening_and_recovers_after_threat():
    opening = opening_plan("pressure")
    memory = {"opening_plan": opening.model_dump(mode="json")}
    reasoner = OfflineReasoner()
    threatened = await reasoner.plan(
        make_snapshot(enemy_army_supply_est=50), opening, [], "threat", memory
    )
    assert threatened.aggression == "turtle"
    recovered = await reasoner.plan(make_snapshot(), threatened, [], "clear", memory)
    assert recovered.attack_at_army_supply == 24
    assert recovered.aggression == "aggressive"
