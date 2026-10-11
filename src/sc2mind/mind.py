"""Coordinates the two systems without ever blocking the game loop.

Both models run as background tasks. Each game step uses the latest accepted
answers; a late or failed answer just means the previous decision stands.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, Callable

from .actions import Invest, Stance, safety_override
from .config import Config
from .state import Snapshot
from .system1 import Decision, Reflex
from .system2 import DEFAULT_PLAN, Plan, Reasoner

log = logging.getLogger(__name__)

TraceFn = Callable[[str, dict[str, Any]], None]


class Mind:
    def __init__(self, cfg: Config, reflex: Reflex, reasoner: Reasoner, trace: TraceFn | None = None) -> None:
        self.cfg = cfg
        self.reflex = reflex
        self.reasoner = reasoner
        self.trace = trace or (lambda kind, data: None)

        self.plan: Plan = DEFAULT_PLAN
        self.invest: Invest = Invest.NONE
        self.stance: Stance = Stance.RALLY
        self.recent: list[dict[str, Any]] = []

        self._s1: asyncio.Task[Decision] | None = None
        self._s2: asyncio.Task[Plan] | None = None
        self._s1_at = -1e9
        self._s2_at = -1e9
        self._wake_s2: str | None = "game start"

    def tick(self, snap: Snapshot) -> tuple[Invest, Stance]:
        self._collect_s2(snap)
        self._collect_s1(snap)

        if self._s1 is None and snap.time_s - self._s1_at >= self.cfg.s1_interval_s:
            self._s1_at = snap.time_s
            self._s1 = asyncio.create_task(self.reflex.decide(snap, self.plan))

        if self._s2 is None:
            since = snap.time_s - self._s2_at
            why = None
            if since >= self.cfg.s2_interval_s:
                why = "scheduled replan"
            if self._wake_s2 and since >= self.cfg.s2_min_interval_s:
                why = self._wake_s2
            if why:
                self._s2_at = snap.time_s
                self._wake_s2 = None
                self._s2 = asyncio.create_task(self.reasoner.plan(snap, self.plan, list(self.recent), why))

        return self.invest, safety_override(snap, self.stance)

    def invest_done(self) -> None:
        self.invest = Invest.NONE

    def _collect_s1(self, snap: Snapshot) -> None:
        if self._s1 is None or not self._s1.done():
            return
        task, self._s1 = self._s1, None
        try:
            d = task.result()
        except Exception as e:  # noqa: BLE001 - any model failure keeps the last decision
            log.warning("System 1 failed: %s", e)
            self.trace("s1_error", {"t": snap.time_s, "error": repr(e)})
            return

        if d.invest_p >= self.cfg.s1_min_confidence:
            self.invest = d.invest
        if d.stance_p >= self.cfg.s1_min_confidence:
            self.stance = d.stance
        if d.replan_p >= self.cfg.s1_replan_threshold and self._wake_s2 is None:
            self._wake_s2 = f"System 1 flagged divergence (p={d.replan_p:.2f})"

        rec = {"t": round(snap.time_s, 1), **asdict(d), "applied": [self.invest.value, self.stance.value]}
        self.recent = (self.recent + [rec])[-20:]
        self.trace("s1", rec)

    def _collect_s2(self, snap: Snapshot) -> None:
        if self._s2 is None or not self._s2.done():
            return
        task, self._s2 = self._s2, None
        try:
            self.plan = task.result()
        except Exception as e:  # noqa: BLE001
            log.warning("System 2 failed: %s", e)
            self.trace("s2_error", {"t": snap.time_s, "error": repr(e)})
            return
        self.trace("s2", {"t": round(snap.time_s, 1), **self.plan.model_dump(mode="json")})

    async def close(self) -> None:
        for t in (self._s1, self._s2):
            if t is not None:
                t.cancel()
