"""Scripted snapshot checks of the real offline Mind, without the SC2 engine.

These scenarios verify decisions, not combat outcomes or win rate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .actions import Invest, Stance
from .config import Config
from .mind import Mind, TraceFn
from .state import Snapshot
from .system1 import OfflineReflex
from .system2 import OfflineReasoner


@dataclass(frozen=True)
class Scenario:
    name: str
    snapshot: Snapshot
    investment: Invest
    stance: Stance


def snapshot(**overrides: Any) -> Snapshot:
    values = dict(
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
    values.update(overrides)
    return Snapshot(**values)


def scenarios() -> list[Scenario]:
    return [
        Scenario(
            "opening supply",
            snapshot(
                time_s=0,
                workers=12,
                supply_cap=15,
                barracks=0,
                barracks_bare=0,
                refineries=0,
                depots=0,
                army={},
                army_supply=0,
            ),
            Invest.NONE,
            Stance.RALLY,
        ),
        Scenario(
            "first barracks",
            snapshot(workers=15, barracks=0, barracks_bare=0, refineries=0),
            Invest.BARRACKS,
            Stance.RALLY,
        ),
        Scenario("first gas", snapshot(workers=16, refineries=0), Invest.REFINERY, Stance.RALLY),
        Scenario("tech lab", snapshot(workers=18), Invest.TECHLAB, Stance.RALLY),
        Scenario(
            "natural expansion", snapshot(techlabs=1, barracks_bare=0), Invest.EXPAND, Stance.RALLY
        ),
        Scenario(
            "production on two bases",
            snapshot(townhalls=2, workers=28, techlabs=1, barracks_bare=0),
            Invest.BARRACKS,
            Stance.RALLY,
        ),
        Scenario(
            "research stim",
            snapshot(
                townhalls=2, workers=28, barracks=4, techlabs=1, barracks_bare=3, refineries=2
            ),
            Invest.STIM,
            Stance.RALLY,
        ),
        Scenario(
            "base under attack",
            snapshot(enemies_near_base=6, enemy_army_supply_est=20, enemy_supply_near_base=12),
            Invest.TECHLAB,
            Stance.DEFEND,
        ),
        Scenario(
            "bio timing attack",
            snapshot(
                townhalls=2,
                workers=36,
                barracks=4,
                techlabs=1,
                reactors=3,
                barracks_bare=0,
                refineries=2,
                stim="done",
                army={"Marine": 30},
                army_supply=30,
            ),
            Invest.ENGBAY,
            Stance.ATTACK,
        ),
        Scenario(
            "fog threat delays attack",
            snapshot(
                townhalls=2,
                workers=36,
                barracks=4,
                techlabs=1,
                reactors=3,
                barracks_bare=0,
                refineries=2,
                stim="done",
                army_supply=30,
                enemy_army_supply_est=50,
            ),
            Invest.ENGBAY,
            Stance.RALLY,
        ),
    ]


async def run_scenarios(trace: TraceFn | None = None) -> dict[str, Any]:
    results = []
    for case in scenarios():
        mind = Mind(Config(offline=True), OfflineReflex(), OfflineReasoner(), trace)
        try:
            # Two decision rounds let S1 observe the freshly returned S2 plan.
            for dt in (0.0, 0.1, 2.1, 2.2):
                snap = snapshot(**case.snapshot.to_dict())
                snap.time_s += dt
                invest, stance = mind.tick(snap)
                await asyncio.sleep(0)
            result = {
                "scenario": case.name,
                "passed": (invest, stance) == (case.investment, case.stance),
                "expected": {"invest": case.investment.value, "stance": case.stance.value},
                "actual": {"invest": invest.value, "stance": stance.value},
                "plan": mind.plan.model_dump(mode="json"),
            }
            if trace:
                trace("scenario", result)
            results.append(result)
        finally:
            await mind.close()
    return {
        "mode": "scripted snapshots (not an SC2 match)",
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "scenarios": results,
    }
