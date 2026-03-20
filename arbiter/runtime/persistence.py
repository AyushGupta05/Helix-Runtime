from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from arbiter.core.contracts import MissionEvent, ModelInvocation, TraceEntry
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

    def save_model_invocation(self, payload: dict, refresh_view: bool = False) -> str:
        invocation = ModelInvocation(
            invocation_id=payload.get("invocation_id") or uuid4().hex,
            mission_id=self.mission_id,
            task_id=payload.get("task_id"),
            bid_id=payload.get("bid_id"),
            provider=payload["provider"],
            lane=payload["lane"],
            model_id=payload.get("model_id"),
            invocation_kind=payload["invocation_kind"],
            status=payload["status"],
            generation_mode=payload.get("generation_mode", "provider_model"),
            started_at=payload.get("started_at"),
            completed_at=payload.get("completed_at"),
            prompt_preview=payload.get("prompt_preview"),
            response_preview=payload.get("response_preview"),
            raw_usage=payload.get("raw_usage", {}),
            token_usage=payload.get("token_usage"),
            cost_usage=payload.get("cost_usage"),
            usage_unavailable_reason=payload.get("usage_unavailable_reason"),
            error=payload.get("error"),
        )
        self.store.save_model_invocation(
            mission_id=self.mission_id,
            invocation=invocation,
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            bid_id=invocation.bid_id,
            provider=invocation.provider,
            lane=invocation.lane,
            model_id=invocation.model_id,
            invocation_kind=invocation.invocation_kind,
            status=invocation.status,
            generation_mode=invocation.generation_mode.value,
            started_at=invocation.started_at,
            completed_at=invocation.completed_at,
            prompt_preview=invocation.prompt_preview,
            response_preview=invocation.response_preview,
            raw_usage=invocation.raw_usage,
            token_usage=invocation.token_usage,
            cost_usage=invocation.cost_usage,
            usage_unavailable_reason=invocation.usage_unavailable_reason,
            error=invocation.error,
        )
        if refresh_view:
            self.store.refresh_mission_view(self.mission_id)
        return invocation.invocation_id

    def append_trace(self, trace_type: str, title: str, message: str, *, status: str = "info", task_id: str | None = None, bid_id: str | None = None, provider: str | None = None, lane: str | None = None, refresh_view: bool = False, **payload) -> int:
        trace = TraceEntry(
            trace_type=trace_type,
            title=title,
            message=message,
            status=status,
            task_id=task_id,
            bid_id=bid_id,
            provider=provider,
            lane=lane,
            payload=payload,
        )
        trace_id = self.store.save_trace_entry(
            mission_id=self.mission_id,
            trace=trace,
            task_id=task_id,
            bid_id=bid_id,
            trace_type=trace_type,
            title=title,
            message=message,
            status=status,
            provider=provider,
            lane=lane,
        )
        self.append_event(
            MissionEvent(
                event_type=trace_type,
                mission_id=self.mission_id,
                message=message,
                payload={
                    "trace_id": trace_id,
                    "title": title,
                    "status": status,
                    "task_id": task_id,
                    "bid_id": bid_id,
                    "provider": provider,
                    "lane": lane,
                    **payload,
                },
            ),
            refresh_view=refresh_view,
        )
        return trace_id

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
