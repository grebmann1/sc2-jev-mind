import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import make_snapshot

from sc2mind.analysis import Review, analyze_match, evidence_packet, measured_review
from sc2mind.memory import Episode, MatchContext, MemoryStore, general_patterns
from sc2mind.system2 import DEFAULT_PLAN
from sc2mind.trace import JsonlTrace


def episode(match_id="match", **changes):
    context = replace(
        MatchContext("MapA", "Zerg", "Medium", "offline", "Base1", True, 1200, 1), **changes
    )
    item = Episode(match_id, context, "balanced")
    for t in range(201):
        item.observe(
            make_snapshot(
                time_s=t,
                supply_used=31,
                minerals=900,
                townhalls=2 if t >= 150 else 1,
                stim="done" if t >= 180 else "none",
                enemy_race=context.enemy_race,
            ),
            {},
        )
    return item.record("Victory", {})


@pytest.fixture
def run(tmp_path):
    directory = tmp_path / "match"
    directory.mkdir()
    (directory / "result.json").write_text(
        json.dumps({"result": "Victory", "map": "MapA", "enemy": "zerg", "seed": 1})
    )
    trace = JsonlTrace(directory / "trace.jsonl")
    trace("config", {"engine": "Base1"})
    trace("snapshot", make_snapshot(time_s=0).to_dict())
    trace(
        "s1",
        {
            "t": 1,
            "age_s": 1,
            "invest": "barracks",
            "stance": "attack",
            "applied": ["none", "rally"],
        },
    )
    trace(
        "s1",
        {
            "t": 2,
            "age_s": 2,
            "invest": "barracks",
            "stance": "rally",
            "applied": ["barracks", "rally"],
        },
    )
    trace("investment", {"t": 3, "invest": "barracks"})
    trace("s2", {"t": 15, "summary": "Grow bio", "reasoning": "Build capacity"})
    trace("end", {"t": 200, "result": "Victory"})
    trace.close()
    return directory


def test_dense_metrics_exclude_observation_gaps_and_track_milestones():
    record = episode()
    metrics = record["metrics"]
    assert metrics["observed_time_s"] == metrics["observed_mineral_bank_s"] == 200
    assert metrics["first_expansion_s"] == 150 and metrics["first_stim_s"] == 180
    item = Episode("id", MatchContext(**record["context"]), "balanced")
    item.observe(make_snapshot(time_s=0, minerals=900), {})
    item.observe(make_snapshot(time_s=100, minerals=900), {})
    assert item.metrics()["observed_time_s"] == item.metrics()["observed_mineral_bank_s"] == 0


def test_general_signals_require_diversity_and_do_not_count_repeats_as_independent():
    repeated = [episode(str(i)) for i in range(5)]
    signal = general_patterns(repeated)["signals"]["supply_blocks"]
    assert signal["matches"] == 5 and signal["conditions"] == 1
    assert signal["status"] == "provisional"
    varied = [
        episode("1"),
        episode("2", map_name="MapB", seed=2),
        episode("3", map_name="MapB", seed=3, enemy_race="Terran"),
    ]
    signal = general_patterns(varied)["signals"]["supply_blocks"]
    assert signal["status"] == "candidate_for_controlled_test"
    assert general_patterns([varied[0], varied[0]])["matches"] == 1


def test_short_diagnostics_and_unknown_metrics_do_not_become_general_evidence():
    short, old = episode("short"), episode("old")
    short["metrics"]["duration_s"] = 10
    old["metrics"].pop("observed_mineral_bank_s")
    patterns = general_patterns([short, old])
    assert patterns["matches"] == 1
    assert patterns["signals"]["mineral_bank"]["measured_matches"] == 0


def test_cross_map_history_holds_execution_context_fixed(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    for item in [
        episode("a"),
        episode("b", map_name="MapB", enemy_race="Protoss"),
        episode("engine", engine="Other"),
        episode("speed", realtime=False),
        episode("policy", policy="future"),
        episode("controller", controller="live"),
        episode("difficulty", difficulty="Hard"),
        episode("limit", time_limit_s=10),
    ]:
        store.save(item)
    context = MatchContext(**episode()["context"])
    assert {r["match_id"] for r in store.review_history(context)} == {"a", "b"}
    assert store.get("a")["result"] == "Victory"
    assert store.retrieve(context)["general_patterns"]["matches"] == 2
    assert (
        store.retrieve(context)["comparable_games"] == 1
    )  # opening selection stays context-specific


async def test_offline_analysis_preserves_evidence_and_leaves_memory_untouched(run, tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    store.save(episode())
    before = store.info()
    report = await analyze_match(run, memory=store, offline=True)
    assert report["status"] == "offline" and store.info() == before
    assert report["evidence"]["providers"]["s1"]["proposed_applied_differences"] == 1
    assert report["evidence"]["facts"]["execution.issued_investments"] == {"barracks": 1}
    assert report["evidence"]["providers"]["s2"]["p95_game_age_s"] is None
    assert {f["category"] for f in report["review"]["findings"]} == {"supply", "economy"}
    assert "held-out seeds" in (run / "analysis.md").read_text()


async def test_structured_review_uses_only_bounded_packet_and_deduplicates_calls(run):
    captured = []

    async def parse(**kwargs):
        captured.append(kwargs)
        packet = json.loads(kwargs["input"])
        return SimpleNamespace(output_parsed=measured_review(packet))

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    result = await analyze_match(run, record=episode(), client=client, model="test")
    assert result["status"] == "complete"
    assert issubclass(captured[0]["text_format"], Review)
    schema = captured[0]["text_format"].model_json_schema()
    assert set(schema["$defs"]["EvidenceId"]["enum"]) == set(result["evidence"]["facts"])
    assert "request_id" not in captured[0]["input"] and "API_KEY" not in captured[0]["input"]
    summary = json.loads((run / "result.json").read_text())
    (run / "result.json").write_text(json.dumps({**summary, "analysis_status": "complete"}))
    again = await analyze_match(run, record=episode(), client=client)
    assert again == result and len(captured) == 1


@pytest.mark.parametrize("failure", ["timeout", "unknown_evidence", "unparsed"])
async def test_failed_review_retains_measured_report_and_result(run, failure):
    async def parse(**kwargs):
        if failure == "timeout":
            await asyncio.sleep(1)
        if failure == "unparsed":
            return SimpleNamespace(output_parsed=None)
        review = measured_review(json.loads(kwargs["input"]))
        altered = review.model_dump()
        altered["findings"][0]["evidence_ids"] = ["invented.kills"]
        return SimpleNamespace(output_parsed=Review.model_validate(altered))

    before = (run / "result.json").read_bytes()
    report = await analyze_match(
        run,
        record=episode(),
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
        timeout_s=0.01,
    )
    assert report["status"] == "failed"
    assert report["review"]["findings"][0]["evidence_ids"][0] == "metrics.observed_supply_blocked_s"
    assert (run / "result.json").read_bytes() == before
    assert "measured analysis retained" in (run / "analysis.md").read_text()


@pytest.mark.parametrize("result", ["Error", "Undecided", "simulation"])
def test_unfinished_or_non_game_runs_are_not_analysis_evidence(run, result):
    (run / "result.json").write_text(json.dumps({"result": result}))
    with pytest.raises(ValueError, match="completed game"):
        evidence_packet(run, None, None)


async def test_oversized_model_input_is_rejected_without_a_paid_call(run):
    with (run / "trace.jsonl").open("a") as trace:
        trace.write(json.dumps({"kind": "s2", "summary": "test", "reasoning": "x" * 70000}) + "\n")
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)

    report = await analyze_match(
        run, record=episode(), client=SimpleNamespace(responses=SimpleNamespace(parse=parse))
    )
    assert not calls and report["failure_reason"] == "input_size_limit"
    assert (run / "analysis.md").exists()


def test_missing_completion_or_wrong_episode_cannot_create_review(run):
    other = episode("other")
    with pytest.raises(ValueError, match="does not match"):
        evidence_packet(run, other, None)
    (run / "trace.jsonl").write_text(json.dumps({"kind": "snapshot"}) + "\n")
    with pytest.raises(ValueError, match="No observed completed"):
        evidence_packet(run, None, None)


def test_legacy_trace_reports_sparse_coverage_instead_of_inventing_time(run):
    packet = evidence_packet(run, None, None)
    assert packet["facts"]["metrics.observed_time_s"] == 0
    assert any("lower bounds" in warning for warning in packet["warnings"])
    assert packet["general_patterns"]["signals"]["supply_blocks"]["measured_matches"] == 0


def test_cli_runs_analysis_after_observed_game_without_keys(tmp_path, monkeypatch):
    import sc2.main
    import sc2.maps
    from sc2.data import Result

    import sc2mind.cli as cli

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "preflight", lambda *args: [])
    monkeypatch.setattr(sc2.maps, "get", lambda name: object())

    def play(game_map, players, **kwargs):
        bot = players[0].ai
        for t in range(201):
            snap = make_snapshot(time_s=t, supply_used=31)
            bot.mind.episode.observe(snap, DEFAULT_PLAN.model_dump(mode="json"))
            if t in (0, 200):
                bot.tracer("snapshot", snap.to_dict())
        # on_end normally records completion and closes the game clients.
        bot.tracer("end", {"t": 200, "result": "Victory"})
        asyncio.run(bot.mind.finish_match("Victory"))
        asyncio.run(bot.mind.close())
        bot.tracer.close()
        return Result.Victory

    monkeypatch.setattr(sc2.main, "run_game", play)
    assert cli.main(["--offline", "--no-memory", "--runs-dir", str(tmp_path)]) == 0
    folder = next(tmp_path.glob("*/result.json")).parent
    assert json.loads((folder / "result.json").read_text())["analysis_status"] == "offline"
    assert (folder / "analysis.md").exists()


def test_review_failure_does_not_turn_victory_into_failed_match(tmp_path, monkeypatch):
    import sc2.main
    import sc2.maps
    from sc2.data import Result

    import sc2mind.analysis as analysis
    import sc2mind.cli as cli

    monkeypatch.setattr(cli, "preflight", lambda *args: [])
    monkeypatch.setattr(sc2.maps, "get", lambda name: object())

    def play(game_map, players, **kwargs):
        players[0].ai.mind.episode.observe(make_snapshot(), {})
        return Result.Victory

    async def fail(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(sc2.main, "run_game", play)
    monkeypatch.setattr(analysis, "analyze_match", fail)
    assert cli.main(["--offline", "--no-memory", "--runs-dir", str(tmp_path)]) == 0
    result = json.loads(next(tmp_path.glob("*/result.json")).read_text())
    assert result["result"] == "Victory" and result["analysis_status"] == "failed"
