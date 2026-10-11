from __future__ import annotations

from sc2.bot_ai import BotAI
from sc2.data import Result

from .actions import Invest
from .executor import Executor
from .mind import Mind
from .state import EnemyMemory, snapshot_from_bot
from .trace import JsonlTrace


class DualMindBot(BotAI):
    def __init__(self, mind: Mind, trace: JsonlTrace) -> None:
        super().__init__()
        self.mind = mind
        self.tracer = trace
        self.memory = EnemyMemory()
        self.executor: Executor

    async def on_start(self) -> None:
        self.executor = Executor(self)
        self.tracer("start", {"map": self.game_info.map_name, "enemy_race": self.enemy_race.name})

    async def on_step(self, iteration: int) -> None:
        snap = snapshot_from_bot(self, self.memory)
        invest, stance = self.mind.tick(snap)

        # Rebalance before selecting builders/scouts so their orders aren't overwritten.
        if iteration % 20 == 0:
            await self.distribute_workers()
        await self.executor.economy(self.mind.plan)

        reserve = (0, 0)
        pending = invest
        if invest is not Invest.NONE:
            if await self.executor.invest(invest, snap):
                self.mind.invest_done()
                pending = Invest.NONE
                self.tracer("investment", {"t": self.time, "invest": invest.value})
            else:
                reserve = self.executor.cost(invest)

        self.executor.production(self.mind.plan, reserve, pending)
        self.executor.scout()
        self.executor.army(stance, snap)

        if iteration % 100 == 0:
            self.tracer("snapshot", {"t": self.time, **snap.to_dict()})

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        self.memory.forget(unit_tag)

    async def on_end(self, game_result: Result) -> None:
        try:
            self.tracer("end", {"t": self.time, "result": game_result.name})
            await self.mind.finish_match(game_result.name)
        finally:
            await self.mind.close()
            self.tracer.close()
