"""Export recorded SC2 positions for the Zana replay viewer, without model calls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def annotations(directory: Path) -> dict:
    """Read optional bot evidence; tolerate the last partial trace line."""
    result = {}
    if (directory / "result.json").exists():
        result = json.loads((directory / "result.json").read_text())
    trace = []
    path = directory / "trace.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                trace.append(row)
    return {
        "result": result.get("result", "Unknown"),
        "memorySaved": result.get("memory_saved", False),
        "enemy": result.get("enemy", "unknown"),
        "difficulty": result.get("difficulty", "Unknown"),
        "opening": result.get("opening", "Unknown"),
        "offline": result.get("offline", False),
        "plans": [r for r in trace if r.get("kind") == "s2"],
        "decisions": [r for r in trace if r.get("kind") == "s1"],
    }


def write_export(output: Path, data: dict) -> None:
    """Publish complete files atomically so the plugin never reads half a replay."""
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        k: v
        for k, v in data.items()
        if k not in ("frames", "terrain", "pathing", "types", "plans", "decisions")
    }
    metadata.update(
        frames=len(data["frames"]), plans=len(data["plans"]), decisions=len(data["decisions"])
    )
    for filename, payload in (("data.json", data), ("metadata.json", metadata)):
        temporary = output / (filename + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
        temporary.replace(output / filename)


async def export(replay: Path, output: Path, step: int = 44) -> None:
    # Imports stay here: inspecting the export helpers requires neither SC2 nor keys.
    from s2clientprotocol import sc2api_pb2 as api
    from sc2.sc2process import SC2Process

    from .engine import engine_runner

    def grid(image):
        return {
            "width": image.size.x,
            "height": image.size.y,
            "bits": image.bits_per_pixel,
            "data": list(image.data),
        }

    with engine_runner():
        async with SC2Process() as controller:
            payload = replay.read_bytes()
            info = (
                await controller._execute(
                    replay_info=api.RequestReplayInfo(replay_data=payload, download_data=True)
                )
            ).replay_info
            if info.HasField("error"):
                raise RuntimeError(info.error_details)
            started = await controller._execute(
                start_replay=api.RequestStartReplay(
                    replay_data=payload,
                    observed_player_id=1,
                    disable_fog=True,
                    realtime=False,
                    options=api.InterfaceOptions(
                        raw=True,
                        score=True,
                        show_cloaked=True,
                        show_burrowed_shadows=True,
                        show_placeholders=True,
                    ),
                )
            )
            if started.start_replay.HasField("error"):
                raise RuntimeError(started.start_replay.error_details)
            game = (await controller._execute(game_info=api.RequestGameInfo())).game_info
            raw_data = (await controller._execute(data=api.RequestData(unit_type_id=True))).data
            types = {
                str(u.unit_id): {"name": u.name, "structure": 8 in u.attributes}
                for u in raw_data.units
            }
            frames = []
            limit = info.game_duration_loops
            while True:
                response = await controller._execute(observation=api.RequestObservation())
                observation = response.observation.observation
                common = observation.player_common
                units = []
                for unit in observation.raw_data.units:
                    name = types.get(str(unit.unit_type), {}).get("name", "")
                    if unit.owner not in (1, 2) and not any(
                        word in name for word in ("Mineral", "Vespene")
                    ):
                        continue
                    units.append(
                        [
                            str(unit.tag),
                            unit.unit_type,
                            unit.owner,
                            round(unit.pos.x, 2),
                            round(unit.pos.y, 2),
                            round(unit.radius, 2),
                            round(unit.health / unit.health_max, 2) if unit.health_max else 1,
                            round(unit.build_progress, 2),
                            unit.is_flying,
                        ]
                    )
                frames.append(
                    {
                        "t": round(observation.game_loop / 22.4, 2),
                        "units": units,
                        "minerals": common.minerals,
                        "gas": common.vespene,
                        "supply": common.food_used,
                        "cap": common.food_cap,
                        "army": common.food_army,
                        "workers": common.food_workers,
                    }
                )
                if len(frames) % 25 == 0:
                    print(
                        json.dumps(
                            {
                                "progress": round(min(1, observation.game_loop / max(1, limit)), 3),
                                "seconds": frames[-1]["t"],
                            }
                        ),
                        flush=True,
                    )
                if response.observation.player_result or observation.game_loop >= limit:
                    break
                await controller._execute(
                    step=api.RequestStep(count=min(step, limit - observation.game_loop))
                )
            evidence = annotations(replay.parent)
            # For foreign replays, retain the engine's outcome if bot reports are absent.
            if evidence["result"] == "Unknown":
                player = next((p for p in info.player_info if p.player_info.player_id == 1), None)
                if player is not None:
                    evidence["result"] = api.Result.Name(player.player_result.result)
            data = {
                "schemaVersion": 1,
                "map": info.map_name,
                "version": info.game_version,
                "duration": frames[-1]["t"],
                "source": replay.name,
                **evidence,
                "types": types,
                "terrain": grid(game.start_raw.terrain_height),
                "pathing": grid(game.start_raw.pathing_grid),
                "bounds": {
                    "minX": game.start_raw.playable_area.p0.x,
                    "minY": game.start_raw.playable_area.p0.y,
                    "maxX": game.start_raw.playable_area.p1.x,
                    "maxY": game.start_raw.playable_area.p1.y,
                },
                "frames": frames,
            }
            write_export(output, data)
            print(json.dumps({"progress": 1, "frames": len(frames)}), flush=True)
            await controller._execute(quit=api.RequestQuit())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.replay.is_file():
        parser.error("replay does not exist")
    asyncio.run(export(args.replay, args.output or args.replay.parent / "viewer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
