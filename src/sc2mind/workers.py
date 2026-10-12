"""Engine-side worker ownership, safe mining allocation, repair and evacuation."""

from collections import Counter

from sc2.position import Point2

from .actions import (
    MAX_REPAIR_WORKERS,
    REPAIR_HEALTH_FLOOR,
    REPAIR_MINERALS,
    WORKER_DANGER_RADIUS,
    WORKER_REBALANCE_S,
    WorkerMode,
    gas_worker_quota,
    mineral_worker_floor,
)
from .economy import EconomyObservation, MiningSite


class WorkerManager:
    def __init__(self, bot, executor, trace) -> None:
        self.bot, self.executor, self.trace = bot, executor, trace
        self._mining: dict[int, tuple[str, int]] = {}
        self._special: dict[int, tuple[str, int | tuple]] = {}
        self._special_at: dict[int, float] = {}
        self._last_balance = self._last_trace = -1e9
        self._mode: WorkerMode | None = None
        self._issued = Counter()

    def _threats(self):
        return self.bot.enemy_units.filter(
            lambda u: u.is_visible and not u.is_hallucination and u.can_attack_ground
        )

    def _safe(self, point):
        return not self._threats().closer_than(WORKER_DANGER_RADIUS, point)

    def _damaged(self):
        return self.bot.structures.ready.filter(
            lambda u: u.health_percentage < REPAIR_HEALTH_FLOOR and self._safe(u)
        )

    def observation(self, snap, plan):
        def site(u):
            return MiningSite(u.tag, u.assigned_harvesters, u.ideal_harvesters)

        return EconomyObservation(
            snap,
            tuple(site(u) for u in sorted(self.bot.townhalls.ready, key=lambda u: u.tag)),
            tuple(
                site(u)
                for u in sorted(self.bot.gas_buildings.ready, key=lambda u: u.tag)
                if u.has_vespene
            ),
            tuple(u.tag for u in self._damaged()),
            len(self.executor.reserved_workers | self.executor.economy_workers),
            self.bot.workers.idle.amount,
            plan.for_system1(),
        )

    def _escape(self, worker, danger, bases):
        if bases:
            return min(bases, key=worker.distance_to).position
        area = self.bot.game_info.playable_area
        candidates = [worker.position.towards(danger.position, -12)]
        candidates.extend(
            worker.position + Point2(offset) for offset in ((8, 0), (-8, 0), (0, 8), (0, -8))
        )
        candidates = [
            Point2(
                (
                    min(area.x + area.width - 1, max(area.x + 1, p.x)),
                    min(area.y + area.height - 1, max(area.y + 1, p.y)),
                )
            )
            for p in candidates
        ]
        walkable = [p for p in candidates if self.bot.in_pathing_grid(p)]
        return max(walkable or [worker.position], key=danger.distance_to)

    def _special_order(self, worker, kind, target):
        value = target.tag if kind == "repair" else tuple(target)
        same = self._special.get(worker.tag) == (kind, value)
        self._special[worker.tag] = kind, value
        if same and self.bot.time - self._special_at.get(worker.tag, -1e9) < WORKER_REBALANCE_S:
            return
        if kind == "repair":
            if not getattr(worker, "is_repairing", False) or not same:
                worker.repair(target)
            else:
                return
        else:
            worker.move(target)
        self._special_at[worker.tag] = self.bot.time
        self._issued[kind] += 1
        self.trace(
            "worker_order",
            {"t": self.bot.time, "worker": str(worker.tag), "action": kind, "target": value},
        )

    def step(self, decision, investment):
        bot = self.bot
        self._issued.clear()
        old_special = dict(self._special)
        self._special = {}
        threats = self._threats()
        bases = [b for b in bot.townhalls.ready if self._safe(b)]
        workers = [
            w
            for w in bot.workers
            if w.tag not in self.executor.reserved_workers
            and w.tag not in bot.unit_tags_received_action
            and not getattr(w, "is_constructing_scv", False)
            and (
                w.is_idle
                or w.is_gathering
                or getattr(w, "is_returning", False)
                or w.tag in old_special
            )
            and (not getattr(w, "is_repairing", False) or w.tag in old_special)
        ]
        # Local protection is checked every frame, regardless of a model's current mode.
        for worker in workers:
            nearby = threats.closer_than(WORKER_DANGER_RADIUS, worker)
            if nearby:
                self._special[worker.tag] = old_special.get(worker.tag, ("", 0))
                self._special_order(
                    worker, "evacuate", self._escape(worker, nearby.closest_to(worker), bases)
                )
        if decision.mode is WorkerMode.REPAIR and bot.minerals >= REPAIR_MINERALS:
            damaged = self._damaged()
            if damaged:
                target = min(damaged, key=lambda u: u.health_percentage)
                candidates = [
                    w for w in workers if w.tag not in self._special and w.health_percentage >= 0.5
                ]
                candidates.sort(
                    key=lambda w: (
                        old_special.get(w.tag) != ("repair", target.tag),
                        w.distance_to(target),
                    )
                )
                for worker in candidates[: min(MAX_REPAIR_WORKERS, len(workers) // 8)]:
                    self._special[worker.tag] = old_special.get(worker.tag, ("", 0))
                    self._special_order(worker, "repair", target)
        self.executor.economy_workers = set(self._special)
        free = [w for w in workers if w.tag not in self._special]
        changed = decision.mode != self._mode
        if (
            changed
            or bot.time - self._last_balance >= WORKER_REBALANCE_S
            or old_special.keys() - self._special.keys()
        ):
            self._rebalance(free, bases, decision.mode, set(old_special) - set(self._special))
            self._last_balance = bot.time
        self._mode = decision.mode
        alive = {w.tag for w in bot.workers}
        self._mining = {tag: job for tag, job in self._mining.items() if tag in alive}
        self._special_at = {tag: at for tag, at in self._special_at.items() if tag in self._special}
        logged = changed or bot.time - self._last_trace >= 4
        if logged:
            self._last_trace = bot.time
            self.trace(
                "economy_order",
                {
                    "t": bot.time,
                    "mode": decision.mode.value,
                    "priority": decision.priority.value,
                    "investment": investment.value,
                    "workers": len(bot.workers),
                    "protected": len(self.executor.reserved_workers),
                    "repairing": sum(kind == "repair" for kind, _ in self._special.values()),
                    "evacuating": sum(kind == "evacuate" for kind, _ in self._special.values()),
                    "bases": [
                        {
                            "tag": str(b.tag),
                            "assigned": b.assigned_harvesters,
                            "ideal": b.ideal_harvesters,
                        }
                        for b in bot.townhalls.ready
                    ],
                    "gas": [
                        {
                            "tag": str(g.tag),
                            "assigned": g.assigned_harvesters,
                            "ideal": g.ideal_harvesters,
                        }
                        for g in bot.gas_buildings.ready
                    ],
                },
            )
        return {"issued": dict(self._issued), "logged": logged}

    def _gather(self, worker, target, kind, site):
        self._mining[worker.tag] = kind, site.tag
        if (
            worker.order_target == target.tag
            and not worker.is_idle
            and not getattr(worker, "is_repairing", False)
        ):
            return
        worker.gather(target)
        self._issued[kind] += 1
        self.trace(
            "worker_order",
            {
                "t": self.bot.time,
                "worker": str(worker.tag),
                "action": kind,
                "target": str(target.tag),
            },
        )

    def _rebalance(self, workers, bases, mode, released):
        bot = self.bot
        fields = {b.tag: list(bot.mineral_field.closer_than(10, b)) for b in bases}
        bases = [b for b in bases if fields[b.tag] and b.ideal_harvesters > 0]
        base_tags = {b.tag for b in bases}
        gas = [
            g
            for g in bot.gas_buildings.ready
            if g.has_vespene and self._safe(g) and any(b.distance_to(g) < 15 for b in bases)
        ]
        gas_tags = {g.tag for g in gas}
        # Idle/released worker pools must retain mineral income too, not only donors.
        gas_budget = max(0, len(workers) - mineral_worker_floor(WorkerMode.GAS))
        gas_quotas = {}
        for site in gas:
            gas_quotas[site.tag] = min(gas_worker_quota(mode), site.ideal_harvesters, gas_budget)
            gas_budget -= gas_quotas[site.tag]
        mineral_base = {m.tag: b.tag for b in bases for m in fields[b.tag]}
        current, counts, patch_counts = {}, Counter(), Counter()
        for worker in workers:
            target = worker.order_target
            job = (
                ("gas", target)
                if target in gas_tags
                else ("minerals", mineral_base[target])
                if target in mineral_base
                else ("minerals", target)
                if target in base_tags and getattr(worker, "is_carrying_minerals", False)
                else self._mining.get(worker.tag)
            )
            if worker.is_idle or worker.tag in released:
                job = None
            if job and (job[1] in (gas_tags if job[0] == "gas" else base_tags)):
                current[worker.tag] = job
                counts[job] += 1
                if target in mineral_base:
                    patch_counts[target] += 1
        pool = [w for w in workers if w.tag not in current]
        for kind, sites, quota in (("gas", gas, gas_worker_quota(mode)), ("minerals", bases, None)):
            for site in sites:
                desired = gas_quotas[site.tag] if quota is not None else site.ideal_harvesters
                surplus = max(0, counts[(kind, site.tag)] - desired)
                donors = [w for w in workers if current.get(w.tag) == (kind, site.tag)]
                for worker in sorted(donors, key=site.distance_to, reverse=True)[:surplus]:
                    del current[worker.tag]
                    counts[(kind, site.tag)] -= 1
                    pool.append(worker)
        for site in gas:
            desired = gas_quotas[site.tag]
            while counts[("gas", site.tag)] < desired:
                candidates = pool or [
                    w
                    for w in workers
                    if current.get(w.tag, ("", 0))[0] == "minerals"
                    and counts[current[w.tag]] > mineral_worker_floor(mode)
                ]
                if not candidates:
                    break
                worker = min(candidates, key=site.distance_to)
                if worker in pool:
                    pool.remove(worker)
                elif worker.tag in current:
                    counts[current.pop(worker.tag)] -= 1
                self._gather(worker, site, "gas", site)
                current[worker.tag] = "gas", site.tag
                counts[("gas", site.tag)] += 1
        for worker in pool:
            if not bases:
                self._mining.pop(worker.tag, None)
                continue
            base = min(
                bases,
                key=lambda b: (
                    counts[("minerals", b.tag)] >= b.ideal_harvesters,
                    counts[("minerals", b.tag)] / b.ideal_harvesters,
                    b.distance_to(worker),
                ),
            )
            patch = min(
                fields[base.tag], key=lambda m: (patch_counts[m.tag], worker.distance_to(m))
            )
            self._gather(worker, patch, "minerals", base)
            counts[("minerals", base.tag)] += 1
            patch_counts[patch.tag] += 1
