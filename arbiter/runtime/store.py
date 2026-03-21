from __future__ import annotations

from datetime import datetime
import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel

from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ActivePhase,
    ArbiterState,
    MissionControlState,
    MissionOutcome,
    MissionStateCheckpoint,
    MissionSummary,
    PolicyState,
    ReplayRecord,
    RepoStateCheckpoint,
    RunState,
    SimulationSummary,
    utc_now,
)


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


def _metric_total(values: dict[str, Any] | None, *, preferred_keys: tuple[str, ...]) -> float:
    if not values:
        return 0.0
    preferred_value = None
    for key in preferred_keys:
        value = values.get(key)
        if value is not None:
            preferred_value = float(value)
            break
        dotted_matches = [
            float(candidate)
            for existing_key, candidate in values.items()
            if existing_key.lower().endswith(f".{key.lower()}")
        ]
        if dotted_matches:
            preferred_value = max(dotted_matches)
            break
    if preferred_value is not None:
        return preferred_value
    return sum(float(value) for value in values.values())


def _cost_status(
    *,
    total_tokens: float,
    total_cost: float,
    invocation_count: int,
    cost_unavailable_invocation_count: int,
) -> str:
    if cost_unavailable_invocation_count > 0:
        return "partial" if total_cost > 0 else "unavailable"
    if total_cost > 0:
        return "available"
    if total_tokens > 0 or invocation_count > 0:
        return "none"
    return "none"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(start: str | None, end: str | None) -> float:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _command_status(exit_code: int | None) -> str:
    if exit_code is None:
        return "pending"
    return "passed" if int(exit_code) == 0 else "failed"


def _command_results_summary(results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for result in results or []:
        command = result.get("command", [])
        summarized.append(
            {
                "command": " ".join(command) if isinstance(command, list) else str(command),
                "exit_code": result.get("exit_code"),
                "status": _command_status(result.get("exit_code")),
                "duration_seconds": float(result.get("duration_seconds", 0.0) or 0.0),
                "stdout_excerpt": (result.get("stdout") or "")[:1200],
                "stderr_excerpt": (result.get("stderr") or "")[:1200],
            }
        )
    return summarized


class MissionStore:
    def __init__(self, db_path: str, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self._lock = RLock()
        if read_only:
            uri_path = Path(db_path).resolve().as_posix()
            self.connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, check_same_thread=False)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        if read_only:
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.connection.execute("PRAGMA foreign_keys = ON")
        else:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.connection.execute("PRAGMA foreign_keys = ON")
            self._init_schema()
            self._ensure_column("mission_runtime", "worktree_state_json", "TEXT")
            self._ensure_column("mission_runtime", "bidding_state_json", "TEXT")
            self._ensure_column("mission_runtime", "civic_connection_json", "TEXT")
            self._ensure_column("mission_runtime", "civic_capabilities_json", "TEXT")
            self._ensure_column("mission_runtime", "available_skills_json", "TEXT")
            self._ensure_column("mission_runtime", "skill_health_json", "TEXT")
            self._ensure_column("mission_runtime", "skill_outputs_json", "TEXT")
            self._ensure_column("model_invocations", "generation_mode", "TEXT")
            self._ensure_column("model_invocations", "usage_unavailable_reason", "TEXT")

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.connection.execute(query, params)
            self.connection.commit()
            return cursor

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(query, params).fetchone()

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(query, params).fetchall()

    def _init_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS mission (
                id TEXT PRIMARY KEY,
                repo_path TEXT NOT NULL,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                branch_name TEXT,
                outcome TEXT,
                spec_json TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mission_runtime (
                mission_id TEXT PRIMARY KEY,
                active_phase TEXT NOT NULL,
                active_task_id TEXT,
                active_bid_round INTEGER NOT NULL DEFAULT 0,
                simulation_round INTEGER NOT NULL DEFAULT 0,
                recovery_round INTEGER NOT NULL DEFAULT 0,
                winner_bid_id TEXT,
                standby_bid_id TEXT,
                latest_diff_summary TEXT NOT NULL DEFAULT '',
                stop_reason TEXT,
                policy_state TEXT NOT NULL DEFAULT 'clear',
                current_risk_score REAL NOT NULL DEFAULT 0,
                simulation_summary_json TEXT,
                worktree_state_json TEXT,
                bidding_state_json TEXT,
                civic_connection_json TEXT,
                civic_capabilities_json TEXT,
                available_skills_json TEXT,
                skill_health_json TEXT,
                skill_outputs_json TEXT,
                latest_validation_task_id TEXT,
                latest_failure_task_id TEXT,
                accepted_checkpoint_id TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mission_control (
                mission_id TEXT PRIMARY KEY,
                run_state TEXT NOT NULL,
                requested_action TEXT,
                reason TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                required INTEGER NOT NULL,
                dependencies TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS bids (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                role TEXT NOT NULL,
                strategy_family TEXT NOT NULL,
                score REAL,
                risk REAL NOT NULL,
                cost REAL NOT NULL,
                confidence REAL NOT NULL,
                is_winner INTEGER NOT NULL DEFAULT 0,
                is_standby INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                round_index INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS execution_steps (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                action TEXT NOT NULL,
                result TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS governed_bid_envelopes (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                bid_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS governed_action_records (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                bid_id TEXT,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS validation_reports (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                passed INTEGER NOT NULL,
                details TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS failure_contexts (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                failure_type TEXT NOT NULL,
                details TEXT NOT NULL,
                diff_summary TEXT NOT NULL,
                strategy_family TEXT,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS accepted_checkpoints (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                label TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                diff_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mission_state_checkpoints (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                label TEXT NOT NULL,
                active_phase TEXT NOT NULL,
                active_task_id TEXT,
                active_bid_round INTEGER NOT NULL DEFAULT 0,
                recovery_round INTEGER NOT NULL DEFAULT 0,
                winner_bid_id TEXT,
                standby_bid_id TEXT,
                accepted_checkpoint_id TEXT,
                run_state TEXT NOT NULL,
                policy_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS repo_state_checkpoints (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                label TEXT NOT NULL,
                checkpoint_kind TEXT NOT NULL,
                branch_name TEXT,
                commit_sha TEXT,
                accepted INTEGER NOT NULL DEFAULT 0,
                diff_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                jsonl_written INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mission_view_cache (
                mission_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS replay_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                lane TEXT NOT NULL,
                replay_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_invocations (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                bid_id TEXT,
                provider TEXT NOT NULL,
                lane TEXT NOT NULL,
                model_id TEXT,
                invocation_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                prompt_preview TEXT,
                response_preview TEXT,
                raw_usage_json TEXT NOT NULL,
                token_usage_json TEXT NOT NULL,
                cost_usage_json TEXT NOT NULL,
                generation_mode TEXT,
                usage_unavailable_reason TEXT,
                error TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS trace_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                bid_id TEXT,
                trace_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                provider TEXT,
                lane TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES mission(id) ON DELETE CASCADE
            );
                """
            )
            self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        with self._lock:
            columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in columns:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def upsert_mission(self, mission_id: str, status: str, repo_path: str, objective: str, branch_name: str | None, outcome: str | None, spec: BaseModel, summary: BaseModel, created_at: str | None = None) -> None:
        now = utc_now().isoformat()
        self._execute(
            """
            INSERT INTO mission (id, repo_path, objective, status, created_at, updated_at, branch_name, outcome, spec_json, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                repo_path=excluded.repo_path,
                objective=excluded.objective,
                status=excluded.status,
                updated_at=excluded.updated_at,
                branch_name=excluded.branch_name,
                outcome=excluded.outcome,
                spec_json=excluded.spec_json,
                summary_json=excluded.summary_json
            """,
            (mission_id, repo_path, objective, status, created_at or now, now, branch_name, outcome, spec.model_dump_json(), summary.model_dump_json()),
        )

    def upsert_runtime(self, mission_id: str, *, active_phase: str, active_task_id: str | None, active_bid_round: int, simulation_round: int, recovery_round: int, winner_bid_id: str | None, standby_bid_id: str | None, latest_diff_summary: str, stop_reason: str | None, policy_state: str, current_risk_score: float, simulation_summary: SimulationSummary | None, worktree_state: dict[str, Any] | None, bidding_state: dict[str, Any] | None, civic_connection: dict[str, Any] | None, civic_capabilities: list[dict[str, Any]] | None, available_skills: list[str] | None, skill_health: dict[str, Any] | None, skill_outputs: dict[str, Any] | None, latest_validation_task_id: str | None, latest_failure_task_id: str | None, accepted_checkpoint_id: str | None) -> None:
        self._execute(
            """
            INSERT INTO mission_runtime (
                mission_id, active_phase, active_task_id, active_bid_round, simulation_round, recovery_round,
                winner_bid_id, standby_bid_id, latest_diff_summary, stop_reason, policy_state, current_risk_score,
                simulation_summary_json, worktree_state_json, bidding_state_json, civic_connection_json, civic_capabilities_json,
                available_skills_json, skill_health_json, skill_outputs_json, latest_validation_task_id, latest_failure_task_id,
                accepted_checkpoint_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                active_phase=excluded.active_phase,
                active_task_id=excluded.active_task_id,
                active_bid_round=excluded.active_bid_round,
                simulation_round=excluded.simulation_round,
                recovery_round=excluded.recovery_round,
                winner_bid_id=excluded.winner_bid_id,
                standby_bid_id=excluded.standby_bid_id,
                latest_diff_summary=excluded.latest_diff_summary,
                stop_reason=excluded.stop_reason,
                policy_state=excluded.policy_state,
                current_risk_score=excluded.current_risk_score,
                simulation_summary_json=excluded.simulation_summary_json,
                worktree_state_json=excluded.worktree_state_json,
                bidding_state_json=excluded.bidding_state_json,
                civic_connection_json=excluded.civic_connection_json,
                civic_capabilities_json=excluded.civic_capabilities_json,
                available_skills_json=excluded.available_skills_json,
                skill_health_json=excluded.skill_health_json,
                skill_outputs_json=excluded.skill_outputs_json,
                latest_validation_task_id=excluded.latest_validation_task_id,
                latest_failure_task_id=excluded.latest_failure_task_id,
                accepted_checkpoint_id=excluded.accepted_checkpoint_id,
                updated_at=excluded.updated_at
            """,
            (
                mission_id,
                active_phase,
                active_task_id,
                active_bid_round,
                simulation_round,
                recovery_round,
                winner_bid_id,
                standby_bid_id,
                latest_diff_summary,
                stop_reason,
                policy_state,
                current_risk_score,
                simulation_summary.model_dump_json() if simulation_summary else None,
                _dump(worktree_state or {}),
                _dump(bidding_state or {}),
                _dump(civic_connection or {}),
                _dump(civic_capabilities or []),
                _dump(available_skills or []),
                _dump(skill_health or {}),
                _dump(skill_outputs or {}),
                latest_validation_task_id,
                latest_failure_task_id,
                accepted_checkpoint_id,
                utc_now().isoformat(),
            ),
        )

    def upsert_control_state(self, mission_id: str, run_state: str, requested_action: str | None, reason: str | None, updated_at: str) -> None:
        self._execute(
            """
            INSERT INTO mission_control (mission_id, run_state, requested_action, reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                run_state=excluded.run_state,
                requested_action=excluded.requested_action,
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            (mission_id, run_state, requested_action, reason, updated_at),
        )

    def save_task(self, mission_id: str, task: BaseModel, *, task_id: str, title: str, task_type: str, status: str, required: bool, dependencies: list[str]) -> None:
        self._execute(
            """
            INSERT INTO tasks (id, mission_id, title, type, status, required, dependencies, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                status=excluded.status,
                required=excluded.required,
                dependencies=excluded.dependencies,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (task_id, mission_id, title, task_type, status, int(required), _dump(dependencies), task.model_dump_json(), utc_now().isoformat()),
        )

    def save_bid(self, mission_id: str, bid: BaseModel, *, bid_id: str, task_id: str, role: str, strategy_family: str, score: float | None, risk: float, cost: float, confidence: float, is_winner: bool, is_standby: bool, status: str, round_index: int) -> None:
        self._execute(
            """
            INSERT INTO bids (id, mission_id, task_id, role, strategy_family, score, risk, cost, confidence, is_winner, is_standby, status, round_index, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_id=excluded.task_id,
                role=excluded.role,
                strategy_family=excluded.strategy_family,
                score=excluded.score,
                risk=excluded.risk,
                cost=excluded.cost,
                confidence=excluded.confidence,
                is_winner=excluded.is_winner,
                is_standby=excluded.is_standby,
                status=excluded.status,
                round_index=excluded.round_index,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (bid_id, mission_id, task_id, role, strategy_family, score, risk, cost, confidence, int(is_winner), int(is_standby), status, round_index, bid.model_dump_json(), utc_now().isoformat()),
        )

    def save_execution_step(self, mission_id: str, step: BaseModel, *, step_id: str, task_id: str, action: str, result: str, timestamp: str) -> None:
        self._execute(
            """
            INSERT INTO execution_steps (id, mission_id, task_id, action, result, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_id=excluded.task_id,
                action=excluded.action,
                result=excluded.result,
                timestamp=excluded.timestamp,
                payload_json=excluded.payload_json
            """,
            (step_id, mission_id, task_id, action, result, timestamp, step.model_dump_json()),
        )

    def save_governed_bid_envelope(self, mission_id: str, envelope: BaseModel, *, envelope_id: str, task_id: str, bid_id: str, status: str, created_at: str) -> None:
        self._execute(
            """
            INSERT INTO governed_bid_envelopes (id, mission_id, task_id, bid_id, status, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_id=excluded.task_id,
                bid_id=excluded.bid_id,
                status=excluded.status,
                created_at=excluded.created_at,
                payload_json=excluded.payload_json
            """,
            (envelope_id, mission_id, task_id, bid_id, status, created_at, envelope.model_dump_json()),
        )

    def save_governed_action_record(self, mission_id: str, record: BaseModel, *, action_id: str, task_id: str | None, bid_id: str | None, action_type: str, status: str, created_at: str) -> None:
        self._execute(
            """
            INSERT INTO governed_action_records (id, mission_id, task_id, bid_id, action_type, status, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_id=excluded.task_id,
                bid_id=excluded.bid_id,
                action_type=excluded.action_type,
                status=excluded.status,
                created_at=excluded.created_at,
                payload_json=excluded.payload_json
            """,
            (action_id, mission_id, task_id, bid_id, action_type, status, created_at, record.model_dump_json()),
        )

    def save_validation_report(self, mission_id: str, report: BaseModel, *, record_id: str, task_id: str, passed: bool, details: list[str], timestamp: str) -> None:
        self._execute(
            "INSERT INTO validation_reports (id, mission_id, task_id, passed, details, timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, mission_id, task_id, int(passed), _dump(details), timestamp, report.model_dump_json()),
        )

    def save_failure_context(self, mission_id: str, failure: BaseModel, *, record_id: str, task_id: str, failure_type: str, details: str, diff_summary: str, strategy_family: str | None, timestamp: str) -> None:
        self._execute(
            "INSERT INTO failure_contexts (id, mission_id, task_id, failure_type, details, diff_summary, strategy_family, timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, mission_id, task_id, failure_type, details, diff_summary, strategy_family, timestamp, failure.model_dump_json()),
        )

    def save_accepted_checkpoint(self, mission_id: str, checkpoint: AcceptedCheckpoint) -> None:
        self._execute(
            """
            INSERT INTO accepted_checkpoints (id, mission_id, label, commit_sha, diff_summary, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label=excluded.label,
                commit_sha=excluded.commit_sha,
                diff_summary=excluded.diff_summary,
                created_at=excluded.created_at,
                payload_json=excluded.payload_json
            """,
            (checkpoint.checkpoint_id, mission_id, checkpoint.label, checkpoint.commit_sha, checkpoint.diff_summary, checkpoint.created_at.isoformat(), checkpoint.model_dump_json()),
        )

    def touch_runtime(self, mission_id: str) -> None:
        self._execute(
            "UPDATE mission_runtime SET updated_at = ? WHERE mission_id = ?",
            (utc_now().isoformat(), mission_id),
        )

    def save_mission_state_checkpoint(self, checkpoint: MissionStateCheckpoint) -> None:
        self._execute(
            """
            INSERT INTO mission_state_checkpoints (
                id, mission_id, label, active_phase, active_task_id, active_bid_round, recovery_round,
                winner_bid_id, standby_bid_id, accepted_checkpoint_id, run_state, policy_state, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label=excluded.label,
                active_phase=excluded.active_phase,
                active_task_id=excluded.active_task_id,
                active_bid_round=excluded.active_bid_round,
                recovery_round=excluded.recovery_round,
                winner_bid_id=excluded.winner_bid_id,
                standby_bid_id=excluded.standby_bid_id,
                accepted_checkpoint_id=excluded.accepted_checkpoint_id,
                run_state=excluded.run_state,
                policy_state=excluded.policy_state,
                created_at=excluded.created_at,
                payload_json=excluded.payload_json
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.mission_id,
                checkpoint.label,
                checkpoint.active_phase.value,
                checkpoint.active_task_id,
                checkpoint.active_bid_round,
                checkpoint.recovery_round,
                checkpoint.winner_bid_id,
                checkpoint.standby_bid_id,
                checkpoint.accepted_checkpoint_id,
                checkpoint.run_state.value,
                checkpoint.policy_state.value,
                checkpoint.created_at.isoformat(),
                checkpoint.model_dump_json(),
            ),
        )

    def save_repo_state_checkpoint(self, checkpoint: RepoStateCheckpoint) -> None:
        self._execute(
            """
            INSERT INTO repo_state_checkpoints (
                id, mission_id, label, checkpoint_kind, branch_name, commit_sha, accepted, diff_summary, created_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label=excluded.label,
                checkpoint_kind=excluded.checkpoint_kind,
                branch_name=excluded.branch_name,
                commit_sha=excluded.commit_sha,
                accepted=excluded.accepted,
                diff_summary=excluded.diff_summary,
                created_at=excluded.created_at,
                payload_json=excluded.payload_json
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.mission_id,
                checkpoint.label,
                checkpoint.checkpoint_kind,
                checkpoint.branch_name,
                checkpoint.commit_sha,
                int(checkpoint.accepted),
                checkpoint.diff_summary,
                checkpoint.created_at.isoformat(),
                checkpoint.model_dump_json(),
            ),
        )

    def save_model_invocation(self, mission_id: str, invocation: BaseModel, *, invocation_id: str, task_id: str | None, bid_id: str | None, provider: str, lane: str, model_id: str | None, invocation_kind: str, status: str, generation_mode: str = "provider_model", started_at: str | None = None, completed_at: str | None = None, prompt_preview: str | None = None, response_preview: str | None = None, raw_usage: dict[str, Any] | None = None, token_usage: dict[str, int] | None = None, cost_usage: dict[str, float] | None = None, usage_unavailable_reason: str | None = None, error: str | None = None) -> None:
        self._execute(
            """
            INSERT INTO model_invocations (
                id, mission_id, task_id, bid_id, provider, lane, model_id, invocation_kind, status,
                started_at, completed_at, prompt_preview, response_preview, raw_usage_json, token_usage_json,
                cost_usage_json, generation_mode, usage_unavailable_reason, error, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_id=excluded.task_id,
                bid_id=excluded.bid_id,
                provider=excluded.provider,
                lane=excluded.lane,
                model_id=excluded.model_id,
                invocation_kind=excluded.invocation_kind,
                status=excluded.status,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at,
                prompt_preview=excluded.prompt_preview,
                response_preview=excluded.response_preview,
                raw_usage_json=excluded.raw_usage_json,
                token_usage_json=excluded.token_usage_json,
                cost_usage_json=excluded.cost_usage_json,
                generation_mode=excluded.generation_mode,
                usage_unavailable_reason=excluded.usage_unavailable_reason,
                error=excluded.error,
                payload_json=excluded.payload_json
            """,
            (
                invocation_id,
                mission_id,
                task_id,
                bid_id,
                provider,
                lane,
                model_id,
                invocation_kind,
                status,
                started_at,
                completed_at,
                prompt_preview,
                response_preview,
                _dump(raw_usage or {}),
                _dump(token_usage),
                _dump(cost_usage),
                generation_mode,
                usage_unavailable_reason,
                error,
                invocation.model_dump_json(),
            ),
        )

    def save_trace_entry(self, mission_id: str, trace: BaseModel, *, task_id: str | None, bid_id: str | None, trace_type: str, title: str, message: str, status: str, provider: str | None, lane: str | None) -> int:
        cursor = self._execute(
            """
            INSERT INTO trace_entries (mission_id, task_id, bid_id, trace_type, title, message, status, provider, lane, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                task_id,
                bid_id,
                trace_type,
                title,
                message,
                status,
                provider,
                lane,
                trace.model_dump_json(),
                getattr(trace, "created_at", utc_now()).isoformat(),
            ),
        )
        return int(cursor.lastrowid)

    def add_replay_record(self, mission_id: str | None, lane: str, replay_key: str, payload: ReplayRecord) -> None:
        self._execute(
            "INSERT INTO replay_records (mission_id, lane, replay_key, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (mission_id, lane, replay_key, payload.model_dump_json(), payload.created_at.isoformat()),
        )

    def append_event(self, mission_id: str, event_type: str, payload: dict[str, Any], created_at: str) -> int:
        cursor = self._execute(
            "INSERT INTO events (mission_id, event_type, payload_json, created_at, jsonl_written) VALUES (?, ?, ?, ?, 0)",
            (mission_id, event_type, _dump(payload), created_at),
        )
        return int(cursor.lastrowid)

    def mark_event_jsonl_written(self, event_id: int) -> None:
        self._execute("UPDATE events SET jsonl_written = 1 WHERE id = ?", (event_id,))

    def fetch_events_needing_jsonl(self, mission_id: str) -> list[sqlite3.Row]:
        return self._fetchall("SELECT * FROM events WHERE mission_id = ? AND jsonl_written = 0 ORDER BY id ASC", (mission_id,))

    def fetch_mission(self, mission_id: str | None = None) -> sqlite3.Row | None:
        if mission_id is None:
            return self._fetchone("SELECT * FROM mission LIMIT 1")
        return self._fetchone("SELECT * FROM mission WHERE id = ?", (mission_id,))

    def fetch_runtime(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM mission_runtime WHERE mission_id = ?", (mission_id,))

    def fetch_control_state(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM mission_control WHERE mission_id = ?", (mission_id,))

    def fetch_events_after(self, mission_id: str, last_id: int = 0) -> list[sqlite3.Row]:
        return self._fetchall("SELECT * FROM events WHERE mission_id = ? AND id > ? ORDER BY id ASC", (mission_id, last_id))

    def fetch_all(self, table: str, mission_id: str | None = None) -> list[sqlite3.Row]:
        if mission_id is None:
            return self._fetchall(f"SELECT * FROM {table}")
        key = "id" if table == "mission" else "mission_id"
        return self._fetchall(f"SELECT * FROM {table} WHERE {key} = ?", (mission_id,))

    def count_rows(self, table: str, mission_id: str | None = None) -> int:
        if mission_id is None:
            row = self._fetchone(f"SELECT COUNT(*) AS count FROM {table}")
            return int(row["count"]) if row is not None else 0
        key = "id" if table == "mission" else "mission_id"
        row = self._fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE {key} = ?", (mission_id,))
        return int(row["count"]) if row is not None else 0

    def fetch_ordered(self, table: str, order_by: str, mission_id: str | None = None) -> list[sqlite3.Row]:
        if mission_id is None:
            return self._fetchall(f"SELECT * FROM {table} ORDER BY {order_by}")
        key = "id" if table == "mission" else "mission_id"
        return self._fetchall(f"SELECT * FROM {table} WHERE {key} = ? ORDER BY {order_by}", (mission_id,))

    def fetch_latest_validation(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM validation_reports WHERE mission_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1", (mission_id,))

    def fetch_latest_failure(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM failure_contexts WHERE mission_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1", (mission_id,))

    def fetch_latest_checkpoint(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM accepted_checkpoints WHERE mission_id = ? ORDER BY created_at DESC, id DESC LIMIT 1", (mission_id,))

    def fetch_latest_mission_state_checkpoint(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone(
            "SELECT * FROM mission_state_checkpoints WHERE mission_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (mission_id,),
        )

    def fetch_latest_repo_state_checkpoint(self, mission_id: str) -> sqlite3.Row | None:
        return self._fetchone(
            "SELECT * FROM repo_state_checkpoints WHERE mission_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (mission_id,),
        )

    def fetch_model_invocations(self, mission_id: str, task_id: str | None = None) -> list[sqlite3.Row]:
        if task_id is None:
            return self._fetchall("SELECT * FROM model_invocations WHERE mission_id = ? ORDER BY started_at ASC, id ASC", (mission_id,))
        return self._fetchall("SELECT * FROM model_invocations WHERE mission_id = ? AND task_id = ? ORDER BY started_at ASC, id ASC", (mission_id, task_id))

    def fetch_trace_entries(self, mission_id: str, limit: int = 200, after_id: int = 0) -> list[sqlite3.Row]:
        rows = self._fetchall(
            "SELECT * FROM trace_entries WHERE mission_id = ? AND id > ? ORDER BY id DESC LIMIT ?",
            (mission_id, after_id, limit),
        )
        rows = list(rows)
        rows.reverse()
        return rows

    def _events_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM events WHERE mission_id = ? ORDER BY id DESC LIMIT 500", (mission_id,))
        rows.reverse()
        return [{"id": row["id"], "event_type": row["event_type"], "created_at": row["created_at"], "message": json.loads(row["payload_json"]).get("message", ""), "payload": json.loads(row["payload_json"]).get("payload", {})} for row in rows]

    def _trace_for_view(self, mission_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.fetch_trace_entries(mission_id, limit=limit)
        return [
            {
                "id": row["id"],
                "trace_type": row["trace_type"],
                "title": row["title"],
                "message": row["message"],
                "status": row["status"],
                "task_id": row["task_id"],
                "bid_id": row["bid_id"],
                "provider": row["provider"],
                "lane": row["lane"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]).get("payload", {}),
            }
            for row in rows
        ]

    def _execution_steps_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.fetch_ordered("execution_steps", "timestamp ASC", mission_id)]

    def _governed_bid_envelopes_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        envelopes = [json.loads(row["payload_json"]) for row in self.fetch_ordered("governed_bid_envelopes", "created_at ASC", mission_id)]
        bid_rows = {row["id"]: row for row in self.fetch_all("bids", mission_id)}
        for envelope in envelopes:
            bid_row = bid_rows.get(envelope.get("bid_id"))
            if bid_row:
                if not envelope.get("role"):
                    envelope["role"] = bid_row["role"]
                if not envelope.get("strategy_family"):
                    envelope["strategy_family"] = bid_row["strategy_family"]
        return envelopes

    def _governed_action_records_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.fetch_ordered("governed_action_records", "created_at ASC", mission_id)]

    def _checkpoints_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.fetch_ordered("accepted_checkpoints", "created_at ASC", mission_id)]

    def _mission_state_checkpoints_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["payload_json"])
            for row in self.fetch_ordered("mission_state_checkpoints", "created_at ASC, id ASC", mission_id)
        ]

    def _repo_state_checkpoints_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["payload_json"])
            for row in self.fetch_ordered("repo_state_checkpoints", "created_at ASC, id ASC", mission_id)
        ]

    def _mission_output(
        self,
        mission: sqlite3.Row,
        runtime: sqlite3.Row | None,
        checkpoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = checkpoints[-1] if checkpoints else None
        return {
            "branch_name": mission["branch_name"],
            "worktree_path": json.loads(runtime["worktree_state_json"]).get("worktree_path") if runtime and runtime["worktree_state_json"] else None,
            "accepted_checkpoint_id": latest.get("checkpoint_id") if latest else None,
            "accepted_commit": latest.get("commit_sha") if latest else None,
            "accepted_summary": latest.get("summary") if latest else None,
            "accepted_diff_summary": latest.get("diff_summary") if latest else None,
            "accepted_diff_patch": latest.get("diff_patch") if latest else None,
            "affected_files": latest.get("affected_files", []) if latest else [],
            "validator_results": latest.get("validator_results", []) if latest else [],
        }

    def _runtime_seconds(
        self,
        mission: sqlite3.Row,
        summary: dict[str, Any],
        control: sqlite3.Row | None,
        runtime: sqlite3.Row | None,
    ) -> float:
        recorded = float(summary.get("runtime_seconds") or 0.0)
        if recorded > 0:
            return recorded
        end_time = None
        if control and control["run_state"] == RunState.RUNNING.value:
            end_time = utc_now().isoformat()
        else:
            end_time = max(
                (
                    timestamp
                    for timestamp in (
                        mission["updated_at"],
                        runtime["updated_at"] if runtime else None,
                        control["updated_at"] if control else None,
                    )
                    if timestamp
                ),
                default=mission["updated_at"],
            )
        return _elapsed_seconds(mission["created_at"], end_time)

    def _history_metrics(
        self,
        runtime: sqlite3.Row | None,
        validation_report: dict[str, Any] | None,
        failure_count: int,
        accepted_checkpoints: list[dict[str, Any]],
        mission_state_checkpoints: list[dict[str, Any]],
        repo_state_checkpoints: list[dict[str, Any]],
        mission_output: dict[str, Any],
    ) -> dict[str, Any]:
        validation_commands = validation_report.get("command_results", []) if validation_report else []
        baseline_commands = validation_report.get("baseline_command_results", []) if validation_report else []
        validation_status = "pending"
        if validation_report is not None:
            validation_status = "passed" if validation_report.get("passed") else "failed"
        return {
            "checkpoint_count": len(accepted_checkpoints),
            "mission_checkpoint_count": len(mission_state_checkpoints),
            "repo_checkpoint_count": len(repo_state_checkpoints),
            "failure_count": failure_count,
            "recovery_count": runtime["recovery_round"] if runtime else 0,
            "changed_file_count": len(mission_output.get("affected_files", [])),
            "validation": {
                "status": validation_status,
                "passed": validation_report.get("passed") if validation_report is not None else None,
                "validator_count": len(validation_commands),
                "baseline_validator_count": len(baseline_commands),
                "commands": _command_results_summary(validation_commands),
                "baseline_commands": _command_results_summary(baseline_commands),
                "notes": list(validation_report.get("notes", [])) if validation_report else [],
            },
        }

    def _repo_insights(
        self,
        mission: sqlite3.Row,
        summary: dict[str, Any],
        latest_state_checkpoint: sqlite3.Row | None,
        validation_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        spec = json.loads(mission["spec_json"])
        checkpoint_payload = json.loads(latest_state_checkpoint["payload_json"]) if latest_state_checkpoint else {}
        state = checkpoint_payload.get("state", {})
        repo_snapshot = state.get("repo_snapshot") or {}
        capabilities = repo_snapshot.get("capabilities") or {}
        return {
            "runtime": capabilities.get("runtime", "unknown"),
            "branch": repo_snapshot.get("branch"),
            "tracking_branch": repo_snapshot.get("tracking_branch"),
            "head_commit": repo_snapshot.get("head_commit") or summary.get("head_commit"),
            "dirty": bool(repo_snapshot.get("dirty")),
            "remotes": dict(repo_snapshot.get("remotes", {})),
            "default_remote": repo_snapshot.get("default_remote"),
            "remote_provider": repo_snapshot.get("remote_provider"),
            "remote_slug": repo_snapshot.get("remote_slug"),
            "objective_hints": dict(repo_snapshot.get("objective_hints", {})),
            "tree_summary": list(repo_snapshot.get("tree_summary", [])),
            "dependency_files": list(repo_snapshot.get("dependency_files", [])),
            "complexity_hotspots": list(repo_snapshot.get("complexity_hotspots", [])),
            "failure_signals": list(repo_snapshot.get("failure_signals", [])),
            "risky_paths": list(capabilities.get("risky_paths", [])),
            "protected_interfaces": list(capabilities.get("protected_interfaces", [])),
            "protected_paths": list(spec.get("protected_paths", [])),
            "public_api_surface": list(spec.get("public_api_surface", [])),
            "toolchain": {
                "tests": [" ".join(command) for command in capabilities.get("test_commands", [])],
                "lint": [" ".join(command) for command in capabilities.get("lint_commands", [])],
                "static": [" ".join(command) for command in capabilities.get("static_commands", [])],
                "benchmarks": [" ".join(command) for command in capabilities.get("benchmark_commands", [])],
            },
            "baseline": {
                "tests": _command_results_summary(repo_snapshot.get("initial_test_results")),
                "lint": _command_results_summary(repo_snapshot.get("initial_lint_results")),
                "static": _command_results_summary(repo_snapshot.get("initial_static_results")),
            },
            "latest_validation": {
                "status": "pending"
                if validation_report is None
                else "passed"
                if validation_report.get("passed")
                else "failed",
                "notes": list(validation_report.get("notes", [])) if validation_report else [],
            },
        }

    def _civic_activity(
        self,
        summary: dict[str, Any],
        runtime: sqlite3.Row | None,
        failure_context: dict[str, Any] | None,
        governed_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        records = []
        for audit_id, payload in (summary.get("audit_summary") or {}).items():
            if not isinstance(payload, dict):
                continue
            record_payload = payload.get("payload") or {}
            records.append(
                {
                    "audit_id": audit_id,
                    "created_at": payload.get("created_at"),
                    "task_id": payload.get("task_id"),
                    "action_type": payload.get("action_type"),
                    "status": payload.get("status"),
                    "policy_state": payload.get("policy_state"),
                    "reasons": list(payload.get("reasons", [])),
                    "target": record_payload.get("file_scope") or record_payload.get("checkpoint"),
                    "payload": record_payload,
                }
            )
        for record in governed_actions or []:
            records.append(
                {
                    "audit_id": record.get("audit_id") or record.get("action_id"),
                    "created_at": record.get("created_at"),
                    "task_id": record.get("task_id"),
                    "action_type": record.get("action_type"),
                    "status": record.get("status"),
                    "policy_state": record.get("policy_state"),
                    "reasons": list(record.get("reasoning", [])),
                    "target": record.get("tool_name") or record.get("skill_id"),
                    "payload": record.get("input_payload", {}),
                }
            )
        records.sort(key=lambda item: item.get("created_at") or "")
        blocked_count = sum(1 for record in records if record.get("status") not in {"approved", "executed", "success"})
        return {
            "status": runtime["policy_state"] if runtime else PolicyState.CLEAR.value,
            "audit_count": len(records),
            "blocked_count": blocked_count,
            "latest_audit_id": records[-1]["audit_id"] if records else None,
            "recent_failure_audits": list((failure_context or {}).get("civic_action_history", [])),
            "ledger": records,
        }

    def _activity_summary(
        self,
        events: list[dict[str, Any]],
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stream: list[dict[str, Any]] = []
        for event in events[-20:]:
            payload = event.get("payload") or {}
            stream.append(
                {
                    "kind": "event",
                    "id": event.get("id"),
                    "created_at": event.get("created_at"),
                    "type": event.get("event_type"),
                    "title": payload.get("title") or event.get("event_type"),
                    "message": event.get("message"),
                    "status": payload.get("status"),
                    "task_id": payload.get("task_id"),
                    "bid_id": payload.get("bid_id"),
                    "provider": payload.get("provider"),
                    "lane": payload.get("lane"),
                }
            )
        for item in trace[-20:]:
            stream.append(
                {
                    "kind": "trace",
                    "id": item.get("id"),
                    "created_at": item.get("created_at"),
                    "type": item.get("trace_type"),
                    "title": item.get("title"),
                    "message": item.get("message"),
                    "status": item.get("status"),
                    "task_id": item.get("task_id"),
                    "bid_id": item.get("bid_id"),
                    "provider": item.get("provider"),
                    "lane": item.get("lane"),
                }
            )
        stream.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or 0))
        stream = stream[-30:]
        return {
            "event_count": len(events),
            "trace_count": len(trace),
            "latest_type": stream[-1]["type"] if stream else None,
            "stream": stream,
        }

    def _outcome_summary(
        self,
        *,
        mission: sqlite3.Row,
        runtime: sqlite3.Row | None,
        outcome: str | None,
        run_state: str,
        winner_bid: dict[str, Any] | None,
        validation_report: dict[str, Any] | None,
        failure_context: dict[str, Any] | None,
        mission_output: dict[str, Any],
        history_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        changed_files = mission_output.get("affected_files", [])
        checkpoint_count = history_metrics.get("checkpoint_count", 0)
        confidence = float((winner_bid or {}).get("confidence") or 0.55)
        confidence_reasons: list[str] = []
        if validation_report and validation_report.get("passed"):
            confidence = min(0.99, confidence + 0.2)
            confidence_reasons.append("Latest validator run passed.")
        if checkpoint_count:
            confidence = min(0.99, confidence + 0.05)
            confidence_reasons.append("Mission produced an accepted checkpoint.")
        if failure_context:
            confidence = max(0.05, confidence - 0.2)
            confidence_reasons.append("Recovery or validation failures occurred during the run.")
        if runtime and runtime["policy_state"] == PolicyState.BLOCKED.value:
            confidence = max(0.05, confidence - 0.25)
            confidence_reasons.append("Civic blocked at least one attempted action.")
        if not confidence_reasons:
            confidence_reasons.append("Confidence is based on the winning bid score and current mission state.")
        confidence_label = "high" if confidence >= 0.8 else "medium" if confidence >= 0.55 else "low"
        plain_summary = (
            f"Mission completed on {mission['branch_name']} with {len(changed_files)} changed files and {checkpoint_count} accepted checkpoints."
            if outcome == MissionOutcome.SUCCESS.value
            else f"Mission is {run_state} with {checkpoint_count} accepted checkpoints and {len(changed_files)} changed files ready for review."
            if outcome is None
            else f"Mission ended as {outcome.replace('_', ' ')} on {mission['branch_name']}."
        )
        risks: list[str] = []
        if failure_context and failure_context.get("details"):
            risks.append(str(failure_context["details"]))
        if runtime and runtime["stop_reason"]:
            risks.append(str(runtime["stop_reason"]))
        protected_touches = [
            path
            for path in changed_files
            if "api" in path.lower() or "public" in path.lower()
        ]
        if protected_touches:
            risks.append(f"Review public or protected interfaces touched: {', '.join(protected_touches[:3])}.")
        next_actions: list[str] = []
        if changed_files:
            next_actions.append(f"Review these files first: {', '.join(changed_files[:3])}.")
        if validation_report and not validation_report.get("passed"):
            next_actions.append("Inspect the latest validation report before promoting the branch.")
        elif changed_files:
            next_actions.append("Run any missing integration or staging checks before merging.")
        if protected_touches:
            next_actions.append("Confirm public API compatibility on the touched interface files.")
        if run_state != RunState.FINALIZED.value:
            next_actions.append("Keep monitoring the Live Market view until Arbiter finalizes the mission.")
        return {
            "status": outcome or run_state,
            "plain_summary": plain_summary,
            "branch_name": mission["branch_name"],
            "accepted_checkpoint_id": mission_output.get("accepted_checkpoint_id"),
            "changed_files": changed_files,
            "files_changed": len(changed_files),
            "checkpoint_count": checkpoint_count,
            "validation_status": history_metrics.get("validation", {}).get("status"),
            "confidence": round(confidence, 3),
            "confidence_label": confidence_label,
            "confidence_reasons": confidence_reasons,
            "risks": risks,
            "next_actions": next_actions,
        }

    def _mission_meta(
        self,
        *,
        mission: sqlite3.Row,
        runtime: sqlite3.Row | None,
        control: sqlite3.Row | None,
        run_state: str,
        active_task: dict[str, Any] | None,
        usage_summary: dict[str, Any],
        runtime_seconds: float,
        history_metrics: dict[str, Any],
        civic_activity: dict[str, Any],
        available_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        health = "healthy"
        if civic_activity.get("status") == PolicyState.BLOCKED.value:
            health = "blocked"
        elif history_metrics.get("failure_count", 0) > 0 or history_metrics.get("recovery_count", 0) > 0:
            health = "recovering"
        elif history_metrics.get("validation", {}).get("status") == "failed":
            health = "at_risk"
        return {
            "repo_name": Path(mission["repo_path"]).name,
            "repo_path": mission["repo_path"],
            "objective": mission["objective"],
            "status": mission["status"],
            "run_state": run_state,
            "active_phase": runtime["active_phase"] if runtime else ActivePhase.IDLE.value,
            "branch_name": mission["branch_name"],
            "head_commit": None,
            "runtime_seconds": runtime_seconds,
            "elapsed_seconds": runtime_seconds,
            "total_tokens": usage_summary.get("mission", {}).get("total_tokens", 0),
            "total_cost": usage_summary.get("mission", {}).get("total_cost", 0.0),
            "active_task_id": active_task.get("task_id") if active_task else None,
            "active_task_title": active_task.get("title") if active_task else None,
            "active_task_type": active_task.get("task_type") if active_task else None,
            "checkpoint_count": history_metrics.get("checkpoint_count", 0),
            "failure_count": history_metrics.get("failure_count", 0),
            "validator_status": history_metrics.get("validation", {}).get("status"),
            "civic_status": civic_activity.get("status"),
            "active_skill_count": len(available_skills or []),
            "mission_health": health,
        }

    def _usage_summary(self, mission_id: str, active_task_id: str | None, rows: list[sqlite3.Row] | None = None) -> dict[str, Any]:
        rows = rows if rows is not None else self.fetch_model_invocations(mission_id)
        mission_tokens: dict[str, int] = {}
        mission_costs: dict[str, float] = {}
        active_tokens: dict[str, int] = {}
        active_costs: dict[str, float] = {}
        mission_cost_unavailable_invocation_count = 0
        active_cost_unavailable_invocation_count = 0
        active_invocation_count = 0
        by_provider: dict[str, dict[str, Any]] = {}
        by_lane: dict[str, dict[str, Any]] = {}
        invocations: list[dict[str, Any]] = []
        for row in rows:
            invocation_payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            token_usage = json.loads(row["token_usage_json"]) if row["token_usage_json"] else None
            cost_usage = json.loads(row["cost_usage_json"]) if row["cost_usage_json"] else None
            total_tokens = int(_metric_total(token_usage, preferred_keys=("total_tokens",)))
            total_cost = _metric_total(cost_usage, preferred_keys=("usd", "total_cost"))
            has_token_usage = bool(token_usage) or total_tokens > 0
            cost_unavailable = has_token_usage and not cost_usage
            for key, value in (token_usage or {}).items():
                mission_tokens[key] = mission_tokens.get(key, 0) + int(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_tokens[key] = active_tokens.get(key, 0) + int(value)
            for key, value in (cost_usage or {}).items():
                mission_costs[key] = mission_costs.get(key, 0.0) + float(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_costs[key] = active_costs.get(key, 0.0) + float(value)
            if cost_unavailable:
                mission_cost_unavailable_invocation_count += 1
            if row["task_id"] and row["task_id"] == active_task_id:
                active_invocation_count += 1
                if cost_unavailable:
                    active_cost_unavailable_invocation_count += 1
            provider_bucket = by_provider.setdefault(
                row["provider"],
                {
                    "provider": row["provider"],
                    "token_usage": {},
                    "cost_usage": {},
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "invocation_count": 0,
                    "cost_unavailable_invocation_count": 0,
                },
            )
            lane_bucket = by_lane.setdefault(
                row["lane"],
                {
                    "lane": row["lane"],
                    "provider": row["provider"],
                    "token_usage": {},
                    "cost_usage": {},
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "invocation_count": 0,
                    "cost_unavailable_invocation_count": 0,
                },
            )
            provider_bucket["total_tokens"] += total_tokens
            provider_bucket["total_cost"] += total_cost
            provider_bucket["invocation_count"] += 1
            lane_bucket["total_tokens"] += total_tokens
            lane_bucket["total_cost"] += total_cost
            lane_bucket["invocation_count"] += 1
            if cost_unavailable:
                provider_bucket["cost_unavailable_invocation_count"] += 1
                lane_bucket["cost_unavailable_invocation_count"] += 1
            for bucket in (provider_bucket, lane_bucket):
                for key, value in (token_usage or {}).items():
                    bucket["token_usage"][key] = bucket["token_usage"].get(key, 0) + int(value)
                for key, value in (cost_usage or {}).items():
                    bucket["cost_usage"][key] = bucket["cost_usage"].get(key, 0.0) + float(value)
            invocations.append(
                {
                    "invocation_id": invocation_payload.get("invocation_id") or row["id"],
                    "record_id": row["id"],
                    "task_id": row["task_id"],
                    "bid_id": row["bid_id"],
                    "provider": row["provider"],
                    "lane": row["lane"],
                    "model_id": row["model_id"],
                    "invocation_kind": row["invocation_kind"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "prompt_preview": row["prompt_preview"],
                    "response_preview": row["response_preview"],
                    "raw_usage": json.loads(row["raw_usage_json"]),
                    "token_usage": token_usage,
                    "cost_usage": cost_usage,
                    "generation_mode": row["generation_mode"] or invocation_payload.get("generation_mode"),
                    "usage_unavailable_reason": row["usage_unavailable_reason"] or invocation_payload.get("usage_unavailable_reason"),
                    "error": row["error"],
                    "total_tokens": total_tokens,
                    "total_cost": total_cost,
                    "cost_status": "unavailable" if cost_unavailable else "available" if cost_usage else "none",
                }
            )
        mission_total_tokens = int(_metric_total(mission_tokens, preferred_keys=("total_tokens",)))
        mission_total_cost = _metric_total(mission_costs, preferred_keys=("usd", "total_cost"))
        active_total_tokens = int(_metric_total(active_tokens, preferred_keys=("total_tokens",)))
        active_total_cost = _metric_total(active_costs, preferred_keys=("usd", "total_cost"))
        for bucket in (*by_provider.values(), *by_lane.values()):
            bucket["cost_status"] = _cost_status(
                total_tokens=float(bucket["total_tokens"]),
                total_cost=float(bucket["total_cost"]),
                invocation_count=int(bucket["invocation_count"]),
                cost_unavailable_invocation_count=int(bucket["cost_unavailable_invocation_count"]),
            )
            bucket["cost_unavailable"] = bucket["cost_unavailable_invocation_count"] > 0
        return {
            "mission": {
                "token_usage": mission_tokens,
                "cost_usage": mission_costs,
                "total_tokens": mission_total_tokens,
                "total_cost": mission_total_cost,
                "invocation_count": len(rows),
                "cost_unavailable_invocation_count": mission_cost_unavailable_invocation_count,
                "cost_unavailable": mission_cost_unavailable_invocation_count > 0,
                "cost_status": _cost_status(
                    total_tokens=float(mission_total_tokens),
                    total_cost=mission_total_cost,
                    invocation_count=len(rows),
                    cost_unavailable_invocation_count=mission_cost_unavailable_invocation_count,
                ),
            },
            "active_task": {
                "task_id": active_task_id,
                "token_usage": active_tokens,
                "cost_usage": active_costs,
                "total_tokens": active_total_tokens,
                "total_cost": active_total_cost,
                "invocation_count": active_invocation_count,
                "cost_unavailable_invocation_count": active_cost_unavailable_invocation_count,
                "cost_unavailable": active_cost_unavailable_invocation_count > 0,
                "cost_status": _cost_status(
                    total_tokens=float(active_total_tokens),
                    total_cost=active_total_cost,
                    invocation_count=active_invocation_count,
                    cost_unavailable_invocation_count=active_cost_unavailable_invocation_count,
                ),
            },
            "by_provider": by_provider,
            "by_lane": by_lane,
            "invocations": invocations,
        }

    def _usage_totals(
        self,
        mission_id: str,
        active_task_id: str | None = None,
        rows: list[sqlite3.Row] | None = None,
    ) -> dict[str, Any]:
        rows = rows if rows is not None else self.fetch_model_invocations(mission_id)
        mission_tokens: dict[str, int] = {}
        mission_costs: dict[str, float] = {}
        active_tokens: dict[str, int] = {}
        active_costs: dict[str, float] = {}
        for row in rows:
            token_usage = json.loads(row["token_usage_json"]) if row["token_usage_json"] else None
            cost_usage = json.loads(row["cost_usage_json"]) if row["cost_usage_json"] else None
            for key, value in (token_usage or {}).items():
                mission_tokens[key] = mission_tokens.get(key, 0) + int(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_tokens[key] = active_tokens.get(key, 0) + int(value)
            for key, value in (cost_usage or {}).items():
                mission_costs[key] = mission_costs.get(key, 0.0) + float(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_costs[key] = active_costs.get(key, 0.0) + float(value)
        return {
            "mission": {
                "token_usage": mission_tokens,
                "cost_usage": mission_costs,
                "total_tokens": int(_metric_total(mission_tokens, preferred_keys=("total_tokens",))),
                "total_cost": _metric_total(mission_costs, preferred_keys=("usd", "total_cost")),
            },
            "active_task": {
                "token_usage": active_tokens,
                "cost_usage": active_costs,
                "total_tokens": int(_metric_total(active_tokens, preferred_keys=("total_tokens",))),
                "total_cost": _metric_total(active_costs, preferred_keys=("usd", "total_cost")),
            },
        }

    def _bidding_state_for_view(
        self,
        summary: dict[str, Any],
        runtime: sqlite3.Row | None,
        usage_summary: dict[str, Any],
        bids: list[dict[str, Any]],
        active_task_id: str | None,
    ) -> dict[str, Any]:
        state = dict(summary.get("bidding_state", {}))
        if runtime and runtime["bidding_state_json"]:
            state.update(json.loads(runtime["bidding_state_json"]))
        active_bids = [bid for bid in bids if bid.get("task_id") == active_task_id]
        state["total_provider_invocations"] = sum(
            1 for invocation in usage_summary.get("invocations", []) if invocation.get("generation_mode") == "provider_model"
        )
        state["active_provider_bids"] = sum(1 for bid in active_bids if bid.get("generation_mode") == "provider_model")
        state["active_fallback_bids"] = sum(1 for bid in active_bids if bid.get("generation_mode") == "deterministic_fallback")
        state["degraded"] = bool(state.get("degraded") or state.get("generation_mode") == "deterministic_fallback")
        return state

    def _provider_market_summary(self, bids: list[dict[str, Any]], active_task_id: str | None, winner_bid_id: str | None, standby_bid_id: str | None) -> dict[str, Any]:
        active_bids = [bid for bid in bids if bid.get("task_id") == active_task_id]
        providers: dict[str, list[dict[str, Any]]] = {}
        families: dict[str, list[dict[str, Any]]] = {}
        for bid in active_bids:
            providers.setdefault(bid.get("provider") or "system", []).append(bid)
            families.setdefault(bid.get("strategy_family") or "unclassified", []).append(bid)
        return {
            "active_task_id": active_task_id,
            "winner_bid_id": winner_bid_id,
            "standby_bid_id": standby_bid_id,
            "providers": providers,
            "families": families,
        }

    def refresh_mission_view(self, mission_id: str) -> dict[str, Any]:
        mission = self.fetch_mission(mission_id)
        runtime = self.fetch_runtime(mission_id)
        if mission is None:
            raise ValueError(f"Mission {mission_id} not found.")
        summary = json.loads(mission["summary_json"])
        control = self.fetch_control_state(mission_id)
        status = mission["status"]
        outcome = mission["outcome"]
        control_run_state = control["run_state"] if control else RunState.IDLE.value
        control_reason = control["reason"] if control and "reason" in control.keys() else None
        invocation_rows = self.fetch_model_invocations(mission_id)
        has_inflight_model_work = any(
            row["status"] == "started" and not row["completed_at"]
            for row in invocation_rows
        )
        stale_session_finalize = (
            control_run_state == RunState.FINALIZED.value
            and control_reason in {"session_ended", "session_terminated"}
            and outcome in {None, MissionOutcome.FAILED_SAFE_STOP.value}
            and runtime is not None
            and runtime["active_phase"] != ActivePhase.FINALIZE.value
            and has_inflight_model_work
        )
        if stale_session_finalize:
            control_run_state = RunState.RUNNING.value
            status = runtime["active_phase"] or mission["status"]
            outcome = None
        elif control_run_state == RunState.FINALIZED.value:
            status = RunState.FINALIZED.value
            outcome = outcome or summary.get("outcome")
            if outcome is None and control_reason in {"session_ended", "session_terminated"}:
                outcome = MissionOutcome.FAILED_SAFE_STOP.value
        validation = self.fetch_latest_validation(mission_id)
        validation_report = json.loads(validation["payload_json"]) if validation else None
        failure = self.fetch_latest_failure(mission_id)
        failure_context = json.loads(failure["payload_json"]) if failure else None
        latest_state_checkpoint = self.fetch_latest_mission_state_checkpoint(mission_id)
        checkpoint = self.fetch_latest_checkpoint(mission_id)
        events = self._events_for_view(mission_id)
        recent_trace = self._trace_for_view(mission_id)
        tasks = [json.loads(row["payload_json"]) for row in self.fetch_ordered("tasks", "updated_at ASC", mission_id)]
        bids = [json.loads(row["payload_json"]) for row in self.fetch_ordered("bids", "updated_at ASC", mission_id)]
        active_task = next((task for task in tasks if task["task_id"] == (runtime["active_task_id"] if runtime else None)), None)
        usage_summary = self._usage_summary(
            mission_id,
            runtime["active_task_id"] if runtime else None,
            rows=invocation_rows,
        )
        bidding_state = self._bidding_state_for_view(
            summary,
            runtime,
            usage_summary,
            bids,
            runtime["active_task_id"] if runtime else None,
        )
        accepted_checkpoints = self._checkpoints_for_view(mission_id)
        mission_state_checkpoints = self._mission_state_checkpoints_for_view(mission_id)
        repo_state_checkpoints = self._repo_state_checkpoints_for_view(mission_id)
        governed_bid_envelopes = self._governed_bid_envelopes_for_view(mission_id)
        recent_civic_actions = self._governed_action_records_for_view(mission_id)
        mission_output = self._mission_output(mission, runtime, accepted_checkpoints)
        failure_count = len(self.fetch_all("failure_contexts", mission_id))
        runtime_seconds = self._runtime_seconds(mission, summary, control, runtime)
        history_metrics = self._history_metrics(
            runtime,
            validation_report,
            failure_count,
            accepted_checkpoints,
            mission_state_checkpoints,
            repo_state_checkpoints,
            mission_output,
        )
        civic_activity = self._civic_activity(summary, runtime, failure_context, recent_civic_actions)
        provider_market_summary = self._provider_market_summary(
            bids,
            runtime["active_task_id"] if runtime else None,
            runtime["winner_bid_id"] if runtime else None,
            runtime["standby_bid_id"] if runtime else None,
        )
        winner_bid = next((bid for bid in bids if bid.get("bid_id") == (runtime["winner_bid_id"] if runtime else None)), None)
        outcome_summary = self._outcome_summary(
            mission=mission,
            runtime=runtime,
            outcome=outcome,
            run_state=control_run_state,
            winner_bid=winner_bid,
            validation_report=validation_report,
            failure_context=failure_context,
            mission_output=mission_output,
            history_metrics=history_metrics,
        )
        mission_meta = self._mission_meta(
            mission=mission,
            runtime=runtime,
            control=control,
            run_state=control_run_state,
            active_task=active_task,
            usage_summary=usage_summary,
            runtime_seconds=runtime_seconds,
            history_metrics=history_metrics,
            civic_activity=civic_activity,
            available_skills=json.loads(runtime["available_skills_json"]) if runtime and runtime["available_skills_json"] else [],
        )
        mission_meta["status"] = status
        mission_meta["outcome"] = outcome
        mission_meta["head_commit"] = json.loads(checkpoint["payload_json"])["commit_sha"] if checkpoint else summary.get("head_commit")
        payload = {
            "mission_id": mission["id"],
            "repo_path": mission["repo_path"],
            "objective": mission["objective"],
            "created_at": mission["created_at"],
            "updated_at": max(
                (
                    timestamp
                    for timestamp in (
                        mission["updated_at"],
                        runtime["updated_at"] if runtime else None,
                        control["updated_at"] if control else None,
                    )
                    if timestamp
                ),
                default=mission["updated_at"],
            ),
            "runtime_seconds": runtime_seconds,
            "status": status,
            "outcome": outcome,
            "run_state": control_run_state,
            "active_phase": runtime["active_phase"] if runtime else ActivePhase.IDLE.value,
            "active_task_id": runtime["active_task_id"] if runtime else None,
            "active_bid_round": runtime["active_bid_round"] if runtime else 0,
            "simulation_round": runtime["simulation_round"] if runtime else 0,
            "recovery_round": runtime["recovery_round"] if runtime else 0,
            "branch_name": mission["branch_name"],
            "head_commit": json.loads(checkpoint["payload_json"])["commit_sha"] if checkpoint else summary.get("head_commit"),
            "latest_event_id": events[-1]["id"] if events else 0,
            "latest_diff_summary": runtime["latest_diff_summary"] if runtime else "",
            "winner_bid_id": runtime["winner_bid_id"] if runtime else None,
            "standby_bid_id": runtime["standby_bid_id"] if runtime else None,
            "decision_history": summary.get("decision_history", []),
            "failed_attempt_history": summary.get("failed_attempt_history", []),
            "tasks": tasks,
            "active_task": active_task,
            "bids": bids,
            "events": events,
            "validation_report": validation_report,
            "failure_context": failure_context,
            "simulation_summary": json.loads(runtime["simulation_summary_json"]) if runtime and runtime["simulation_summary_json"] else None,
            "guardrail_state": {"policy_state": runtime["policy_state"] if runtime else PolicyState.CLEAR.value, "current_risk_score": runtime["current_risk_score"] if runtime else 0.0},
            "recovery_state": {"recovery_round": runtime["recovery_round"] if runtime else 0, "last_failure_task_id": runtime["latest_failure_task_id"] if runtime else None},
            "stop_state": {"stop_reason": runtime["stop_reason"] if runtime else None},
            "bidding_state": bidding_state,
            "civic_audit_summary": summary.get("audit_summary", {}),
            "civic_connection": json.loads(runtime["civic_connection_json"]) if runtime and runtime["civic_connection_json"] else {},
            "civic_capabilities": json.loads(runtime["civic_capabilities_json"]) if runtime and runtime["civic_capabilities_json"] else [],
            "available_skills": json.loads(runtime["available_skills_json"]) if runtime and runtime["available_skills_json"] else [],
            "skill_health": json.loads(runtime["skill_health_json"]) if runtime and runtime["skill_health_json"] else {},
            "skill_outputs": json.loads(runtime["skill_outputs_json"]) if runtime and runtime["skill_outputs_json"] else {},
            "mission_meta": mission_meta,
            "history_metrics": history_metrics,
            "repo_insights": self._repo_insights(mission, summary, latest_state_checkpoint, validation_report),
            "outcome_summary": outcome_summary,
            "civic_activity": civic_activity,
            "activity_summary": self._activity_summary(events, recent_trace),
            "provider_market_summary": provider_market_summary,
            "usage_summary": usage_summary,
            "worktree_state": json.loads(runtime["worktree_state_json"]) if runtime and runtime["worktree_state_json"] else {},
            "accepted_checkpoints": accepted_checkpoints,
            "mission_state_checkpoints": mission_state_checkpoints,
            "repo_state_checkpoints": repo_state_checkpoints,
            "governed_bid_envelopes": governed_bid_envelopes,
            "recent_civic_actions": recent_civic_actions,
            "mission_output": mission_output,
            "execution_steps": self._execution_steps_for_view(mission_id),
            "recent_trace": recent_trace,
        }
        return payload

    def get_mission_view(self, mission_id: str) -> dict[str, Any]:
        return self.refresh_mission_view(mission_id)

    def rebuild_state(self, mission_id: str) -> ArbiterState:
        checkpoint_row = self.fetch_latest_mission_state_checkpoint(mission_id)
        if checkpoint_row is not None:
            checkpoint_payload = json.loads(checkpoint_row["payload_json"])
            state = ArbiterState.model_validate(checkpoint_payload.get("state", {}))
            control = self.fetch_control_state(mission_id)
            state.control = MissionControlState(
                run_state=RunState(control["run_state"]) if control else RunState(checkpoint_payload.get("run_state", state.control.run_state.value)),
                requested_action=control["requested_action"] if control else state.control.requested_action,
                reason=control["reason"] if control else state.control.reason,
            )
            if state.current_bid is None and state.winner_bid_id:
                state.current_bid = next((bid for bid in state.active_bids if bid.bid_id == state.winner_bid_id), None)
            if state.standby_bid is None and state.standby_bid_id:
                state.standby_bid = next((bid for bid in state.active_bids if bid.bid_id == state.standby_bid_id), None)
            return state
        mission = self.fetch_mission(mission_id)
        runtime = self.fetch_runtime(mission_id)
        if mission is None or runtime is None:
            raise ValueError(f"Mission {mission_id} not found.")
        control = self.fetch_control_state(mission_id)
        validation = self.fetch_latest_validation(mission_id)
        failure = self.fetch_latest_failure(mission_id)
        checkpoint = self.fetch_latest_checkpoint(mission_id)
        state = ArbiterState.model_validate(
            {
                "mission": json.loads(mission["spec_json"]),
                "summary": MissionSummary.model_validate(json.loads(mission["summary_json"])).model_dump(mode="json"),
                "control": MissionControlState(run_state=RunState(control["run_state"]) if control else RunState.IDLE, requested_action=control["requested_action"] if control else None, reason=control["reason"] if control else None).model_dump(mode="json"),
                "active_phase": runtime["active_phase"],
                "active_task_id": runtime["active_task_id"],
                "active_bid_round": runtime["active_bid_round"],
                "recovery_round": runtime["recovery_round"],
                "winner_bid_id": runtime["winner_bid_id"],
                "standby_bid_id": runtime["standby_bid_id"],
                "latest_diff_summary": runtime["latest_diff_summary"],
                "tasks": [json.loads(row["payload_json"]) for row in self.fetch_ordered("tasks", "updated_at ASC", mission_id)],
                "active_bids": [json.loads(row["payload_json"]) for row in self.fetch_ordered("bids", "updated_at DESC", mission_id)],
                "validation_report": json.loads(validation["payload_json"]) if validation else None,
                "failure_context": json.loads(failure["payload_json"]) if failure else None,
                "simulation_summary": json.loads(runtime["simulation_summary_json"]) if runtime["simulation_summary_json"] else None,
                "worktree_state": json.loads(runtime["worktree_state_json"]) if runtime["worktree_state_json"] else {},
                "bidding_state": json.loads(runtime["bidding_state_json"]) if runtime["bidding_state_json"] else json.loads(mission["summary_json"]).get("bidding_state", {}),
            }
        )
        if checkpoint:
            state.accepted_checkpoint = AcceptedCheckpoint.model_validate_json(checkpoint["payload_json"])
        if state.current_bid is None and state.winner_bid_id:
            state.current_bid = next((bid for bid in state.active_bids if bid.bid_id == state.winner_bid_id), None)
        if state.standby_bid is None and state.standby_bid_id:
            state.standby_bid = next((bid for bid in state.active_bids if bid.bid_id == state.standby_bid_id), None)
        return state
