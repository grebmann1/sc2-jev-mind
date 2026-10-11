from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import Config
from .mind import Mind
from .system1 import JevReflex, OfflineReflex
from .system2 import OfflineReasoner, OpenAIReasoner
from .trace import JsonlTrace


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Play SC2 with Jev (System 1) + OpenAI (System 2).")
    p.add_argument("--map", default="AcropolisLE")
    p.add_argument("--enemy", default="random", choices=["terran", "zerg", "protoss", "random"])
    p.add_argument("--difficulty", default="Medium", help="Built-in AI: VeryEasy .. CheatInsane")
    p.add_argument("--offline", action="store_true", help="Rule-based stand-ins for both models")
    p.add_argument("--no-realtime", action="store_true", help="Step as fast as possible (models lag more)")
    p.add_argument("--runs-dir", default="runs")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env(offline=args.offline)

    if not cfg.offline:
        missing = [k for k in ("TYPESAFE_API_KEY", "OPENAI_API_KEY") if not os.environ.get(k)]
        if missing:
            print(f"Missing {', '.join(missing)}; set them or pass --offline.", file=sys.stderr)
            return 2

    from sc2 import maps
    from sc2.data import Difficulty, Race
    from sc2.main import run_game
    from sc2.player import Bot, Computer

    from .bot import DualMindBot

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.runs_dir) / stamp
    trace = JsonlTrace(run_dir / "trace.jsonl")

    reflex = OfflineReflex() if cfg.offline else JevReflex(cfg.jev_model)
    reasoner = OfflineReasoner() if cfg.offline else OpenAIReasoner(cfg.openai_model)
    bot = DualMindBot(Mind(cfg, reflex, reasoner, trace), trace)

    result = run_game(
        maps.get(args.map),
        [Bot(Race.Terran, bot, name="sc2mind"), Computer(Race[args.enemy.title()], Difficulty[args.difficulty])],
        realtime=not args.no_realtime,
        save_replay_as=str(run_dir / "replay.SC2Replay"),
    )
    print(f"Result: {result}  (trace: {run_dir / 'trace.jsonl'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
