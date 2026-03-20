from __future__ import annotations

import json

from arbiter.core.contracts import ArbiterState, Bid, TaskNode
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.schemas import BidView, MissionView, TaskView, TimelineEventView


def materialize_mission_view(repo_path: str, mission_id: str) -> MissionView:
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        mission_row = store.fetch_mission()
        if mission_row is None:
            raise ValueError(f"Mission {mission_id} not found.")
        checkpoint = store.fetch_latest_checkpoint()
        if checkpoint:
            state = ArbiterState.model_validate_json(checkpoint["state_json"])
        else:
            spec = json.loads(mission_row["spec_json"])
            state = ArbiterState.model_validate({"mission": spec, "summary": {"mission_id": mission_id}})
        bids_rows = store.fetch_all("bids")
        bids = []
        for row in bids_rows:
            bid = Bid.model_validate_json(row["payload_json"])
            bids.append(
                BidView(
                    bid_id=bid.bid_id,
                    task_id=bid.task_id,
                    role=bid.role,
                    strategy_family=bid.strategy_family,
                    strategy_summary=bid.strategy_summary,
                    score=bid.score,
                    risk=bid.risk,
                    cost=bid.cost,
                    estimated_runtime_seconds=bid.estimated_runtime_seconds,
                    touched_files=bid.touched_files,
                    rejection_reason=bid.rejection_reason,
                    selected=bool(row["selected"]),
                    standby=bool(row["standby"]),
                )
            )
        tasks = [
            TaskView(
                task_id=task.task_id,
                title=task.title,
                task_type=task.task_type.value,
                status=task.status.value,
                requirement_level=task.requirement_level.value,
                dependencies=task.dependencies,
            )
            for task in state.tasks
        ]
        event_rows = store.fetch_ordered("events", "id DESC")
        events = [
            TimelineEventView(
                id=row["id"],
                event_type=json.loads(row["payload_json"])["event_type"],
                created_at=row["created_at"],
                message=json.loads(row["payload_json"])["message"],
                payload=json.loads(row["payload_json"]).get("payload", {}),
            )
            for row in reversed(event_rows[:200])
        ]
        validation_report = state.validation_report.model_dump(mode="json") if state.validation_report else None
        return MissionView(
            mission_id=mission_id,
            repo_path=state.mission.repo_path,
            objective=state.mission.objective,
            outcome=state.outcome.value if state.outcome else None,
            run_state=state.control.run_state.value,
            active_phase=state.active_phase.value,
            active_bid_round=state.active_bid_round,
            branch_name=state.summary.branch_name,
            head_commit=state.summary.head_commit,
            latest_diff_summary=state.latest_diff_summary,
            winner_bid_id=state.winner_bid_id,
            standby_bid_id=state.standby_bid_id,
            decision_history=state.decision_history,
            failed_attempt_history=state.summary.failed_attempt_history,
            tasks=tasks,
            bids=bids,
            events=events,
            validation_report=validation_report,
        )
    finally:
        store.close()
