"""Optional Linux engine runner for hosts that need explicit CPU emulation."""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager


def resolve_engine_runner() -> str | None:
    command = os.environ.get("SC2_ENGINE_RUNNER", "").strip()
    if not command:
        return None
    runner = shutil.which(command)
    if runner is None:
        raise ValueError(f"SC2_ENGINE_RUNNER is not an executable: {command}")
    return runner


@contextmanager
def engine_runner():
    runner = resolve_engine_runner()
    if runner is None:
        yield
        return

    from sc2 import paths

    if paths.PF != "Linux":
        raise ValueError("SC2_ENGINE_RUNNER requires the Linux SC2 engine")
    original = paths.get_runner_args
    # BurnySC2 prepends these arguments to the engine command. Python and the
    # model clients keep using the container's normal interpreter.
    paths.get_runner_args = lambda cwd: [runner]
    try:
        yield
    finally:
        paths.get_runner_args = original
