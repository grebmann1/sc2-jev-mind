"""Serial real-match evaluation; a dry run needs neither SC2 nor API keys."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .cli import DIFFICULTIES
from .cli import main as play


def match_schedule(races: list[str], difficulties: list[str], games: int) -> list[dict]:
    return [
        {"enemy": race, "difficulty": difficulty, "seed": seed}
        for difficulty in difficulties
        for race in races
        for seed in range(1, games + 1)
    ]


def summarize(records: list[dict]) -> dict:
    completed = [r for r in records if r.get("result") in ("Victory", "Defeat", "Tie")]
    wins = sum(r["result"] == "Victory" for r in completed)
    return {
        "attempted": len(records),
        "completed": len(completed),
        "wins": wins,
        "win_rate": wins / len(completed) if completed else None,
        "errors": len(records) - len(completed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--races",
        nargs="+",
        choices=["terran", "zerg", "protoss"],
        default=["terran", "zerg", "protoss"],
    )
    parser.add_argument("--difficulties", nargs="+", choices=DIFFICULTIES, default=["Medium"])
    parser.add_argument("--games", type=int, default=3, help="Games per race/difficulty")
    parser.add_argument("--map", default="AcropolisLE")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print match schedule without launching"
    )
    speed = parser.add_mutually_exclusive_group()
    speed.add_argument("--realtime", action="store_true", help="Run at real-time speed")
    speed.add_argument("--no-realtime", dest="realtime", action="store_false")
    parser.set_defaults(realtime=None)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument(
        "--learn",
        action="store_true",
        help="Enable cross-match memory and opening learning (training run)",
    )
    parser.add_argument("--memory-dir", default="memory")
    args = parser.parse_args(argv)
    realtime = args.realtime if args.realtime is not None else not args.offline
    if args.games < 1:
        parser.error("--games must be positive")
    schedule = match_schedule(args.races, args.difficulties, args.games)
    if args.dry_run:
        print(
            json.dumps(
                {"matches": schedule, "offline": args.offline, "learning_enabled": args.learn},
                indent=2,
            )
        )
        return 0
    # Check prerequisites once before creating the experiment or launching any matches.
    from .config import Config
    from .doctor import preflight

    try:
        cfg = Config.from_env(offline=args.offline)
    except ValueError as error:
        parser.error(str(error))
    checks = preflight(args.map, cfg)
    if not all(c.ok for c in checks):
        for check in checks:
            if not check.ok:
                print(f"{check.name}: {check.detail}")
        return 2
    root = Path(args.runs_dir) / datetime.now(UTC).strftime("benchmark-%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    records: list[dict] = []
    for index, match in enumerate(schedule):
        directory = root / str(index + 1)
        command = [
            "--map",
            args.map,
            "--enemy",
            match["enemy"],
            "--difficulty",
            match["difficulty"],
            "--seed",
            str(match["seed"]),
            "--runs-dir",
            str(directory),
        ]
        if args.offline:
            command.append("--offline")
        if args.learn:
            command.extend(["--memory-dir", args.memory_dir])
        else:
            command.append("--no-memory")
        if not realtime:
            command.append("--no-realtime")
        code = play(command)
        files = list(directory.glob("*/result.json"))
        record = {**match, "exit_code": code}
        if files and code == 0:
            record.update(json.loads(files[0].read_text()))
            record["run_dir"] = str(files[0].parent)
        records.append(record)
        report = {
            **summarize(records),
            "offline": args.offline,
            "realtime": realtime,
            "map": args.map,
            "planned": len(schedule),
            "learning_enabled": args.learn,
            "matches": records,
        }
        (root / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({**summarize(records), "report": str(root / "benchmark.json")}, indent=2))
    return 1 if summarize(records)["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
