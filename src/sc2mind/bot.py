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

        reserve = (0, 0)
        if invest is not Invest.NONE:
            if await self.executor.invest(invest):
                self.mind.invest_done()
            else:
                reserve = self.executor.cost(invest)

        await self.executor.reflexes(self.mind.plan, reserve)
        self.executor.army(stance, snap)

        if iteration % 20 == 0:
            await self.distribute_workers()

    async def on_end(self, game_result: Result) -> None:
        self.tracer("end", {"t": self.time, "result": game_result.name})
        await self.mind.close()
        self.tracer.close()
