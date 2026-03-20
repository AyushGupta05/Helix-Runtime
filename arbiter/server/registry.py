from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from arbiter.server.schemas import MissionHistoryEntry


def control_root() -> Path:
    configured = os.getenv("ARBITER_CONTROL_ROOT")
    return Path(configured) if configured else Path.home() / ".arbiter-control"


class MissionRegistry:
    def __init__(self) -> None:
        root = control_root()
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "registry.db"
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                repo_path TEXT NOT NULL,
                objective TEXT NOT NULL,
                root_dir TEXT NOT NULL,
                status TEXT NOT NULL,
                run_state TEXT NOT NULL,
                outcome TEXT,
                branch_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert(
        self,
        mission_id: str,
        repo_path: str,
        objective: str,
        root_dir: str,
        status: str,
        run_state: str,
        created_at: str,
        updated_at: str,
        outcome: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO missions (mission_id, repo_path, objective, root_dir, status, run_state, outcome, branch_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                repo_path=excluded.repo_path,
                objective=excluded.objective,
                root_dir=excluded.root_dir,
                status=excluded.status,
                run_state=excluded.run_state,
                outcome=excluded.outcome,
                branch_name=excluded.branch_name,
                updated_at=excluded.updated_at
            """,
            (mission_id, repo_path, objective, root_dir, status, run_state, outcome, branch_name, created_at, updated_at),
        )
        self.connection.commit()

    def get(self, mission_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()

    def list(self) -> list[MissionHistoryEntry]:
        rows = self.connection.execute("SELECT * FROM missions ORDER BY updated_at DESC").fetchall()
        return [MissionHistoryEntry.model_validate(dict(row)) for row in rows]

