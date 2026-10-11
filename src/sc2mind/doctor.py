"""Read-only startup checks; never launches SC2 or contacts either model provider."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config
from .engine import resolve_engine_runner

API_KEYS = ("TYPESAFE_API_KEY", "OPENAI_API_KEY")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def installation_path() -> tuple[Path, str]:
    # Importing sc2.paths is safe; accessing Paths.EXECUTABLE can call sys.exit.
    from sc2.paths import BASEDIR, BINPATH, PF, get_user_sc2_install

    if PF not in BASEDIR:
        raise ValueError(f"Unsupported SC2 platform: {PF}")
    path = os.environ.get("SC2PATH") or get_user_sc2_install() or BASEDIR[PF]
    return Path(path).expanduser(), BINPATH[PF]


def preflight(map_name: str, cfg: Config) -> list[Check]:
    checks = [Check("python", sys.version_info >= (3, 12), sys.version.split()[0])]
    try:
        runner = resolve_engine_runner()
    except ValueError as error:
        checks.append(Check("engine_runner", False, str(error)))
    else:
        if runner:
            checks.append(Check("engine_runner", sys.platform == "linux", runner))
    try:
        base, binary_name = installation_path()
    except (ImportError, ValueError, KeyError, AttributeError) as error:
        checks.append(Check("sc2", False, str(error)))
    else:
        versions = base / "Versions"
        builds = sorted(
            (p for p in versions.glob("Base*") if p.name[4:].isdigit()),
            key=lambda p: int(p.name[4:]),
            reverse=True,
        )
        executable = builds[0] / binary_name if builds else None
        installed = (
            executable is not None and executable.is_file() and int(builds[0].name[4:]) >= 55958
        )
        checks.append(Check("sc2", installed, str(executable or base)))
        maps = base / ("maps" if (base / "maps").exists() else "Maps")
        candidates = [*maps.glob("*.SC2Map"), *maps.glob("*/*.SC2Map")]
        found = next((p for p in candidates if p.stem == map_name), None)
        checks.append(
            Check(
                "map",
                found is not None,
                str(found) if found else f"Put {map_name}.SC2Map in {maps}",
            )
        )
    if cfg.offline:
        checks.append(Check("models", True, "Offline rules; no API keys or network required"))
    else:
        for key in API_KEYS:
            present = bool(os.environ.get(key, "").strip())
            checks.append(Check(key, present, "configured" if present else "missing"))
    return checks


def report(checks: list[Check]) -> dict:
    return {"ready": all(c.ok for c in checks), "checks": [asdict(c) for c in checks]}
