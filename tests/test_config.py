import pytest

from sc2mind.config import Config


@pytest.mark.parametrize(
    "field,value",
    [
        ("s1_interval_s", 0),
        ("s1_interval_s", float("nan")),
        ("s2_timeout_s", -1),
        ("s1_max_age_s", float("inf")),
        ("s1_min_confidence", -0.1),
        ("s1_replan_threshold", 1.01),
        ("s1_min_confidence", float("nan")),
        ("jev_model", ""),
        ("openai_model", " "),
        ("s2_min_interval_s", 50),
    ],
)
def test_rejects_invalid_configuration(field, value):
    with pytest.raises(ValueError):
        Config(**{field: value})


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("S1_TIMEOUT_S", "1.5")
    monkeypatch.setenv("OPENAI_MODEL", "example-model")
    monkeypatch.setenv("ECONOMY_INTERVAL_S", "5")
    cfg = Config.from_env(offline=True)
    assert (cfg.offline, cfg.s1_timeout_s, cfg.openai_model) == (True, 1.5, "example-model")
    assert cfg.economy_interval_s == 5


@pytest.mark.parametrize(
    "env,field",
    [("SC2MIND_MULTI_SQUAD", "multi_squad"), ("SC2MIND_ECONOMY_BRAIN", "economy_brain")],
)
def test_brain_environment_is_strict(monkeypatch, env, field):
    monkeypatch.setenv(env, "false")
    assert not getattr(Config.from_env(), field)
    monkeypatch.setenv(env, "invalid")
    with pytest.raises(ValueError):
        Config.from_env()
