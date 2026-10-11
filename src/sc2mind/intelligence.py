"""Bounded observed enemy positions; fog is uncertainty, never evidence of absence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import hypot

from .actions import INTEL_FRESH_S

BASE_NAMES = {
    "CommandCenter",
    "OrbitalCommand",
    "PlanetaryFortress",
    "Nexus",
    "Hatchery",
    "Lair",
    "Hive",
}


@dataclass(frozen=True)
class Contact:
    tag: int
    name: str
    position: tuple[float, float]
    seen_at: float
    supply: float = 0
    worker: bool = False
    structure: bool = False
    dangerous: bool = False


def distance(a, b) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


class Intelligence:
    def __init__(self) -> None:
        self.contacts: dict[int, Contact] = {}
        self.last_seen: float | None = None
        self.movement = "unknown"
        self.movement_at = -1e9
        self.scouted: dict[tuple[float, float], float] = {}
        self.visits = 0

    def observe(self, now: float, contacts: list[Contact], homes: list[tuple]) -> None:
        closing = []
        for c in contacts:
            old = self.contacts.get(c.tag)
            if old and homes and c.dangerous and not c.structure and not c.worker:
                dt = now - old.seen_at
                if 0 < dt <= INTEL_FRESH_S:
                    before = min(distance(old.position, home) for home in homes)
                    after = min(distance(c.position, home) for home in homes)
                    closing.append((before - after) / dt)
            self.contacts[c.tag] = c
        if contacts:
            self.last_seen = now
        if closing:
            mean = sum(closing) / len(closing)
            self.movement = (
                "toward_our_bases"
                if mean > 0.8
                else "away_from_our_bases"
                if mean < -0.8
                else "holding_or_lateral"
            )
            self.movement_at = now
        self.contacts = {
            tag: c for tag, c in self.contacts.items() if c.structure or now - c.seen_at <= 120
        }
        if len(self.contacts) > 256:
            newest = sorted(self.contacts.values(), key=lambda c: c.seen_at, reverse=True)[:256]
            self.contacts = {c.tag: c for c in newest}

    def forget(self, tag: int) -> None:
        self.contacts.pop(tag, None)

    def visit(self, point: tuple, now: float) -> None:
        self.scouted[point] = now
        self.visits += 1
        if len(self.scouted) > 128:
            del self.scouted[min(self.scouted, key=self.scouted.get)]

    def report(self, now: float) -> dict:
        fresh = [c for c in self.contacts.values() if now - c.seen_at <= INTEL_FRESH_S]
        history = {}
        for c in self.contacts.values():
            if c.structure:
                history[c.name] = max(history.get(c.name, -1e9), c.seen_at)
        return {
            "last_sighting_age_s": None
            if self.last_seen is None
            else round(now - self.last_seen, 1),
            "movement": self.movement if now - self.movement_at <= INTEL_FRESH_S else "unknown",
            "recent_army": dict(Counter(c.name for c in fresh if not c.worker and not c.structure)),
            "recent_supply": round(sum(c.supply for c in fresh if not c.worker), 1),
            "known_bases": sum(
                c.structure and c.name in BASE_NAMES for c in self.contacts.values()
            ),
            "recent_structures": sorted({c.name for c in fresh if c.structure})[:16],
            "known_structure_history": [
                {"name": name, "age_s": round(now - seen, 1)}
                for name, seen in sorted(history.items(), key=lambda item: item[1], reverse=True)[
                    :16
                ]
            ],
            "scout_visits": self.visits,
            "uncertainty": "Only observed contacts. Missing or stale sightings do not establish a weak enemy.",
        }

    def short_report(self, now: float) -> str:
        r = self.report(now)
        return f"age={r['last_sighting_age_s']} movement={r['movement']} bases_seen={r['known_bases']} recent={r['recent_army']} visits={self.visits}; unseen strength unknown"[
            :400
        ]
