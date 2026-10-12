from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from .actions import Role
from .config import Config
from .doctor import preflight, report
from .economy import JevEconomyReflex, OfflineEconomyReflex
from .engine import engine_runner, resolve_engine_runner
from .learning import OPENINGS, choose_opening, opening_plan
from .memory import Episode, MatchContext, MemoryStore
from .mind import Mind
from .squads import JevSquadReflex, OfflineSquadReflex
from .system1 import JevReflex, OfflineReflex
from .system2 import OfflineReasoner, OpenAIReasoner
from .trace import JsonlTrace

DIFFICULTIES = (
    "VeryEasy",
    "Easy",
    "Medium",
    "MediumHard",
    "Hard",
    "Harder",
    "VeryHard",
    "CheatVision",
    "CheatMoney",
    "CheatInsane",
)


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def _seed(value: str) -> int:
    seed = int(value)
    if not 0 <= seed <= 2**32 - 1:
        raise argparse.ArgumentTypeError("must be an unsigned 32-bit integer")
    return seed


def _engine_id() -> str:
    from .doctor import installation_path

    directory, _ = installation_path()
    builds = (
        [int(p.name[4:]) for p in (directory / "Versions").glob("Base*") if p.name[4:].isdigit()]
        if directory
        else []
    )
    runner = resolve_engine_runner()
    backend = f";runner={Path(runner).name}" if runner else ""
    return f"SC2-Base{max(builds) if builds else 'unknown'};burnysc2-{version('burnysc2')}{backend}"


def _controller_id(cfg: Config) -> str:
    settings = asdict(cfg)
    if cfg.offline:
        settings.pop("jev_model")
        settings.pop("openai_model")
    return json.dumps(
        {"implementation": "offline-v4" if cfg.offline else "jev-openai-v4", "settings": settings},
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Play SC2 with Jev (System 1) + OpenAI (System 2).")
    p.add_argument("--map", default="AcropolisLE")
    p.add_argument("--enemy", default="random", choices=["terran", "zerg", "protoss", "random"])
    p.add_argument("--difficulty", default="Medium", choices=DIFFICULTIES)
    p.add_argument("--offline", action="store_true", help="Rule-based stand-ins for both models")
    p.add_argument("--single-army", action="store_true", help="Disable independent squad brains")
    p.add_argument(
        "--no-economy-brain", action="store_true", help="Use the legacy worker distributor"
    )
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--doctor", action="store_true", help="Check setup without launching SC2")
    modes.add_argument(
        "--simulate",
        action="store_true",
        help="Exercise offline decisions on scripted snapshots; no SC2 needed",
    )
    modes.add_argument(
        "--memory-info",
        action="store_true",
        help="Read local learning statistics without SC2 or API keys",
    )
    p.add_argument("--memory-dir", default=os.environ.get("SC2MIND_MEMORY_DIR", "memory"))
    p.add_argument("--no-memory", action="store_true", help="Disable match storage and retrieval")
    p.add_argument(
        "--no-learning",
        action="store_true",
        help="Disable automatic opening selection; retain memory",
    )
    p.add_argument("--opening", choices=["auto", *OPENINGS], default="auto")
    p.add_argument("--no-analysis", action="store_true", help="Skip the post-game review")
    p.add_argument(
        "--analysis-offline",
        action="store_true",
        help="Post-game measurements without a model call",
    )
    p.add_argument(
        "--analysis-timeout",
        type=_positive,
        default=os.environ.get("ANALYSIS_TIMEOUT_S", "120"),
        help="Wall-clock seconds allowed for the post-game model review",
    )
    p.add_argument("--json", action="store_true", help="JSON output for --doctor or --simulate")
    p.add_argument(
        "--no-realtime", action="store_true", help="Step as fast as possible (models lag more)"
    )
    p.add_argument("--runs-dir", default="runs")
    p.add_argument("--seed", type=_seed, default=1, help="SC2 map generation seed (default: 1)")
    p.add_argument(
        "--game-time-limit",
        type=_positive,
        default=1200,
        help="Stop after this many game seconds (default: 1200)",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.memory_info:
        try:
            print(
                json.dumps(MemoryStore(Path(args.memory_dir) / "matches.sqlite3").info(), indent=2)
            )
            return 0
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Cannot read memory: {error}", file=sys.stderr)
            return 1
    try:
        cfg = Config.from_env(offline=args.offline or args.simulate)
        if args.single_army:
            cfg = replace(cfg, multi_squad=False)
        if args.no_economy_brain:
            cfg = replace(cfg, economy_brain=False)
    except ValueError as error:
        print(f"Invalid configuration: {error}", file=sys.stderr)
        return 2

    if args.doctor:
        status = report(preflight(args.map, cfg))
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            for check in status["checks"]:
                print(f"{'OK' if check['ok'] else 'MISSING'} {check['name']}: {check['detail']}")
        return 0 if status["ready"] else 1

    if not args.simulate:
        checks = preflight(args.map, cfg)
        if not all(c.ok for c in checks):
            for check in checks:
                if not check.ok:
                    print(f"{check.name}: {check.detail}", file=sys.stderr)
            print(
                "Run --doctor --offline for game setup, or --simulate to test without SC2.",
                file=sys.stderr,
            )
            return 2

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    run_dir = Path(args.runs_dir) / stamp
    trace = JsonlTrace(run_dir / "trace.jsonl")
    if args.simulate:
        from .scenarios import run_scenarios

        try:
            results = asyncio.run(run_scenarios(trace))
            (run_dir / "result.json").write_text(json.dumps(results, indent=2) + "\n")
            if args.json:
                print(json.dumps({**results, "run_dir": str(run_dir)}, indent=2))
            else:
                print(f"Scripted snapshot checks: {results['passed']}/{results['total']} passed")
                print(f"Report: {run_dir / 'result.json'}")
            return 0 if results["passed"] == results["total"] else 1
        finally:
            trace.close()

    from sc2 import maps
    from sc2.data import Difficulty, Race
    from sc2.main import run_game
    from sc2.player import Bot, Computer

    from .bot import DualMindBot

    mind = None
    squad_reflexes = {}
    reflex = reasoner = economy_reflex = None
    try:
        context = MatchContext(
            args.map,
            args.enemy.title(),
            args.difficulty,
            _controller_id(cfg),
            _engine_id(),
            not args.no_realtime,
            args.game_time_limit,
            seed=args.seed,
        )
        memory = None
        stats = {}
        experience = {}
        if not args.no_memory:
            try:
                memory = MemoryStore(Path(args.memory_dir) / "matches.sqlite3")
                memory.initialize()
                experience = memory.retrieve(context)
                stats = experience["opening_stats"]
            except Exception as error:
                logging.warning(
                    "Persistent memory unavailable: %s; continuing without it", type(error).__name__
                )
                memory = None
        opening, selection_reason = choose_opening(
            stats, enabled=not args.no_learning and memory is not None
        )
        if args.opening != "auto":
            opening, selection_reason = args.opening, "explicit opening selection"
        reflex = (
            OfflineReflex() if cfg.offline else JevReflex(cfg.jev_model, timeout_s=cfg.s1_timeout_s)
        )
        reasoner = (
            OfflineReasoner()
            if cfg.offline
            else OpenAIReasoner(cfg.openai_model, timeout_s=cfg.s2_timeout_s)
        )
        if cfg.multi_squad:
            for role in Role:
                squad_reflexes[role] = (
                    OfflineSquadReflex()
                    if cfg.offline
                    else JevSquadReflex(cfg.jev_model, timeout_s=cfg.s1_timeout_s)
                )
        if cfg.economy_brain:
            economy_reflex = (
                OfflineEconomyReflex()
                if cfg.offline
                else JevEconomyReflex(cfg.jev_model, timeout_s=cfg.s1_timeout_s)
            )
        mind = Mind(
            cfg,
            reflex,
            reasoner,
            trace,
            memory=memory,
            episode=Episode(stamp, context, opening),
            initial_plan=opening_plan(opening),
            experience=experience,
            squad_reflexes=squad_reflexes,
            economy_reflex=economy_reflex,
        )
        bot = DualMindBot(mind, trace)
        trace(
            "config",
            {
                "map": args.map,
                "enemy": args.enemy,
                "difficulty": args.difficulty,
                "offline": cfg.offline,
                "realtime": not args.no_realtime,
                "jev_model": cfg.jev_model,
                "openai_model": cfg.openai_model,
                "seed": args.seed,
                "opening": opening,
                "selection_reason": selection_reason,
                "memory_enabled": memory is not None,
                "engine": context.engine,
            },
        )
        with engine_runner():
            result = run_game(
                maps.get(args.map),
                [
                    Bot(Race.Terran, bot, name="sc2mind"),
                    Computer(Race[args.enemy.title()], Difficulty[args.difficulty]),
                ],
                realtime=not args.no_realtime,
                game_time_limit=args.game_time_limit,
                random_seed=args.seed,
                save_replay_as=str(run_dir / "replay.SC2Replay"),
            )
        summary = {
            "result": getattr(result, "name", str(result)),
            "map": args.map,
            "enemy": args.enemy,
            "difficulty": args.difficulty,
            "offline": cfg.offline,
            "seed": args.seed,
            "realtime": not args.no_realtime,
            "game_time_limit": args.game_time_limit,
            "opening": opening,
            "memory_saved": mind.memory_saved,
            "selection_reason": selection_reason,
            "match_id": stamp,
            "context": asdict(mind.episode.context),
        }
        (run_dir / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
        summary["analysis_status"] = "disabled" if args.no_analysis else "skipped"
        if (
            not args.no_analysis
            and mind.episode.observations
            and summary["result"] in {"Victory", "Defeat", "Tie"}
        ):
            try:
                from .analysis import analyze_match

                print("Game complete. Reviewing measured evidence…")
                analysis = asyncio.run(
                    analyze_match(
                        run_dir,
                        record=mind.episode.record(
                            summary["result"], mind.plan.model_dump(mode="json")
                        ),
                        memory=memory,
                        offline=cfg.offline or args.analysis_offline,
                        model=cfg.openai_model,
                        timeout_s=args.analysis_timeout,
                    )
                )
                summary["analysis_status"] = analysis["status"]
                print(f"Post-game review: {run_dir / 'analysis.md'} ({analysis['status']})")
            except Exception as error:
                summary["analysis_status"] = "failed"
                logging.warning(
                    "Post-game analysis unavailable: %s; match result retained",
                    type(error).__name__,
                )
        (run_dir / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Result: {result}  (trace: {run_dir / 'trace.jsonl'})")
        return 0
    except Exception as error:
        if not trace.closed:
            trace("run_error", {"type": type(error).__name__, "error": str(error)})
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "result": "Error",
                    "error_type": type(error).__name__,
                    "map": args.map,
                    "enemy": args.enemy,
                    "difficulty": args.difficulty,
                    "seed": args.seed,
                    "offline": cfg.offline,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Game failed: {error}", file=sys.stderr)
        return 1
    finally:
        if mind is not None:
            asyncio.run(mind.close())
        else:

            async def close_clients():
                for client in (reflex, reasoner, economy_reflex, *squad_reflexes.values()):
                    close = getattr(client, "close", None)
                    if close:
                        try:
                            await close()
                        except Exception as error:
                            logging.warning("Closing model client failed: %s", type(error).__name__)

            asyncio.run(close_clients())
        trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
