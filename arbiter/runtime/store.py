from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class MissionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS mission (
                mission_id TEXT PRIMARY KEY,
                status TEXT,
                repo_path TEXT,
                branch_name TEXT,
                outcome TEXT,
                spec_json TEXT,
                summary_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                payload_json TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS bids (
                bid_id TEXT PRIMARY KEY,
                task_id TEXT,
                selected INTEGER DEFAULT 0,
                standby INTEGER DEFAULT 0,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS execution_steps (
                step_id TEXT PRIMARY KEY,
                task_id TEXT,
                bid_id TEXT,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS validation_reports (
                task_id TEXT PRIMARY KEY,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS failure_contexts (
                task_id TEXT PRIMARY KEY,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS mission_state_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT,
                state_json TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS repo_state_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                accepted INTEGER DEFAULT 0,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS replay_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lane TEXT,
                replay_key TEXT,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS mission_control (
                mission_id TEXT PRIMARY KEY,
                run_state TEXT,
                requested_action TEXT,
                reason TEXT,
                updated_at TEXT
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert_mission(self, mission_id: str, status: str, repo_path: str, branch_name: str | None, outcome: str | None, spec: BaseModel, summary: BaseModel) -> None:
        self.connection.execute(
            """
            INSERT INTO mission (mission_id, status, repo_path, branch_name, outcome, spec_json, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                status=excluded.status,
                repo_path=excluded.repo_path,
                branch_name=excluded.branch_name,
                outcome=excluded.outcome,
                spec_json=excluded.spec_json,
                summary_json=excluded.summary_json
            """,
            (
                mission_id,
                status,
                repo_path,
                branch_name,
                outcome,
                spec.model_dump_json(),
                summary.model_dump_json(),
            ),
        )
        self.connection.commit()

    def save_record(self, table: str, key_field: str, key_value: str, payload: BaseModel, **extra: Any) -> None:
        payload_json = payload.model_dump_json()
        columns = [key_field, "payload_json", *extra.keys()]
        values = [key_value, payload_json, *extra.values()]
        placeholders = ", ".join(["?"] * len(columns))
        updates = ", ".join(
            [f"{col}=excluded.{col}" for col in columns if col != key_field]
        )
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key_field}) DO UPDATE SET {updates}",
            values,
        )
        self.connection.commit()

    def append_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> None:
        self.connection.execute(
            "INSERT INTO events (event_type, payload_json, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(payload, default=str), created_at),
        )
        self.connection.commit()

    def add_checkpoint(self, label: str, state: BaseModel, created_at: str) -> None:
        self.connection.execute(
            "INSERT INTO mission_state_checkpoints (label, state_json, created_at) VALUES (?, ?, ?)",
            (label, state.model_dump_json(), created_at),
        )
        self.connection.commit()

    def add_repo_checkpoint(self, checkpoint_id: str, accepted: bool, payload: BaseModel) -> None:
        self.connection.execute(
            """
            INSERT INTO repo_state_checkpoints (checkpoint_id, accepted, payload_json)
            VALUES (?, ?, ?)
            ON CONFLICT(checkpoint_id) DO UPDATE SET
                accepted=excluded.accepted,
                payload_json=excluded.payload_json
            """,
            (checkpoint_id, int(accepted), payload.model_dump_json()),
        )
        self.connection.commit()

    def add_replay_record(self, lane: str, replay_key: str, payload: BaseModel) -> None:
        self.connection.execute(
            "INSERT INTO replay_records (lane, replay_key, payload_json) VALUES (?, ?, ?)",
            (lane, replay_key, payload.model_dump_json()),
        )
        self.connection.commit()

    def fetch_mission(self) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM mission LIMIT 1").fetchone()

    def fetch_all(self, table: str) -> list[sqlite3.Row]:
        return self.connection.execute(f"SELECT * FROM {table}").fetchall()

    def fetch_ordered(self, table: str, order_by: str) -> list[sqlite3.Row]:
        return self.connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()

    def fetch_latest_checkpoint(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM mission_state_checkpoints ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def upsert_control_state(
        self,
        mission_id: str,
        run_state: str,
        requested_action: str | None,
        reason: str | None,
        updated_at: str,
    ) -> None:
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

    def fetch_control_state(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM mission_control WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()

    def fetch_events_after(self, last_id: int = 0) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id ASC",
            (last_id,),
        ).fetchall()
