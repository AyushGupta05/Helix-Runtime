from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from arbiter.core.contracts import ActivePhase, PolicyState
from arbiter.runtime.store import MissionStore


def _copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def migrate_legacy_mission(paths, mission_id: str) -> None:
    if Path(paths.db_path).exists() or not paths.legacy_root_dir:
        return
    legacy_root = Path(paths.legacy_root_dir)
    legacy_db = legacy_root / "mission.db"
    if not legacy_db.exists():
        return

    _copy_if_exists(legacy_root / "events.jsonl", Path(paths.events_path))
    _copy_if_exists(legacy_root / "metadata.json", Path(paths.metadata_path))
    _copy_if_exists(legacy_root / "reports", Path(paths.reports_dir))
    _copy_if_exists(legacy_root / "replay", Path(paths.replay_dir))

    legacy = sqlite3.connect(legacy_db)
    legacy.row_factory = sqlite3.Row
    store = MissionStore(paths.db_path)
    try:
        mission = legacy.execute("SELECT * FROM mission LIMIT 1").fetchone()
        if mission is None:
            return
        spec = json.loads(mission["spec_json"])
        summary = json.loads(mission["summary_json"])
        store.upsert_mission(
            mission_id=mission["mission_id"],
            status=mission["status"],
            repo_path=mission["repo_path"],
            objective=spec["objective"],
            branch_name=mission["branch_name"],
            outcome=mission["outcome"],
            spec=type("SpecProxy", (), {"model_dump_json": lambda self: json.dumps(spec)})(),
            summary=type("SummaryProxy", (), {"model_dump_json": lambda self: json.dumps(summary)})(),
            created_at=spec.get("created_at"),
        )

        control = legacy.execute("SELECT * FROM mission_control WHERE mission_id = ?", (mission_id,)).fetchone()
        if control:
            store.upsert_control_state(
                mission_id=mission_id,
                run_state=control["run_state"],
                requested_action=control["requested_action"],
                reason=control["reason"],
                updated_at=control["updated_at"],
            )

        checkpoint = legacy.execute("SELECT * FROM mission_state_checkpoints ORDER BY id DESC LIMIT 1").fetchone()
        state = json.loads(checkpoint["state_json"]) if checkpoint else {}
        failure = state.get("failure_context") or {}
        validation = state.get("validation_report") or {}
        store.upsert_runtime(
            mission_id=mission_id,
            active_phase=state.get("active_phase", ActivePhase.IDLE.value),
            active_task_id=state.get("active_task_id"),
            active_bid_round=state.get("active_bid_round", 0),
            simulation_round=0,
            recovery_round=state.get("recovery_round", 0),
            winner_bid_id=state.get("winner_bid_id"),
            standby_bid_id=state.get("standby_bid_id"),
            latest_diff_summary=state.get("latest_diff_summary", ""),
            stop_reason=summary.get("stop_reason"),
            policy_state=PolicyState.CLEAR.value,
            current_risk_score=summary.get("current_risk_score", 0.0),
            simulation_summary=None,
            latest_validation_task_id=validation.get("task_id"),
            latest_failure_task_id=failure.get("task_id"),
            accepted_checkpoint_id=None,
        )

        for row in legacy.execute("SELECT * FROM tasks").fetchall():
            payload = json.loads(row["payload_json"])
            store.save_task(
                mission_id=mission_id,
                task=type("TaskProxy", (), {"model_dump_json": lambda self, payload=payload: json.dumps(payload)})(),
                task_id=payload["task_id"],
                title=payload["title"],
                task_type=payload["task_type"],
                status=payload["status"],
                required=payload["requirement_level"] == "required",
                dependencies=payload.get("dependencies", []),
            )

        for row in legacy.execute("SELECT * FROM bids").fetchall():
            payload = json.loads(row["payload_json"])
            status = "winner" if row["selected"] else "standby" if row["standby"] else payload.get("status", "generated")
            store.save_bid(
                mission_id=mission_id,
                bid=type("BidProxy", (), {"model_dump_json": lambda self, payload=payload: json.dumps(payload)})(),
                bid_id=payload["bid_id"],
                task_id=payload["task_id"],
                role=payload["role"],
                strategy_family=payload["strategy_family"],
                score=payload.get("score"),
                risk=payload["risk"],
                cost=payload["cost"],
                confidence=payload["confidence"],
                is_winner=bool(row["selected"]),
                is_standby=bool(row["standby"]),
                status=status,
                round_index=state.get("active_bid_round", 0),
            )

        for row in legacy.execute("SELECT * FROM execution_steps").fetchall():
            payload = json.loads(row["payload_json"])
            store.save_execution_step(
                mission_id=mission_id,
                step=type("StepProxy", (), {"model_dump_json": lambda self, payload=payload: json.dumps(payload)})(),
                step_id=payload["step_id"],
                task_id=payload["task_id"],
                action=payload.get("action_type", "unknown"),
                result=json.dumps(payload.get("output_payload", {})),
                timestamp=payload.get("created_at", state.get("mission", {}).get("created_at", "")),
            )

        for row in legacy.execute("SELECT * FROM validation_reports").fetchall():
            payload = json.loads(row["payload_json"])
            store.save_validation_report(
                mission_id=mission_id,
                report=type("ValidationProxy", (), {"model_dump_json": lambda self, payload=payload: json.dumps(payload)})(),
                record_id=f"{payload['task_id']}-legacy",
                task_id=payload["task_id"],
                passed=payload["passed"],
                details=payload.get("notes", []),
                timestamp=state.get("mission", {}).get("created_at", ""),
            )

        for row in legacy.execute("SELECT * FROM failure_contexts").fetchall():
            payload = json.loads(row["payload_json"])
            store.save_failure_context(
                mission_id=mission_id,
                failure=type("FailureProxy", (), {"model_dump_json": lambda self, payload=payload: json.dumps(payload)})(),
                record_id=f"{payload['task_id']}-legacy",
                task_id=payload["task_id"],
                failure_type=payload["failure_type"],
                details=payload["details"],
                diff_summary=payload["diff_summary"],
                strategy_family=payload.get("strategy_family"),
                timestamp=payload.get("created_at", state.get("mission", {}).get("created_at", "")),
            )

        for row in legacy.execute("SELECT * FROM repo_state_checkpoints WHERE accepted = 1").fetchall():
            payload = json.loads(row["payload_json"])
            checkpoint_payload = payload | {"diff_summary": payload.get("summary", "")}
            store.save_accepted_checkpoint(
                mission_id,
                type("CheckpointProxy", (), {"checkpoint_id": payload["checkpoint_id"], "label": payload["label"], "commit_sha": payload["commit_sha"], "diff_summary": payload.get("summary", ""), "created_at": type("Ts", (), {"isoformat": lambda self, value=payload.get("created_at", spec.get("created_at", "")): value})(), "model_dump_json": lambda self, payload=checkpoint_payload: json.dumps(payload)})(),
            )

        for row in legacy.execute("SELECT * FROM events ORDER BY id ASC").fetchall():
            store.append_event(mission_id=mission_id, event_type=row["event_type"], payload=json.loads(row["payload_json"]), created_at=row["created_at"])

        store.refresh_mission_view(mission_id)
    finally:
        store.close()
        legacy.close()
