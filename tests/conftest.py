import pytest

from sc2mind.state import Snapshot


def make_snapshot(**over) -> Snapshot:
    base = dict(
        time_s=120.0,
        minerals=300,
        gas=50,
        supply_used=24,
        supply_cap=31,
        workers=20,
        townhalls=1,
        townhalls_pending=0,
        barracks=1,
        barracks_pending=0,
        barracks_bare=1,
        techlabs=0,
        reactors=0,
        refineries=1,
        engbays=0,
        stim="none",
        weapons1="none",
        army={"Marine": 4},
        army_supply=4,
        enemy_race="Zerg",
    )
    base.update(over)
    return Snapshot(**base)


@pytest.fixture
def snap() -> Snapshot:
    return make_snapshot()
