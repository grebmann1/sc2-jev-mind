"""The bounded action vocabulary. Code decides what is legal; the models only choose."""

from __future__ import annotations

from enum import StrEnum

from .state import Snapshot


class Invest(StrEnum):
    """One investment priority per System 1 tick. Units, SCVs and depots are code reflexes."""

    EXPAND = "expand"
    BARRACKS = "barracks"
    REFINERY = "refinery"
    TECHLAB = "techlab"
    REACTOR = "reactor"
    ENGBAY = "engbay"
    STIM = "stim"
    WEAPONS1 = "weapons1"
    NONE = "none"


class Stance(StrEnum):
    ATTACK = "attack"
    DEFEND = "defend"
    RALLY = "rally"
    RETREAT = "retreat"


INVEST_DESCRIPTIONS: dict[Invest, str] = {
    Invest.EXPAND: "Build a new command center at the next base (400 minerals)",
    Invest.BARRACKS: "Build another barracks for more unit production (150 minerals)",
    Invest.REFINERY: "Build a refinery to mine gas (75 minerals)",
    Invest.TECHLAB: "Add a tech lab to a barracks: enables marauders and stim (50m/25g)",
    Invest.REACTOR: "Add a reactor to a barracks: doubles marine output (50m/50g)",
    Invest.ENGBAY: "Build an engineering bay for infantry upgrades (125 minerals)",
    Invest.STIM: "Research stimpack at a tech lab (100m/100g)",
    Invest.WEAPONS1: "Research infantry weapons level 1 (100m/100g)",
    Invest.NONE: "No new investment: spend everything on units",
}

STANCE_DESCRIPTIONS: dict[Stance, str] = {
    Stance.ATTACK: "Push the army toward the enemy base and fight",
    Stance.DEFEND: "Fight enemy units near our bases",
    Stance.RALLY: "Gather the army at our forward rally point and wait",
    Stance.RETREAT: "Pull the army back to the main base to avoid a losing fight",
}

MAX_BARRACKS_PER_BASE = 3
MAX_REFINERIES_PER_BASE = 2


def legal_investments(s: Snapshot) -> list[Invest]:
    """Prerequisites only, not affordability: choosing an unaffordable option means 'save for it'."""
    legal: list[Invest] = []
    bases = max(s.townhalls, 1)
    if s.townhalls > 0 and s.townhalls_pending == 0 and s.townhalls < 6:
        legal.append(Invest.EXPAND)
    if (
        s.depots > 0
        and s.supply_cap > 15
        and s.barracks + s.barracks_pending < MAX_BARRACKS_PER_BASE * bases
    ):
        legal.append(Invest.BARRACKS)
    if s.refineries + s.refineries_pending < MAX_REFINERIES_PER_BASE * s.townhalls:
        legal.append(Invest.REFINERY)
    if s.barracks_bare > s.techlabs_pending + s.reactors_pending and s.refineries > 0:
        legal.extend([Invest.TECHLAB, Invest.REACTOR])
    if s.barracks > 0 and s.engbays + s.engbays_pending == 0:
        legal.append(Invest.ENGBAY)
    if s.techlabs > 0 and s.stim == "none":
        legal.append(Invest.STIM)
    if s.engbays > 0 and s.weapons1 == "none":
        legal.append(Invest.WEAPONS1)
    legal.append(Invest.NONE)
    return legal


def legal_stances(s: Snapshot) -> list[Stance]:
    if s.army_supply == 0:
        return [Stance.RALLY]
    stances = [Stance.ATTACK, Stance.RALLY, Stance.RETREAT]
    if s.base_threatened:
        stances.insert(0, Stance.DEFEND)
    return stances


def safety_override(s: Snapshot, stance: Stance) -> Stance:
    """Deterministic policy that outranks both models."""
    if s.army_supply == 0:
        return Stance.RALLY
    if s.base_threatened and stance in (Stance.ATTACK, Stance.RALLY):
        threat_supply = (
            s.enemy_army_supply_est
            if s.enemy_supply_near_base is None
            else s.enemy_supply_near_base
        )
        if s.army_supply <= threat_supply:
            return Stance.DEFEND
    return stance


def baseline_investment(s: Snapshot) -> Invest:
    """A progressing bio build order used by the offline baseline; policy stays in code."""
    legal = legal_investments(s)
    priorities: list[Invest] = []
    barracks = s.barracks + s.barracks_pending
    gas = s.refineries + s.refineries_pending
    if barracks < 1:
        priorities.append(Invest.BARRACKS)
    if barracks > 0 and gas < 1:
        priorities.append(Invest.REFINERY)
    if s.techlabs + s.techlabs_pending < 1 and barracks > 0:
        priorities.append(Invest.TECHLAB)
    if not s.base_threatened and s.workers >= 20 and s.townhalls < 2:
        priorities.append(Invest.EXPAND)
    if barracks < min(2 * max(s.townhalls, 1), 8):
        priorities.append(Invest.BARRACKS)
    priorities.append(Invest.STIM)
    if s.techlabs + s.techlabs_pending > 0 and s.reactors + s.reactors_pending < barracks - 1:
        priorities.append(Invest.REACTOR)
    if barracks > 0 and gas < s.townhalls:
        priorities.append(Invest.REFINERY)
    if s.army_supply >= 16:
        priorities.extend([Invest.ENGBAY, Invest.WEAPONS1])
    if not s.base_threatened and s.workers >= 16 * s.townhalls and s.townhalls < 4:
        priorities.append(Invest.EXPAND)
    return next((a for a in priorities if a in legal), Invest.NONE)
