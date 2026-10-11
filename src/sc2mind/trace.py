from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class JsonlTrace:
    """Append-only decision log: one line per System 1 / System 2 event."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = path.open("a", encoding="utf-8")

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self._f.write(json.dumps({"kind": kind, "wall": time.time(), **data}, default=str) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()
