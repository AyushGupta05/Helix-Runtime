from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from arbiter.core.contracts import ReplayRecord
from arbiter.runtime.store import MissionStore


def replay_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ReplayManager:
    def __init__(self, store: MissionStore, replay_dir: str, mode: str = "off") -> None:
        self.store = store
        self.replay_dir = Path(replay_dir)
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode

    def record(self, lane: str, prompt: dict[str, Any], response: dict[str, Any]) -> ReplayRecord:
        record = ReplayRecord(lane=lane, key=replay_key(prompt), prompt=prompt, response=response)
        self.store.add_replay_record(lane=lane, replay_key=record.key, payload=record)
        target = self.replay_dir / f"{record.key}.json"
        target.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def load(self, prompt: dict[str, Any]) -> dict[str, Any] | None:
        key = replay_key(prompt)
        target = self.replay_dir / f"{key}.json"
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            return data["response"]
        return None
