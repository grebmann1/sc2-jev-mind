import json
from pathlib import Path

from sc2mind.benchmark import main, match_schedule, summarize


def test_schedule_repeats_each_race_with_reproducible_seeds():
    schedule = match_schedule(["terran", "zerg"], ["Easy", "Medium"], 2)
    assert len(schedule) == 8
    assert schedule[0] == {"enemy": "terran", "difficulty": "Easy", "seed": 1}
    assert schedule[-1] == {"enemy": "zerg", "difficulty": "Medium", "seed": 2}


def test_launch_failures_are_not_counted_as_played_matches():
    result = summarize(
        [{"result": "Victory"}, {"result": "Defeat"}, {"result": "Tie"}, {"exit_code": 2}]
    )
    assert result == {"attempted": 4, "completed": 3, "wins": 1, "win_rate": 1 / 3, "errors": 1}
    assert summarize([])["win_rate"] is None


def test_cross_map_schedule_uses_held_out_seeds(capsys):
    assert main(["--dry-run", "--maps", "MapA", "MapB", "--seed-start", "101", "--games", "3"]) == 0
    matches = json.loads(capsys.readouterr().out)["matches"]
    assert len(matches) == 18
    assert {m["seed"] for m in matches} == {101, 102, 103}
    assert {m["map"] for m in matches} == {"MapA", "MapB"}


def test_dry_run_does_not_require_game_or_keys(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["--dry-run", "--games", "2"]) == 0
    assert len(json.loads(capsys.readouterr().out)["matches"]) == 6


def test_benchmark_saves_each_match_and_summary(tmp_path, monkeypatch):
    import sc2mind.benchmark as benchmark
    import sc2mind.doctor as doctor

    monkeypatch.setattr(doctor, "preflight", lambda *args: [])

    def fake_play(args):
        directory = Path(args[args.index("--runs-dir") + 1]) / "game"
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({"result": "Victory"}))
        assert "--no-realtime" in args
        assert "--no-memory" in args
        return 0

    monkeypatch.setattr(benchmark, "play", fake_play)
    assert main(["--offline", "--games", "1", "--races", "zerg", "--runs-dir", str(tmp_path)]) == 0
    report = json.loads(next(tmp_path.glob("*/benchmark.json")).read_text())
    assert report["wins"] == report["completed"] == report["planned"] == 1
    assert report["win_rate"] == 1.0
