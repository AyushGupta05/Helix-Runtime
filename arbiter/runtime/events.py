from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from arbiter.core.contracts import MissionEvent


class EventLogger:
    def __init__(self, events_path: str) -> None:
        self.path = Path(events_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def emit(self, event: MissionEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def tail(self) -> Iterable[str]:
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield line.rstrip("\n")

    def last_events(self, limit: int = 50) -> list[str]:
        lines = list(self.tail())
        return lines[-limit:]

    def as_json(self) -> list[dict]:
        return [json.loads(line) for line in self.tail()]

