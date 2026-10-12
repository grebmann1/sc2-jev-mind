from __future__ import annotations

from sc2.bot_ai import BotAI
from sc2.data import Result

from .actions import Invest, economy_investment
from .executor import Executor
from .mind import Mind
from .squad_executor import SquadExecutor
from .state import EnemyMemory, snapshot_from_bot
from .trace import JsonlTrace
from .workers import WorkerManager


class DualMindBot(BotAI):
    def __init__(self, mind: Mind, trace: JsonlTrace) -> None:
        super().__init__()
        self.mind = mind
        self.tracer = trace
        self.memory = EnemyMemory()
        self.executor: Executor

    async def on_start(self) -> None:
        self.executor = Executor(self)
        self.squad_executor = SquadExecutor(self, self.executor, self.memory, self.tracer)
        self.worker_manager = WorkerManager(self, self.executor, self.tracer)
        self.tracer("start", {"map": self.game_info.map_name, "enemy_race": self.enemy_race.name})

    async def on_step(self, iteration: int) -> None:
        snap = snapshot_from_bot(self, self.memory)
        invest, stance = self.mind.tick(snap)

        if self.mind.cfg.multi_squad:
            self.squad_executor.assign(snap)
        # Keep the active worker scout out of mining redistribution and builder selection.
        if self.mind.cfg.economy_brain:
            economy = self.mind.tick_economy(self.worker_manager.observation(snap, self.mind.plan))
            invest = economy_investment(invest, economy.priority)
            work = self.worker_manager.step(economy, invest)
            if self.mind.episode:
                self.mind.episode.worker_commands.update(work["issued"])
                if work["logged"]:
                    self.mind.episode.economy_modes[economy.mode.value] += 1
        elif iteration % 20 == 0:
            workers = self.workers
            try:
                self.workers = workers.filter(lambda w: w.tag not in self.executor.reserved_workers)
                await self.distribute_workers()
            finally:
                self.workers = workers
        await self.executor.economy(self.mind.plan)

        reserve = (0, 0)
        pending = invest
        if invest is not Invest.NONE:
            if await self.executor.invest(invest, snap):
                self.mind.invest_done(invest)
                pending = Invest.NONE
                self.tracer("investment", {"t": self.time, "invest": invest.value})
            else:
                reserve = self.executor.cost(invest)

        self.executor.production(self.mind.plan, reserve, pending)
        if self.mind.cfg.multi_squad:
            observations = self.squad_executor.observations(snap, stance, self.mind.plan)
            decisions = self.mind.tick_squads(observations)
            self.squad_executor.execute(observations, decisions)
        else:
            self.executor.scout()
            self.executor.army(stance, snap)

        if iteration % 100 == 0:
            self.tracer("snapshot", {"t": self.time, **snap.to_dict()})

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        self.memory.forget(unit_tag)
        if hasattr(self, "squad_executor"):
            role = self.squad_executor.lost(unit_tag)
            if role is not None and self.mind.episode:
                self.mind.episode.squad_losses[role.value] += 1

    async def on_end(self, game_result: Result) -> None:
        try:
            self.tracer("end", {"t": self.time, "result": game_result.name})
            await self.mind.finish_match(game_result.name)
        finally:
            await self.mind.close()
            self.tracer.close()
