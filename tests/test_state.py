from types import SimpleNamespace

from sc2mind.state import EnemyMemory


def enemy(tag=1, name="Marine", supply=1, hallucination=False):
    return SimpleNamespace(tag=tag, name=name, type_id=supply, is_hallucination=hallucination)


def bot(time=0, units=(), structures=()):
    return SimpleNamespace(
        time=time, enemy_units=units, enemy_structures=structures, calculate_supply_cost=lambda t: t
    )


def test_memory_tracks_tags_instead_of_adding_type_maxima():
    memory = EnemyMemory()
    memory.update(bot(units=[enemy(1), enemy(2)]))
    memory.update(bot(time=10, units=[enemy(2), enemy(3)]))
    assert memory.counts == {"Marine": 3}
    assert memory.supply_est == 3


def test_memory_updates_unit_transform_and_excludes_workers_and_hallucinations():
    memory = EnemyMemory()
    memory.update(bot(units=[enemy(), enemy(2, "SCV"), enemy(3, hallucination=True)]))
    memory.update(bot(time=10, units=[enemy(1, "Marauder", 2)]))
    assert memory.counts == {"Marauder": 1}
    assert memory.supply_est == 2


def test_memory_expires_army_and_forgets_confirmed_deaths():
    memory = EnemyMemory(max_age_s=30)
    memory.update(bot(units=[enemy(1), enemy(2)], structures=[enemy(100)]))
    memory.forget(1)
    memory.forget(100)
    assert memory.counts == {"Marine": 1}
    assert memory.structures == set()
    memory.update(bot(time=31))
    assert memory.supply_est == 0


def test_unseen_structures_remain_in_memory_until_confirmed_destroyed():
    memory = EnemyMemory()
    memory.update(bot(structures=[enemy(100)]))
    memory.update(bot(time=1000))
    assert memory.structures == {100}


def test_random_enemy_race_is_remembered_after_units_leave_vision():
    memory = EnemyMemory()
    unit = enemy()
    unit.race = SimpleNamespace(name="Protoss")
    memory.update(bot(units=[unit]))
    memory.update(bot(time=10))
    assert memory.race == "Protoss"
