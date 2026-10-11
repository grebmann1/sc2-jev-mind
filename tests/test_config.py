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
    cfg = Config.from_env(offline=True)
    assert (cfg.offline, cfg.s1_timeout_s, cfg.openai_model) == (True, 1.5, "example-model")


def test_multi_squad_environment_is_strict(monkeypatch):
    monkeypatch.setenv("SC2MIND_MULTI_SQUAD", "false")
    assert not Config.from_env().multi_squad
    monkeypatch.setenv("SC2MIND_MULTI_SQUAD", "invalid")
    with pytest.raises(ValueError):
        Config.from_env()
