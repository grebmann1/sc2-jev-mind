"""Local episodic memory. SQLite work runs outside the game-step callback.

Only observed completed matches become evidence. No model-written lessons, secrets,
or changes to the legality/safety policy are stored or executed here.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .learning import OPENINGS, POLICY_VERSION
from .state import Snapshot

SCHEMA_VERSION = 1
RESULTS = {"Victory", "Defeat", "Tie"}


@dataclass(frozen=True)
class MatchContext:
    map_name: str
    enemy_race: str
    difficulty: str
    controller: str
    engine: str
    realtime: bool
    time_limit_s: float
    seed: int | None = None
    policy: str = POLICY_VERSION

    @property
    def key(self) -> str:
        fields = asdict(self)
        fields.pop("enemy_race")
        fields.pop("seed")  # Preserve seeds for audit; compare openings across seeds.
        fields["time_limit_s"] = float(self.time_limit_s)
        return json.dumps(fields, sort_keys=True, separators=(",", ":"))

    def for_race(self, race: str) -> MatchContext:
        return replace(
            self, enemy_race=race if race in {"Terran", "Protoss", "Zerg"} else self.enemy_race
        )


class Episode:
    """Bounded in-memory observations; sampling gaps aren't counted as measured time."""

    def __init__(self, match_id: str, context: MatchContext, opening: str) -> None:
        self.match_id, self.context, self.opening = match_id, context, opening
        self.timeline: list[dict] = []
        self.investments: Counter = Counter()
        self.errors: Counter = Counter()
        self.scout_visits: int | None = None
        self.squad_losses: Counter = Counter()
        self.observations = 0
        self.peak_workers = self.peak_army_supply = self.peak_bases = 0
        self.supply_blocked_s = self.under_threat_s = 0.0
        self.observed_s = self.mineral_bank_s = 0.0
        self.peak_minerals = self.peak_gas = 0
        self.first_expansion_s: float | None = None
        self.first_stim_s: float | None = None
        self.first_threat_s: float | None = None
        self._previous: Snapshot | None = None
        self.initial_plan: dict | None = None

    def observe(self, snap: Snapshot, plan: dict) -> None:
        self.observations += 1
        if "scout_visits" in snap.intelligence:
            self.scout_visits = snap.intelligence["scout_visits"]
        self.context = self.context.for_race(snap.enemy_race)
        self.peak_workers = max(self.peak_workers, snap.workers)
        self.peak_army_supply = max(self.peak_army_supply, snap.army_supply)
        self.peak_bases = max(self.peak_bases, snap.townhalls)
        self.peak_minerals = max(self.peak_minerals, snap.minerals)
        self.peak_gas = max(self.peak_gas, snap.gas)
        if snap.townhalls >= 2 and self.first_expansion_s is None:
            self.first_expansion_s = round(snap.time_s, 1)
        if snap.stim == "done" and self.first_stim_s is None:
            self.first_stim_s = round(snap.time_s, 1)
        if self.initial_plan is None:
            self.initial_plan = plan
        if snap.base_threatened and self.first_threat_s is None:
            self.first_threat_s = round(snap.time_s, 1)
        prev = self._previous
        if prev is not None:
            dt = snap.time_s - prev.time_s
            if 0 < dt <= 5:
                self.observed_s += dt
                if prev.minerals >= 800:
                    self.mineral_bank_s += dt
                if prev.supply_used >= prev.supply_cap and prev.supply_cap < 200:
                    self.supply_blocked_s += dt
                if prev.base_threatened:
                    self.under_threat_s += dt
        # Retain the most recent observation without sharing mutable dictionaries.
        self._previous = replace(
            snap, army=dict(snap.army), enemy_army_seen=dict(snap.enemy_army_seen)
        )
        if not self.timeline or snap.time_s - self.timeline[-1]["time_s"] >= 10:
            self.timeline.append(
                {
                    "time_s": round(snap.time_s, 1),
                    "workers": snap.workers,
                    "army_supply": snap.army_supply,
                    "bases": snap.townhalls,
                    "supply": [snap.supply_used, snap.supply_cap],
                    "enemy_army_seen": dict(snap.enemy_army_seen),
                    "threat": snap.enemies_near_base,
                    "minerals": snap.minerals,
                    "gas": snap.gas,
                    "army": dict(snap.army),
                    "barracks": snap.barracks,
                    "techlabs": snap.techlabs,
                    "reactors": snap.reactors,
                    "stim": snap.stim,
                    "weapons1": snap.weapons1,
                    "intelligence": json.loads(json.dumps(snap.intelligence)),
                }
            )
            self.timeline = self.timeline[-200:]

    def metrics(self) -> dict:
        return {
            "duration_s": round(self._previous.time_s, 1) if self._previous else 0,
            "peak_workers": self.peak_workers,
            "peak_army_supply": self.peak_army_supply,
            "peak_bases": self.peak_bases,
            "first_threat_s": self.first_threat_s,
            "observed_supply_blocked_s": round(self.supply_blocked_s, 1),
            "observed_under_threat_s": round(self.under_threat_s, 1),
            "observed_time_s": round(self.observed_s, 1),
            "observations": self.observations,
            "scout_visits": self.scout_visits,
            "squad_losses": dict(self.squad_losses),
            "observed_mineral_bank_s": round(self.mineral_bank_s, 1),
            "peak_minerals": self.peak_minerals,
            "peak_gas": self.peak_gas,
            "first_expansion_s": self.first_expansion_s,
            "first_stim_s": self.first_stim_s,
            "model_errors": dict(self.errors),
            "issued_investments": dict(self.investments),
        }

    def facts(self) -> list[str]:
        facts = [
            f"Peaks: {self.peak_workers} workers, {self.peak_army_supply} army supply, {self.peak_bases} bases."
        ]
        if self.supply_blocked_s >= 10:
            facts.append(
                f"Observed supply blocks for {self.supply_blocked_s:.0f}s; investigate depot timing."
            )
        if self.first_threat_s is not None:
            facts.append(f"First observed base threat at {self.first_threat_s:.0f}s.")
        if self.errors:
            facts.append(
                f"Model request failures: {sum(self.errors.values())}; outcome also reflects provider availability."
            )
        return facts

    def record(self, result: str, final_plan: dict) -> dict:
        if result not in RESULTS or not self.observations:
            raise ValueError("Only observed completed matches can become memory")
        return {
            "match_id": self.match_id,
            "context": asdict(self.context),
            "opening": self.opening,
            "result": result,
            "created_at": datetime.now(UTC).isoformat(),
            "initial_plan": self.initial_plan,
            "final_plan": final_plan,
            "metrics": self.metrics(),
            "facts": self.facts(),
            "timeline": self.timeline,
        }


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f"Unsupported memory schema version: {version}")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS episodes (
                match_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, race TEXT NOT NULL,
                opening TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS episode_context ON episodes(context_key, race)")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def save(self, record: dict) -> bool:
        context = MatchContext(**record["context"])
        if record["result"] not in RESULTS or record["opening"] not in OPENINGS:
            raise ValueError("Invalid match evidence")
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["match_id"],
                    context.key,
                    context.enemy_race,
                    record["opening"],
                    record["result"],
                    record["created_at"],
                    json.dumps(record, allow_nan=False),
                ),
            )
            return cursor.rowcount == 1

    def _connect_readonly(self):
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)

    def get(self, match_id: str) -> dict | None:
        if not self.path.exists():
            return None
        with closing(self._connect_readonly()) as db:
            row = db.execute(
                "SELECT payload FROM episodes WHERE match_id=?", (match_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def review_history(self, context: MatchContext, limit: int = 50) -> list[dict]:
        """Compare maps/races while holding execution and decision policy constant."""
        if not self.path.exists():
            return []
        fields = ("controller", "engine", "difficulty", "policy", "realtime", "time_limit_s")
        where = " AND ".join(f"json_extract(context_key, '$.{key}') = ?" for key in fields)
        with closing(self._connect_readonly()) as db:
            rows = db.execute(
                f"SELECT payload FROM episodes WHERE {where} ORDER BY created_at DESC, match_id DESC LIMIT ?",
                [*(getattr(context, key) for key in fields), max(0, min(limit, 50))],
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def retrieve(self, context: MatchContext, limit: int = 3) -> dict[str, Any]:
        # Pool races only while a random opponent hasn't been identified.
        race_clause = "" if context.enemy_race in {"Random", "Unknown"} else " AND race = ?"
        params = [context.key] + ([context.enemy_race] if race_clause else [])
        where = "context_key = ?" + race_clause
        with closing(self._connect_readonly()) as db:
            groups = db.execute(
                f"SELECT opening, COUNT(*), SUM(result='Victory'), SUM(result='Tie') FROM episodes WHERE {where} GROUP BY opening",
                params,
            ).fetchall()
            rows = db.execute(
                f"SELECT payload FROM episodes WHERE {where} ORDER BY created_at DESC, match_id DESC LIMIT ?",
                [*params, max(0, min(limit, 5))],
            ).fetchall()
        stats = {
            name: {
                "games": n,
                "wins": wins,
                "ties": ties,
                "smoothed_score": round((wins + 0.5 * ties + 1) / (n + 2), 3),
            }
            for name, n, wins, ties in groups
            if name in OPENINGS
        }
        episodes = []
        for (payload,) in rows:
            try:
                record = json.loads(payload)
                episodes.append(
                    {k: record[k] for k in ("match_id", "opening", "result", "metrics", "facts")}
                )
            except (ValueError, KeyError, TypeError):
                continue
        return {
            "comparable_games": sum(s["games"] for s in stats.values()),
            "opening_stats": stats,
            "episodes": episodes,
            "general_patterns": general_patterns(self.review_history(context)),
            "interpretation": "Observed associations, not causal proof; current game facts take precedence.",
        }

    def info(self) -> dict:
        if not self.path.exists():
            return {"path": str(self.path), "matches": 0, "contexts": 0, "openings": {}}
        with closing(self._connect_readonly()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported memory schema version: {version}")
            matches, contexts = db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT context_key || race) FROM episodes"
            ).fetchone()
            rows = db.execute("SELECT opening, COUNT(*) FROM episodes GROUP BY opening").fetchall()
        return {
            "path": str(self.path),
            "matches": matches,
            "contexts": contexts,
            "openings": dict(rows),
        }


def general_patterns(records: list[dict]) -> dict:
    """Recurring measured signals, never model-authored advice or causal claims."""
    eligible = {
        r["match_id"]: r for r in records if r.get("metrics", {}).get("duration_s", 0) >= 180
    }
    games = list(eligible.values())

    def diversity(rows: list[dict]) -> dict:
        contexts = [r["context"] for r in rows]
        conditions = {(c["map_name"], c["enemy_race"], c.get("seed")) for c in contexts}
        return {
            "matches": len(rows),
            "conditions": len(conditions),
            "maps": sorted({c["map_name"] for c in contexts}),
            "races": sorted({c["enemy_race"] for c in contexts}),
            "seeds": sorted({c["seed"] for c in contexts if c.get("seed") is not None}),
        }

    signals = {}
    for name, metric, threshold in (
        ("supply_blocks", "observed_supply_blocked_s", 10),
        ("mineral_bank", "observed_mineral_bank_s", 30),
        ("provider_errors", "model_errors", 1),
    ):
        measured = [
            r
            for r in games
            if metric in r["metrics"]
            and (metric == "model_errors" or r["metrics"].get("observed_time_s", 1) > 0)
        ]
        affected = [
            r
            for r in measured
            if (
                sum(r["metrics"][metric].values())
                if metric == "model_errors"
                else r["metrics"][metric]
            )
            >= threshold
        ]
        scope = diversity(affected)
        broad = (
            scope["conditions"] >= 3
            and len(scope["maps"]) >= 2
            and len(set(scope["races"]) - {"Random", "Unknown"}) >= 2
            and len(scope["seeds"]) >= 3
        )
        signals[name] = {
            **scope,
            "measured_matches": len(measured),
            "metric": metric,
            "threshold": threshold,
            "status": "candidate_for_controlled_test" if broad else "provisional",
        }
    return {
        **diversity(games),
        "outcomes_by_map_race": [
            {
                "map": name,
                "race": race,
                "results": dict(
                    Counter(
                        r["result"]
                        for r in games
                        if r["context"]["map_name"] == name and r["context"]["enemy_race"] == race
                    )
                ),
            }
            for name, race in sorted(
                {(r["context"]["map_name"], r["context"]["enemy_race"]) for r in games}
            )
        ],
        "window_limit": 50,
        "minimum_duration_s": 180,
        "signals": signals,
        "interpretation": "Same engine/controller/policy/difficulty/speed/time limit; maps/races may vary. Repeated conditions are not independent evidence. Signals are associations, never proof of improvement. Short diagnostics excluded; missing metrics are unknown.",
    }


def jev_summary(experience: dict) -> str:
    count = experience.get("comparable_games", 0)
    if not count:
        return "No comparable completed matches yet."
    stats = experience.get("opening_stats", {})
    scores = "; ".join(f"{name}: {s['wins']} wins/{s['games']} games" for name, s in stats.items())
    episodes = experience.get("episodes", [])
    latest = episodes[0] if episodes else None
    facts = " ".join(latest.get("facts", [])[:2]) if latest else ""
    return f"{count} comparable matches. {scores}. Latest: {latest['result'] if latest else 'unknown'}. {facts} Observed associations only."[
        :650
    ]
