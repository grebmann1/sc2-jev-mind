from conftest import make_snapshot

from sc2mind.actions import Invest, Stance, legal_investments, legal_stances, safety_override


def test_none_is_always_legal(snap):
    assert Invest.NONE in legal_investments(snap)


def test_tech_requires_prerequisites():
    early = make_snapshot(barracks=0, barracks_bare=0, refineries=0, supply_cap=15)
    legal = legal_investments(early)
    for a in (Invest.BARRACKS, Invest.TECHLAB, Invest.REACTOR, Invest.STIM, Invest.ENGBAY, Invest.WEAPONS1):
        assert a not in legal


def test_stim_only_once():
    assert Invest.STIM in legal_investments(make_snapshot(techlabs=1))
    assert Invest.STIM not in legal_investments(make_snapshot(techlabs=1, stim="researching"))


def test_no_double_expand():
    assert Invest.EXPAND not in legal_investments(make_snapshot(townhalls_pending=1))


def test_stances_without_army():
    assert legal_stances(make_snapshot(army={}, army_supply=0)) == [Stance.RALLY]


def test_defend_offered_only_under_threat(snap):
    assert Stance.DEFEND not in legal_stances(snap)
    assert Stance.DEFEND in legal_stances(make_snapshot(enemies_near_base=3))


def test_safety_override_forces_defense_when_outnumbered():
    s = make_snapshot(enemies_near_base=6, enemy_army_supply_est=12, army_supply=4)
    assert safety_override(s, Stance.ATTACK) is Stance.DEFEND
    assert safety_override(s, Stance.RETREAT) is Stance.RETREAT


def test_jev_state_is_flat_strings(snap):
    state = snap.to_jev_state("plan text")
    assert all(isinstance(v, str) for v in state.values())
    assert state["strategic_plan"] == "plan text"
