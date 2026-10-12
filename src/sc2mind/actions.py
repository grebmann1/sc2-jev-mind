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


class Role(StrEnum):
    MAIN = "main"
    SCOUT = "scout"
    HARASS = "harass"


class MicroMode(StrEnum):
    ADVANCE = "advance"
    ENGAGE = "engage"
    KITE = "kite"
    HOLD = "hold"
    RETREAT = "retreat"


class Focus(StrEnum):
    ANY = "any"
    THREATS = "threats"
    WORKERS = "workers"
    STRUCTURES = "structures"


class WorkerMode(StrEnum):
    BALANCE = "balance"
    MINERALS = "minerals"
    GAS = "gas"
    REPAIR = "repair"
    EVACUATE = "evacuate"


WORKER_DESCRIPTIONS = {
    WorkerMode.BALANCE: "Balance mineral saturation with two workers per available refinery",
    WorkerMode.MINERALS: "Prioritize mineral saturation, keeping one worker per refinery",
    WorkerMode.GAS: "Fill refineries to three workers while retaining mineral income",
    WorkerMode.REPAIR: "Allocate a few SCVs to damaged structures and balance remaining miners",
    WorkerMode.EVACUATE: "Withdraw endangered workers and keep safe bases mining",
}
WORKER_REBALANCE_S = 2.0
WORKER_DANGER_RADIUS = 15.0
REPAIR_HEALTH_FLOOR = 0.75
REPAIR_MINERALS = 50
MAX_REPAIR_WORKERS = 3


def legal_worker_modes(o) -> list[WorkerMode]:
    if o.snap.base_threatened:
        return [WorkerMode.EVACUATE]
    modes = [WorkerMode.BALANCE, WorkerMode.MINERALS]
    if o.gas_sites:
        modes.append(WorkerMode.GAS)
    if o.damaged_structures and o.snap.minerals >= REPAIR_MINERALS:
        modes.append(WorkerMode.REPAIR)
    return modes


def legal_economy_priorities(o) -> list[Invest]:
    if o.snap.base_threatened:
        return [Invest.NONE]
    legal = legal_investments(o.snap)
    priorities = [Invest.NONE]
    capacity = sum(site.ideal for site in o.bases)
    if (
        Invest.EXPAND in legal
        and o.snap.workers >= max(16, capacity)
        and o.snap.barracks + o.snap.barracks_pending > 0
    ):
        priorities.append(Invest.EXPAND)
    if Invest.REFINERY in legal and o.snap.barracks > 0 and o.snap.workers >= 14:
        priorities.append(Invest.REFINERY)
    return priorities


def baseline_worker_mode(o) -> WorkerMode:
    legal = legal_worker_modes(o)
    if WorkerMode.EVACUATE in legal:
        return WorkerMode.EVACUATE
    if WorkerMode.REPAIR in legal:
        return WorkerMode.REPAIR
    if o.snap.gas > 450 and o.snap.minerals < 300:
        return WorkerMode.MINERALS
    if WorkerMode.GAS in legal and o.snap.gas < 150:
        return WorkerMode.GAS
    return WorkerMode.BALANCE


def economy_investment(macro: Invest, economy: Invest) -> Invest:
    # One investment path; do not starve a pending production/tech investment.
    if macro in (Invest.NONE, Invest.EXPAND, Invest.REFINERY) and economy is not Invest.NONE:
        return economy
    return macro


def gas_worker_quota(mode: WorkerMode) -> int:
    return 1 if mode is WorkerMode.MINERALS else 3 if mode is WorkerMode.GAS else 2


def mineral_worker_floor(mode: WorkerMode) -> int:
    return 8 if mode is WorkerMode.GAS else 16 if mode is WorkerMode.MINERALS else 12


SCOUT_REVISIT_S = 45.0
INTEL_FRESH_S = 30.0
MICRO_HEALTH_FLOOR = 0.35
LOCAL_OVERMATCH_RATIO = 1.4
HARASS_MAX_UNITS = 6
MIN_MAIN_SUPPLY = 12


def squad_sizes(s: Snapshot, marines: int) -> tuple[int, int]:
    if s.base_threatened:
        return 0, 0
    scout = int(marines >= 8)
    harass = min(HARASS_MAX_UNITS, marines // 5, max(0, marines - scout - MIN_MAIN_SUPPLY))
    return scout, harass


def squad_must_retreat(o) -> bool:
    return (
        o.health < MICRO_HEALTH_FLOOR
        or (o.role is not Role.MAIN and o.base_threatened)
        or (
            o.away_from_home
            and (o.unseen_threat or o.enemy_power > max(1, o.supply) * LOCAL_OVERMATCH_RATIO)
        )
    )


def legal_squad_modes(o) -> list[MicroMode]:
    if squad_must_retreat(o):
        return [MicroMode.RETREAT]
    modes = [MicroMode.RETREAT]
    if not o.advancing or o.enemy_power > 0 or o.targets:
        modes.append(MicroMode.HOLD)
    if o.goal:
        modes.append(MicroMode.ADVANCE)
    if o.role is not Role.SCOUT and o.targets:
        modes.extend([MicroMode.ENGAGE, MicroMode.KITE])
    return modes


def baseline_squad_mode(o) -> MicroMode:
    if squad_must_retreat(o):
        return MicroMode.RETREAT
    if o.targets and o.role is not Role.SCOUT:
        return MicroMode.ENGAGE
    return MicroMode.ADVANCE if o.goal else MicroMode.HOLD


def worker_target(suggested: int, bases: int) -> int:
    return min(80, max(suggested, 16 * bases), 22 * bases)


def local_enemy_power(supply: float, structure: bool) -> float:
    return max(1, supply) + (4 if structure else 0)


MICRO_DESCRIPTIONS = {
    MicroMode.ADVANCE: "Follow this squad's assigned scouting, harassment or army objective",
    MicroMode.ENGAGE: "Focus fire on visible, attackable local targets",
    MicroMode.KITE: "Fire when ready, step away from threats during weapon cooldown",
    MicroMode.HOLD: "Hold position while the local situation develops",
    MicroMode.RETREAT: "Withdraw this squad to safety",
}


def pressure_stance(s: Snapshot, chosen: Stance, attack_target: int) -> Stance:
    if (
        chosen not in (Stance.RETREAT, Stance.DEFEND)
        and not s.base_threatened
        and s.army_supply >= max(1, attack_target)
    ):
        return Stance.ATTACK
    return chosen
