from __future__ import annotations

import json
from pathlib import Path

from arbiter.core.contracts import MissionEvent
from arbiter.runtime.events import EventLogger
from arbiter.runtime.store import MissionStore


class PersistenceCoordinator:
    def __init__(self, mission_id: str, store: MissionStore, events: EventLogger) -> None:
        self.mission_id = mission_id
        self.store = store
        self.events = events

    def append_event(self, event: MissionEvent, refresh_view: bool = False) -> int:
        payload = event.model_dump(mode="json")
        event_id = self.store.append_event(
            mission_id=self.mission_id,
            event_type=event.event_type,
            payload=payload,
            created_at=event.created_at.isoformat(),
        )
        event.payload = {**event.payload, "event_id": event_id}
        self.events.emit(event)
        self.store.mark_event_jsonl_written(event_id)
        if refresh_view:
            self.store.refresh_mission_view(self.mission_id)
        return event_id

    def reconcile_jsonl(self) -> None:
        pending = self.store.fetch_events_needing_jsonl(self.mission_id)
        if not pending:
            return
        existing_ids: set[int] = set()
        path = Path(self.events.path)
        if path.exists():
            for line in self.events.tail():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_id = payload.get("payload", {}).get("event_id")
                if isinstance(event_id, int):
                    existing_ids.add(event_id)
        for row in pending:
            payload = json.loads(row["payload_json"])
            payload.setdefault("payload", {})["event_id"] = row["id"]
            if row["id"] not in existing_ids:
                self.events.emit(MissionEvent.model_validate(payload))
            self.store.mark_event_jsonl_written(row["id"])
