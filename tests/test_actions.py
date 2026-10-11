from conftest import make_snapshot

from sc2mind.actions import Invest, Stance, legal_investments, legal_stances, safety_override


def test_none_is_always_legal(snap):
    assert Invest.NONE in legal_investments(snap)


def test_tech_requires_prerequisites():
    early = make_snapshot(barracks=0, barracks_bare=0, refineries=0, supply_cap=15)
    legal = legal_investments(early)
    for a in (
        Invest.BARRACKS,
        Invest.TECHLAB,
        Invest.REACTOR,
        Invest.STIM,
        Invest.ENGBAY,
        Invest.WEAPONS1,
    ):
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


def test_pending_refineries_and_engineering_bay_are_not_duplicated():
    s = make_snapshot(refineries=1, refineries_pending=1, engbays_pending=1)
    legal = legal_investments(s)
    assert Invest.REFINERY not in legal
    assert Invest.ENGBAY not in legal


def test_supply_cap_does_not_replace_completed_depot_prerequisite():
    assert Invest.BARRACKS not in legal_investments(make_snapshot(supply_cap=30, depots=0))


def test_local_threat_does_not_use_entire_remembered_army():
    s = make_snapshot(
        enemies_near_base=1, enemy_supply_near_base=1, enemy_army_supply_est=100, army_supply=10
    )
    assert safety_override(s, Stance.ATTACK) is Stance.ATTACK


def test_no_army_remains_rally_even_under_attack():
    assert (
        safety_override(make_snapshot(enemies_near_base=5, army_supply=0), Stance.ATTACK)
        is Stance.RALLY
    )


def test_known_zero_supply_threat_is_not_replaced_by_distant_army():
    s = make_snapshot(enemies_near_base=1, enemy_supply_near_base=0, enemy_army_supply_est=100)
    assert safety_override(s, Stance.ATTACK) is Stance.ATTACK
