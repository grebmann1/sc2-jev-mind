"""Coordinates the two systems without ever blocking the game loop.

Both models run as background tasks. Each game step uses the latest accepted
answers; cold starts and prolonged lack of an investment use a code baseline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from .actions import (
    Focus,
    Invest,
    Role,
    Stance,
    baseline_investment,
    baseline_squad_mode,
    legal_economy_priorities,
    legal_investments,
    legal_squad_modes,
    legal_stances,
    legal_worker_modes,
    safety_override,
)
from .config import Config
from .economy import EconomyDecision, EconomyObservation, EconomyReflex, baseline_economy
from .memory import Episode, MemoryStore, jev_summary
from .squads import SquadDecision, SquadObservation, SquadReflex
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
        squad_reflexes: dict[Role, SquadReflex] | None = None,
        economy_reflex: EconomyReflex | None = None,
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
        self.squad_reflexes = squad_reflexes or {}
        self._squad_tasks: dict[Role, tuple[asyncio.Task, float, tuple]] = {}
        self._squad_choices: dict[Role, tuple[SquadDecision, float, tuple]] = {}
        self._squad_at: dict[Role, float] = {}
        self.squad_recent: list[dict] = []
        self.economy_reflex = economy_reflex
        self._economy_task: asyncio.Task | None = None
        self._economy_at = -1e9
        self._economy_identity: tuple = ()
        self._economy_choice: tuple[EconomyDecision, float, tuple] | None = None
        self.economy_recent: list[dict] = []
        self.economy_state: dict[str, Any] = {}

    def tick_economy(self, o: EconomyObservation) -> EconomyDecision:
        """Schedule the independent worker brain; never wait for its response."""
        if self._closed:
            raise RuntimeError("Mind is closed")
        task = self._economy_task
        self.economy_state = {
            "t": o.snap.time_s,
            "bases": [asdict(b) for b in o.bases],
            "gas_sites": [asdict(g) for g in o.gas_sites],
            "damaged_structures": len(o.damaged_structures),
            "protected_workers": o.protected_workers,
            "idle_workers": o.idle_workers,
        }
        if self.episode:
            self.episode.economy_observation = deepcopy(self.economy_state)
        if task is not None:
            if not task.done():
                if o.identity != self._economy_identity:
                    task.cancel()
            else:
                self._economy_task = None
                try:
                    answer = task.result()
                    if not isinstance(answer, EconomyDecision):
                        raise ValueError("Worker brain did not return a typed decision")
                    answer.__post_init__()
                    if (
                        o.identity != self._economy_identity
                        or o.snap.time_s - self._economy_at > self.cfg.economy_max_age_s
                    ):
                        self.trace(
                            "economy_discarded",
                            {
                                "t": o.snap.time_s,
                                "requested_t": self._economy_at,
                                "reason": "assignment_changed"
                                if o.identity != self._economy_identity
                                else "too_old",
                            },
                        )
                    else:
                        if answer.confidence >= self.cfg.s1_min_confidence and self._economy_legal(
                            o, answer
                        ):
                            self._economy_choice = answer, self._economy_at, o.identity
                        applied = self._choose_economy(o)
                        event = {
                            "t": o.snap.time_s,
                            "requested_t": self._economy_at,
                            "age_s": o.snap.time_s - self._economy_at,
                            **asdict(answer),
                            "applied": [applied.mode.value, applied.priority.value],
                        }
                        self.trace("economy", event)
                        self.economy_recent = (self.economy_recent + [event])[-8:]
                except asyncio.CancelledError:
                    pass
                except Exception as error:
                    if self.episode:
                        self.episode.errors["system1_economy"] += 1
                    self.trace("economy_error", {"t": o.snap.time_s, "error": type(error).__name__})
        choice = self._choose_economy(o)
        if (
            self.economy_reflex is not None
            and o.bases
            and self._economy_task is None
            and o.snap.time_s - self._economy_at >= self.cfg.economy_interval_s
        ):
            self._economy_at, self._economy_identity = o.snap.time_s, o.identity
            self._economy_task = asyncio.create_task(self._call_economy(deepcopy(o)))
        return choice

    @staticmethod
    def _economy_legal(o: EconomyObservation, d: EconomyDecision) -> bool:
        return d.mode in legal_worker_modes(o) and d.priority in legal_economy_priorities(o)

    def _choose_economy(self, o: EconomyObservation) -> EconomyDecision:
        saved = self._economy_choice
        if saved:
            answer, requested_at, identity = saved
            if (
                o.identity == identity
                and o.snap.time_s - requested_at <= self.cfg.economy_max_age_s
                and self._economy_legal(o, answer)
            ):
                return answer
        return baseline_economy(o)

    async def _call_economy(self, o: EconomyObservation) -> EconomyDecision:
        async with asyncio.timeout(self.cfg.s1_timeout_s):
            return await self.economy_reflex.decide(
                replace(o, experience=jev_summary(self.experience))
            )

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

    def tick_squads(self, observations: list[SquadObservation]) -> dict[Role, SquadDecision]:
        """Independent bounded background requests; no model await in the game loop."""
        if self._closed:
            raise RuntimeError("Mind is closed")
        active = {o.role: o for o in observations if o.tags}
        for role, (task, requested_at, identity) in list(self._squad_tasks.items()):
            o = active.get(role)
            if not task.done():
                if o is None or o.identity != identity:
                    task.cancel()
                continue
            del self._squad_tasks[role]
            try:
                answer = task.result()
                if not isinstance(answer, SquadDecision):
                    raise ValueError("Squad brain did not return a typed decision")
                answer.__post_init__()
            except asyncio.CancelledError:
                continue
            except Exception as error:
                if self.episode:
                    self.episode.errors["system1_squads"] += 1
                self.trace(
                    "squad_error",
                    {
                        "t": requested_at if o is None else o.time_s,
                        "role": role.value,
                        "error": type(error).__name__,
                    },
                )
                continue
            if (
                o is None
                or o.identity != identity
                or o.time_s - requested_at > self.cfg.squad_max_age_s
            ):
                self.trace(
                    "squad_discarded",
                    {
                        "role": role.value,
                        "requested_t": requested_at,
                        "reason": "inactive"
                        if o is None
                        else "assignment_changed"
                        if o.identity != identity
                        else "too_old",
                    },
                )
                continue
            if (
                answer.confidence >= self.cfg.s1_min_confidence
                and answer.mode in legal_squad_modes(o)
                and answer.focus in (Focus.ANY, *o.targets)
            ):
                self._squad_choices[role] = answer, requested_at, identity
            applied = self._squad_choice(o)
            event = {
                "t": o.time_s,
                "requested_t": requested_at,
                "age_s": o.time_s - requested_at,
                "role": role.value,
                "tags": [str(tag) for tag in o.tags],
                "goal": o.goal,
                **asdict(answer),
                "applied": [applied.mode.value, applied.focus.value],
            }
            self.trace("squad", event)
            self.squad_recent = (self.squad_recent + [event])[-12:]
        result = {}
        for role, o in active.items():
            result[role] = self._squad_choice(o)
            if (
                role in self.squad_reflexes
                and role not in self._squad_tasks
                and o.time_s - self._squad_at.get(role, -1e9) >= self.cfg.squad_interval_s
            ):
                self._squad_at[role] = o.time_s
                task = asyncio.create_task(self._call_squad(role, deepcopy(o)))
                self._squad_tasks[role] = task, o.time_s, o.identity
        for role in self._squad_choices.keys() - active.keys():
            del self._squad_choices[role]
        return result

    def _squad_choice(self, o: SquadObservation) -> SquadDecision:
        saved = self._squad_choices.get(o.role)
        if saved:
            answer, requested_at, identity = saved
            if (
                o.identity == identity
                and o.time_s - requested_at <= self.cfg.squad_max_age_s
                and answer.mode in legal_squad_modes(o)
                and answer.focus in (Focus.ANY, *o.targets)
            ):
                return answer
        return SquadDecision(baseline_squad_mode(o), 1, Focus.ANY)

    async def _call_squad(self, role: Role, o: SquadObservation) -> SquadDecision:
        async with asyncio.timeout(self.cfg.s1_timeout_s):
            return await self.squad_reflexes[role].decide(
                replace(o, experience=jev_summary(self.experience))
            )

    def invest_done(self, investment: Invest | None = None) -> None:
        done = self.invest if investment is None else investment
        if self.episode and done is not Invest.NONE:
            self.episode.investments[done.value] += 1
        if done is self.invest:
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
            context = {
                **experience,
                "opening_plan": self.opening_plan,
                "recent_squad_decisions": deepcopy(self.squad_recent),
                "recent_economy_decisions": deepcopy(self.economy_recent),
                "economy_observation": deepcopy(self.economy_state),
            }
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
        self.trace(
            "s2",
            {
                "t": round(snap.time_s, 1),
                "requested_t": self._s2_at,
                "age_s": age,
                **self.plan.model_dump(mode="json"),
            },
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        all_tasks = [
            self._s1,
            self._s2,
            self._economy_task,
            *(row[0] for row in self._squad_tasks.values()),
        ]
        tasks = [t for t in all_tasks if t is not None and not t.done()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for task in all_tasks:
            if task is not None and task.done() and not task.cancelled():
                task.exception()  # Consume failures even if no final tick collected them.
        self._s1 = self._s2 = None
        self._economy_task = None
        self._economy_choice = None
        self._squad_tasks.clear()
        self._squad_choices.clear()
        models = {
            id(m): m
            for m in (
                self.reflex,
                self.reasoner,
                self.economy_reflex,
                *self.squad_reflexes.values(),
            )
            if m is not None
        }
        for model in models.values():
            close = getattr(model, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as e:
                    log.warning("Closing model client failed: %s", type(e).__name__)
