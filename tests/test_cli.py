import json

import pytest

from sc2mind.cli import main
from sc2mind.config import Config
from sc2mind.doctor import preflight
from sc2mind.scenarios import run_scenarios


async def test_scripted_snapshot_suite():
    result = await run_scenarios()
    assert result["passed"] == result["total"] == 10


def test_simulation_without_game_or_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("SC2PATH", str(tmp_path / "missing-game"))
    assert main(["--simulate", "--json", "--runs-dir", str(tmp_path / "runs")]) == 0
    result = json.loads(capsys.readouterr().out)
    report = json.loads(
        (tmp_path / "runs" / result["run_dir"].split("/")[-1] / "result.json").read_text()
    )
    assert report["passed"] == 10
    trace = list((tmp_path / "runs").glob("*/trace.jsonl"))[0]
    assert all(json.loads(line) for line in trace.read_text().splitlines())


def test_doctor_reports_missing_game_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    assert main(["--doctor", "--offline", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert next(c for c in report["checks"] if c["name"] == "sc2")["ok"] is False


def test_doctor_recognizes_game_and_map_without_launching(tmp_path, monkeypatch):
    import sc2mind.doctor as doctor

    executable = tmp_path / "Versions" / "Base90000" / "SC2_x64"
    executable.parent.mkdir(parents=True)
    executable.touch()
    maps = tmp_path / "Maps"
    maps.mkdir()
    (maps / "AcropolisLE.SC2Map").touch()
    monkeypatch.setattr(doctor, "installation_path", lambda: (tmp_path, "SC2_x64"))
    assert all(c.ok for c in preflight("AcropolisLE", Config(offline=True)))


def test_invalid_config_is_actionable(monkeypatch, capsys):
    monkeypatch.setenv("S1_INTERVAL_S", "nope")
    assert main(["--simulate"]) == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_missing_prerequisites_do_not_launch_game(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    assert main(["--offline"]) == 2
    assert "--simulate" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--difficulty", "Impossible"],
        ["--game-time-limit", "-2"],
        ["--seed", "-1"],
        ["--seed", "4294967296"],
    ],
)
def test_invalid_game_args_fail_before_launch(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_match_entry_point_records_seed_and_result_without_game(tmp_path, monkeypatch):
    import sc2.main
    import sc2.maps
    from sc2.data import Result

    import sc2mind.cli as cli

    monkeypatch.setattr(cli, "preflight", lambda *args: [])
    monkeypatch.setattr(sc2.maps, "get", lambda name: object())
    captured = {}

    def run_game(*args, **kwargs):
        captured.update(kwargs)
        return Result.Victory

    monkeypatch.setattr(sc2.main, "run_game", run_game)
    assert (
        main(
            [
                "--offline",
                "--seed",
                "42",
                "--runs-dir",
                str(tmp_path),
                "--memory-dir",
                str(tmp_path / "memory"),
            ]
        )
        == 0
    )
    result = json.loads(next(tmp_path.glob("*/result.json")).read_text())
    assert result["result"] == "Victory"
    assert result["seed"] == captured["random_seed"] == 42
    assert result["game_time_limit"] == 1200
    assert result["memory_saved"] is False  # fake runner supplied no actual observations


def test_memory_info_requires_no_game_keys_or_model_config(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("S1_INTERVAL_S", "invalid")
    assert main(["--memory-info", "--memory-dir", str(tmp_path / "memory")]) == 0
    assert json.loads(capsys.readouterr().out)["matches"] == 0
    assert not (tmp_path / "memory").exists()


def test_scenarios_never_write_match_memory(tmp_path):
    directory = tmp_path / "memory"
    assert (
        main(["--simulate", "--memory-dir", str(directory), "--runs-dir", str(tmp_path / "runs")])
        == 0
    )
    assert not directory.exists()


def test_unexpected_runtime_error_is_reported_without_training(tmp_path, monkeypatch):
    import sc2.main
    import sc2.maps

    import sc2mind.cli as cli

    monkeypatch.setattr(cli, "preflight", lambda *args: [])
    monkeypatch.setattr(sc2.maps, "get", lambda name: object())

    def fail(*args, **kwargs):
        raise AttributeError("game callback failed")

    monkeypatch.setattr(sc2.main, "run_game", fail)
    assert main(["--offline", "--no-memory", "--runs-dir", str(tmp_path)]) == 1
    result = json.loads(next(tmp_path.glob("*/result.json")).read_text())
    assert result["result"] == "Error" and result["error_type"] == "AttributeError"


def test_partial_model_initialization_closes_client(tmp_path, monkeypatch):
    import sc2mind.cli as cli

    monkeypatch.setattr(cli, "preflight", lambda *args: [])
    closed = []

    class Reflex:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            closed.append(True)

    def fail(*args, **kwargs):
        raise ValueError("reasoner setup failed")

    monkeypatch.setattr(cli, "JevReflex", Reflex)
    monkeypatch.setattr(cli, "OpenAIReasoner", fail)
    assert main(["--no-memory", "--runs-dir", str(tmp_path)]) == 1
    assert closed == [True]
