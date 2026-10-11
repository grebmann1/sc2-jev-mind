"""Coordinates the two systems without ever blocking the game loop.

Both models run as background tasks. Each game step uses the latest accepted
answers; cold starts and prolonged lack of an investment use a code baseline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .actions import (
    Invest,
    Stance,
    baseline_investment,
    legal_investments,
    legal_stances,
    safety_override,
)
from .config import Config
from .memory import Episode, MemoryStore, jev_summary
from .state import Snapshot
from .system1 import Decision, Reflex
from .system2 import DEFAULT_PLAN, Plan, Reasoner

log = logging.getLogger(__name__)

TraceFn = Callable[[str, dict[str, Any]], None]


class Mind:
    def __init__(
        self,
        cfg: Config,
        reflex: Reflex,
        reasoner: Reasoner,
        trace: TraceFn | None = None,
        *,
        memory: MemoryStore | None = None,
        episode: Episode | None = None,
        initial_plan: Plan | None = None,
        experience: dict[str, Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.reflex = reflex
        self.reasoner = reasoner
        self.trace = trace or (lambda kind, data: None)

        self.plan = Plan.model_validate((initial_plan or DEFAULT_PLAN).model_dump()).model_copy(
            deep=True
        )
        self.opening_plan = self.plan.model_dump(mode="json")
        self.memory, self.episode = memory, episode
        self.experience = deepcopy(experience or {})
        self.memory_saved = False
        self._finished = False
        self._accepted_invest = False
        self._last_invest_at = -1e9
        self.invest: Invest = Invest.NONE
        self.stance: Stance = Stance.RALLY
        self.recent: list[dict[str, Any]] = []

        self._s1: asyncio.Task[Decision] | None = None
        self._s2: asyncio.Task[Plan] | None = None
        self._s1_at = -1e9
        self._s2_at = -1e9
        self._wake_s2: str | None = "game start"
        self._closed = False

    def tick(self, snap: Snapshot) -> tuple[Invest, Stance]:
        if self._closed:
            raise RuntimeError("Mind is closed")
        self._collect_s2(snap)
        self._collect_s1(snap)

        if self.episode:
            self.episode.observe(snap, self.plan.model_dump(mode="json"))
        # Revalidate against this frame, including while a model request is pending.
        if self.invest not in legal_investments(snap):
            self.invest = Invest.NONE
        if self.stance not in legal_stances(snap):
            self.stance = Stance.RALLY
        # An unavailable provider must not leave an empty investment indefinitely.
        if not self._accepted_invest or (
            self.invest is Invest.NONE
            and snap.time_s - self._last_invest_at > self.cfg.s1_max_age_s
        ):
            self.invest = baseline_investment(snap)

        if self._s1 is None and snap.time_s - self._s1_at >= self.cfg.s1_interval_s:
            self._s1_at = snap.time_s
            self._s1 = asyncio.create_task(
                self._call_s1(deepcopy(snap), self.plan.model_copy(deep=True))
            )

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
                self._s2 = asyncio.create_task(
                    self._call_s2(
                        deepcopy(snap), self.plan.model_copy(deep=True), deepcopy(self.recent), why
                    )
                )

        return self.invest, safety_override(snap, self.stance)

    def invest_done(self) -> None:
        if self.episode and self.invest is not Invest.NONE:
            self.episode.investments[self.invest.value] += 1
        self.invest = Invest.NONE

    async def _call_s1(self, snap: Snapshot, plan: Plan) -> Decision:
        async with asyncio.timeout(self.cfg.s1_timeout_s):
            return await self.reflex.decide(snap, plan, jev_summary(self.experience))

    async def _call_s2(
        self, snap: Snapshot, plan: Plan, recent: list[dict[str, Any]], why: str
    ) -> Plan:
        async with asyncio.timeout(self.cfg.s2_timeout_s):
            experience = {}
            if self.memory and self.episode:
                try:
                    experience = await asyncio.to_thread(
                        self.memory.retrieve, self.episode.context.for_race(snap.enemy_race)
                    )
                except Exception as error:
                    log.warning("Memory retrieval failed: %s", type(error).__name__)
                    self.trace(
                        "memory_error", {"operation": "retrieve", "type": type(error).__name__}
                    )
            self.experience = experience
            context = {**experience, "opening_plan": self.opening_plan}
            if self.episode:
                context["current_match"] = {
                    "metrics": self.episode.metrics(),
                    "recent_observations": deepcopy(self.episode.timeline[-12:]),
                }
            return await self.reasoner.plan(snap, plan, recent, why, context)

    async def finish_match(self, result: str) -> None:
        if self._finished:
            return
        self._finished = True
        if not self.memory or not self.episode or not self.episode.observations:
            return
        try:
            record = self.episode.record(result, self.plan.model_dump(mode="json"))
            self.memory_saved = await asyncio.to_thread(self.memory.save, record)
            self.trace(
                "memory_saved",
                {
                    "match_id": self.episode.match_id,
                    "saved": self.memory_saved,
                    "opening": self.episode.opening,
                    "facts": record["facts"],
                },
            )
        except Exception as error:
            log.warning("Match memory could not be saved: %s", type(error).__name__)
            self.trace("memory_error", {"operation": "save", "type": type(error).__name__})

    def _collect_s1(self, snap: Snapshot) -> None:
        if self._s1 is None or not self._s1.done():
            return
        task, self._s1 = self._s1, None
        try:
            d = task.result()
            if not isinstance(d, Decision):
                raise ValueError("System 1 did not return a Decision")
            d.__post_init__()
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001 - any model failure keeps the last decision
            log.warning("System 1 failed: %s", e)
            if self.episode:
                self.episode.errors["system1"] += 1
            self.trace("s1_error", {"t": snap.time_s, "error": repr(e)})
            return

        age = snap.time_s - self._s1_at
        if age > self.cfg.s1_max_age_s:
            self.trace("s1_discarded", {"t": snap.time_s, "age_s": age})
            return

        if d.invest_p >= self.cfg.s1_min_confidence and d.invest in legal_investments(snap):
            self.invest = d.invest
            self._accepted_invest = True
            self._last_invest_at = self._s1_at
        if d.stance_p >= self.cfg.s1_min_confidence and d.stance in legal_stances(snap):
            self.stance = d.stance
        if d.replan_p >= self.cfg.s1_replan_threshold and self._wake_s2 is None:
            self._wake_s2 = f"System 1 flagged divergence (p={d.replan_p:.2f})"

        rec = {
            "t": round(snap.time_s, 1),
            "requested_t": self._s1_at,
            "age_s": age,
            **asdict(d),
            "applied": [self.invest.value, self.stance.value],
        }
        self.recent = (self.recent + [rec])[-20:]
        self.trace("s1", rec)

    def _collect_s2(self, snap: Snapshot) -> None:
        if self._s2 is None or not self._s2.done():
            return
        task, self._s2 = self._s2, None
        try:
            result = task.result()
            if not isinstance(result, Plan):
                raise ValueError("System 2 did not return a Plan")
            result = Plan.model_validate(result.model_dump())
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("System 2 failed: %s", e)
            if self.episode:
                self.episode.errors["system2"] += 1
            self.trace("s2_error", {"t": snap.time_s, "error": repr(e)})
            return
        age = snap.time_s - self._s2_at
        if age > self.cfg.s2_max_age_s:
            self.trace("s2_discarded", {"t": snap.time_s, "age_s": age})
            return
        self.plan = result
        self.trace("s2", {"t": round(snap.time_s, 1), **self.plan.model_dump(mode="json")})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = [t for t in (self._s1, self._s2) if t is not None and not t.done()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for task in (self._s1, self._s2):
            if task is not None and task.done() and not task.cancelled():
                task.exception()  # Consume failures even if no final tick collected them.
        self._s1 = self._s2 = None
        for model in (self.reflex, self.reasoner):
            close = getattr(model, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as e:
                    log.warning("Closing model client failed: %s", type(e).__name__)
