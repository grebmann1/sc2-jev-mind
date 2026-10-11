import pytest

from sc2mind.engine import engine_runner, resolve_engine_runner


def test_unconfigured_runner_preserves_sdk_launch(monkeypatch):
    import sc2.paths

    monkeypatch.delenv("SC2_ENGINE_RUNNER", raising=False)
    original = sc2.paths.get_runner_args
    with engine_runner():
        assert sc2.paths.get_runner_args is original
    assert resolve_engine_runner() is None


def test_runner_is_restored_after_game_failure(tmp_path, monkeypatch):
    import sc2.paths

    runner = tmp_path / "qemu"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    monkeypatch.setenv("SC2_ENGINE_RUNNER", str(runner))
    monkeypatch.setattr(sc2.paths, "PF", "Linux")
    original = sc2.paths.get_runner_args
    with pytest.raises(RuntimeError, match="game failed"), engine_runner():
        assert sc2.paths.get_runner_args(None) == [str(runner)]
        raise RuntimeError("game failed")
    assert sc2.paths.get_runner_args is original


def test_missing_runner_fails_preflight(tmp_path, monkeypatch):
    from sc2mind.config import Config
    from sc2mind.doctor import preflight

    monkeypatch.setenv("SC2_ENGINE_RUNNER", str(tmp_path / "missing"))
    monkeypatch.setenv("SC2PATH", str(tmp_path))
    check = next(
        c for c in preflight("AcropolisLE", Config(offline=True)) if c.name == "engine_runner"
    )
    assert not check.ok
    assert "not an executable" in check.detail
