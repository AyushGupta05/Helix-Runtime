from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ActivePhase,
    ArbiterState,
    MissionControlState,
    MissionSummary,
    PolicyState,
    ReplayRecord,
    RunState,
    SimulationSummary,
    utc_now,
)


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


class MissionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self._ensure_column("mission_runtime", "worktree_state_json", "TEXT")

    def _init_schema(self) -> None:
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
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert_mission(self, mission_id: str, status: str, repo_path: str, objective: str, branch_name: str | None, outcome: str | None, spec: BaseModel, summary: BaseModel, created_at: str | None = None) -> None:
        now = utc_now().isoformat()
        self.connection.execute(
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
        self.connection.commit()

    def upsert_runtime(self, mission_id: str, *, active_phase: str, active_task_id: str | None, active_bid_round: int, simulation_round: int, recovery_round: int, winner_bid_id: str | None, standby_bid_id: str | None, latest_diff_summary: str, stop_reason: str | None, policy_state: str, current_risk_score: float, simulation_summary: SimulationSummary | None, worktree_state: dict[str, Any] | None, latest_validation_task_id: str | None, latest_failure_task_id: str | None, accepted_checkpoint_id: str | None) -> None:
        self.connection.execute(
            """
            INSERT INTO mission_runtime (
                mission_id, active_phase, active_task_id, active_bid_round, simulation_round, recovery_round,
                winner_bid_id, standby_bid_id, latest_diff_summary, stop_reason, policy_state, current_risk_score,
                simulation_summary_json, worktree_state_json, latest_validation_task_id, latest_failure_task_id, accepted_checkpoint_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                latest_validation_task_id,
                latest_failure_task_id,
                accepted_checkpoint_id,
                utc_now().isoformat(),
            ),
        )
        self.connection.commit()

    def upsert_control_state(self, mission_id: str, run_state: str, requested_action: str | None, reason: str | None, updated_at: str) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def save_task(self, mission_id: str, task: BaseModel, *, task_id: str, title: str, task_type: str, status: str, required: bool, dependencies: list[str]) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def save_bid(self, mission_id: str, bid: BaseModel, *, bid_id: str, task_id: str, role: str, strategy_family: str, score: float | None, risk: float, cost: float, confidence: float, is_winner: bool, is_standby: bool, status: str, round_index: int) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def save_execution_step(self, mission_id: str, step: BaseModel, *, step_id: str, task_id: str, action: str, result: str, timestamp: str) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def save_validation_report(self, mission_id: str, report: BaseModel, *, record_id: str, task_id: str, passed: bool, details: list[str], timestamp: str) -> None:
        self.connection.execute(
            "INSERT INTO validation_reports (id, mission_id, task_id, passed, details, timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, mission_id, task_id, int(passed), _dump(details), timestamp, report.model_dump_json()),
        )
        self.connection.commit()

    def save_failure_context(self, mission_id: str, failure: BaseModel, *, record_id: str, task_id: str, failure_type: str, details: str, diff_summary: str, strategy_family: str | None, timestamp: str) -> None:
        self.connection.execute(
            "INSERT INTO failure_contexts (id, mission_id, task_id, failure_type, details, diff_summary, strategy_family, timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, mission_id, task_id, failure_type, details, diff_summary, strategy_family, timestamp, failure.model_dump_json()),
        )
        self.connection.commit()

    def save_accepted_checkpoint(self, mission_id: str, checkpoint: AcceptedCheckpoint) -> None:
        self.connection.execute(
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
        self.connection.commit()

    def save_model_invocation(self, mission_id: str, invocation: BaseModel, *, invocation_id: str, task_id: str | None, bid_id: str | None, provider: str, lane: str, model_id: str | None, invocation_kind: str, status: str, started_at: str | None, completed_at: str | None, prompt_preview: str | None, response_preview: str | None, raw_usage: dict[str, Any], token_usage: dict[str, int], cost_usage: dict[str, float], error: str | None) -> None:
        self.connection.execute(
            """
            INSERT INTO model_invocations (
                id, mission_id, task_id, bid_id, provider, lane, model_id, invocation_kind, status,
                started_at, completed_at, prompt_preview, response_preview, raw_usage_json, token_usage_json,
                cost_usage_json, error, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _dump(raw_usage),
                _dump(token_usage),
                _dump(cost_usage),
                error,
                invocation.model_dump_json(),
            ),
        )
        self.connection.commit()

    def save_trace_entry(self, mission_id: str, trace: BaseModel, *, task_id: str | None, bid_id: str | None, trace_type: str, title: str, message: str, status: str, provider: str | None, lane: str | None) -> int:
        cursor = self.connection.execute(
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
        self.connection.commit()
        return int(cursor.lastrowid)

    def add_replay_record(self, mission_id: str | None, lane: str, replay_key: str, payload: ReplayRecord) -> None:
        self.connection.execute(
            "INSERT INTO replay_records (mission_id, lane, replay_key, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (mission_id, lane, replay_key, payload.model_dump_json(), payload.created_at.isoformat()),
        )
        self.connection.commit()

    def append_event(self, mission_id: str, event_type: str, payload: dict[str, Any], created_at: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO events (mission_id, event_type, payload_json, created_at, jsonl_written) VALUES (?, ?, ?, ?, 0)",
            (mission_id, event_type, _dump(payload), created_at),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def mark_event_jsonl_written(self, event_id: int) -> None:
        self.connection.execute("UPDATE events SET jsonl_written = 1 WHERE id = ?", (event_id,))
        self.connection.commit()

    def fetch_events_needing_jsonl(self, mission_id: str) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM events WHERE mission_id = ? AND jsonl_written = 0 ORDER BY id ASC", (mission_id,)).fetchall()

    def fetch_mission(self, mission_id: str | None = None) -> sqlite3.Row | None:
        if mission_id is None:
            return self.connection.execute("SELECT * FROM mission LIMIT 1").fetchone()
        return self.connection.execute("SELECT * FROM mission WHERE id = ?", (mission_id,)).fetchone()

    def fetch_runtime(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM mission_runtime WHERE mission_id = ?", (mission_id,)).fetchone()

    def fetch_control_state(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM mission_control WHERE mission_id = ?", (mission_id,)).fetchone()

    def fetch_events_after(self, mission_id: str, last_id: int = 0) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM events WHERE mission_id = ? AND id > ? ORDER BY id ASC", (mission_id, last_id)).fetchall()

    def fetch_all(self, table: str, mission_id: str | None = None) -> list[sqlite3.Row]:
        if mission_id is None:
            return self.connection.execute(f"SELECT * FROM {table}").fetchall()
        key = "id" if table == "mission" else "mission_id"
        return self.connection.execute(f"SELECT * FROM {table} WHERE {key} = ?", (mission_id,)).fetchall()

    def fetch_ordered(self, table: str, order_by: str, mission_id: str | None = None) -> list[sqlite3.Row]:
        if mission_id is None:
            return self.connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
        key = "id" if table == "mission" else "mission_id"
        return self.connection.execute(f"SELECT * FROM {table} WHERE {key} = ? ORDER BY {order_by}", (mission_id,)).fetchall()

    def fetch_latest_validation(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM validation_reports WHERE mission_id = ? ORDER BY timestamp DESC LIMIT 1", (mission_id,)).fetchone()

    def fetch_latest_failure(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM failure_contexts WHERE mission_id = ? ORDER BY timestamp DESC LIMIT 1", (mission_id,)).fetchone()

    def fetch_latest_checkpoint(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM accepted_checkpoints WHERE mission_id = ? ORDER BY created_at DESC LIMIT 1", (mission_id,)).fetchone()

    def fetch_model_invocations(self, mission_id: str, task_id: str | None = None) -> list[sqlite3.Row]:
        if task_id is None:
            return self.connection.execute("SELECT * FROM model_invocations WHERE mission_id = ? ORDER BY started_at ASC, id ASC", (mission_id,)).fetchall()
        return self.connection.execute("SELECT * FROM model_invocations WHERE mission_id = ? AND task_id = ? ORDER BY started_at ASC, id ASC", (mission_id, task_id)).fetchall()

    def fetch_trace_entries(self, mission_id: str, limit: int = 200, after_id: int = 0) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            "SELECT * FROM trace_entries WHERE mission_id = ? AND id > ? ORDER BY id DESC LIMIT ?",
            (mission_id, after_id, limit),
        ).fetchall()
        rows = list(rows)
        rows.reverse()
        return rows

    def _events_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM events WHERE mission_id = ? ORDER BY id DESC LIMIT 200", (mission_id,)).fetchall()
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

    def _checkpoints_for_view(self, mission_id: str) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.fetch_ordered("accepted_checkpoints", "created_at ASC", mission_id)]

    def _usage_summary(self, mission_id: str, active_task_id: str | None) -> dict[str, Any]:
        rows = self.fetch_model_invocations(mission_id)
        mission_tokens: dict[str, int] = {}
        mission_costs: dict[str, float] = {}
        active_tokens: dict[str, int] = {}
        active_costs: dict[str, float] = {}
        by_provider: dict[str, dict[str, Any]] = {}
        by_lane: dict[str, dict[str, Any]] = {}
        invocations: list[dict[str, Any]] = []
        for row in rows:
            token_usage = json.loads(row["token_usage_json"])
            cost_usage = json.loads(row["cost_usage_json"])
            for key, value in token_usage.items():
                mission_tokens[key] = mission_tokens.get(key, 0) + int(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_tokens[key] = active_tokens.get(key, 0) + int(value)
            for key, value in cost_usage.items():
                mission_costs[key] = mission_costs.get(key, 0.0) + float(value)
                if row["task_id"] and row["task_id"] == active_task_id:
                    active_costs[key] = active_costs.get(key, 0.0) + float(value)
            provider_bucket = by_provider.setdefault(row["provider"], {"provider": row["provider"], "token_usage": {}, "cost_usage": {}, "total_tokens": 0, "total_cost": 0.0, "invocation_count": 0})
            lane_bucket = by_lane.setdefault(row["lane"], {"lane": row["lane"], "provider": row["provider"], "token_usage": {}, "cost_usage": {}, "total_tokens": 0, "total_cost": 0.0, "invocation_count": 0})
            total_tokens = sum(int(value) for value in token_usage.values())
            total_cost = sum(float(value) for value in cost_usage.values())
            provider_bucket["total_tokens"] += total_tokens
            provider_bucket["total_cost"] += total_cost
            provider_bucket["invocation_count"] += 1
            lane_bucket["total_tokens"] += total_tokens
            lane_bucket["total_cost"] += total_cost
            lane_bucket["invocation_count"] += 1
            for bucket in (provider_bucket, lane_bucket):
                for key, value in token_usage.items():
                    bucket["token_usage"][key] = bucket["token_usage"].get(key, 0) + int(value)
                for key, value in cost_usage.items():
                    bucket["cost_usage"][key] = bucket["cost_usage"].get(key, 0.0) + float(value)
            invocations.append(
                {
                    "invocation_id": row["id"],
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
                    "error": row["error"],
                    "total_tokens": total_tokens,
                    "total_cost": total_cost,
                }
            )
        return {
            "mission": {
                "token_usage": mission_tokens,
                "cost_usage": mission_costs,
                "total_tokens": sum(mission_tokens.values()),
                "total_cost": sum(mission_costs.values()),
            },
            "active_task": {
                "task_id": active_task_id,
                "token_usage": active_tokens,
                "cost_usage": active_costs,
                "total_tokens": sum(active_tokens.values()),
                "total_cost": sum(active_costs.values()),
            },
            "by_provider": by_provider,
            "by_lane": by_lane,
            "invocations": invocations,
        }

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
        validation = self.fetch_latest_validation(mission_id)
        failure = self.fetch_latest_failure(mission_id)
        checkpoint = self.fetch_latest_checkpoint(mission_id)
        events = self._events_for_view(mission_id)
        tasks = [json.loads(row["payload_json"]) for row in self.fetch_ordered("tasks", "updated_at ASC", mission_id)]
        bids = [json.loads(row["payload_json"]) for row in self.fetch_ordered("bids", "updated_at ASC", mission_id)]
        active_task = next((task for task in tasks if task["task_id"] == (runtime["active_task_id"] if runtime else None)), None)
        usage_summary = self._usage_summary(mission_id, runtime["active_task_id"] if runtime else None)
        payload = {
            "mission_id": mission["id"],
            "repo_path": mission["repo_path"],
            "objective": mission["objective"],
            "status": mission["status"],
            "outcome": mission["outcome"],
            "run_state": control["run_state"] if control else RunState.IDLE.value,
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
            "validation_report": json.loads(validation["payload_json"]) if validation else None,
            "failure_context": json.loads(failure["payload_json"]) if failure else None,
            "simulation_summary": json.loads(runtime["simulation_summary_json"]) if runtime and runtime["simulation_summary_json"] else None,
            "guardrail_state": {"policy_state": runtime["policy_state"] if runtime else PolicyState.CLEAR.value, "current_risk_score": runtime["current_risk_score"] if runtime else 0.0},
            "recovery_state": {"recovery_round": runtime["recovery_round"] if runtime else 0, "last_failure_task_id": runtime["latest_failure_task_id"] if runtime else None},
            "stop_state": {"stop_reason": runtime["stop_reason"] if runtime else None},
            "civic_audit_summary": summary.get("audit_summary", {}),
            "provider_market_summary": self._provider_market_summary(bids, runtime["active_task_id"] if runtime else None, runtime["winner_bid_id"] if runtime else None, runtime["standby_bid_id"] if runtime else None),
            "usage_summary": usage_summary,
            "worktree_state": json.loads(runtime["worktree_state_json"]) if runtime and runtime["worktree_state_json"] else {},
            "accepted_checkpoints": self._checkpoints_for_view(mission_id),
            "execution_steps": self._execution_steps_for_view(mission_id),
            "recent_trace": self._trace_for_view(mission_id),
        }
        self.connection.execute(
            "INSERT INTO mission_view_cache (mission_id, payload_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(mission_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
            (mission_id, _dump(payload), utc_now().isoformat()),
        )
        self.connection.commit()
        return payload

    def get_mission_view(self, mission_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM mission_view_cache WHERE mission_id = ?", (mission_id,)).fetchone()
        if row:
            cache_updated_at = row["updated_at"]
            source_updated_at = [
                mission_row["updated_at"]
                for mission_row in (
                    self.fetch_mission(mission_id),
                    self.fetch_runtime(mission_id),
                    self.fetch_control_state(mission_id),
                )
                if mission_row and mission_row["updated_at"]
            ]
            if not source_updated_at or max(source_updated_at) <= cache_updated_at:
                return json.loads(row["payload_json"])
        return self.refresh_mission_view(mission_id)

    def rebuild_state(self, mission_id: str) -> ArbiterState:
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
            }
        )
        if checkpoint:
            state.accepted_checkpoint = AcceptedCheckpoint.model_validate_json(checkpoint["payload_json"])
        return state
