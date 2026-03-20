from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from arbiter.agents.backend import EditProposal, FileUpdate, ScriptedStrategyBackend
from arbiter.mission.runner import mission_status, start_mission
from arbiter.repo.worktree import WorktreeSetupError


def test_python_bugfix_mission_recovers_via_standby(python_bug_repo: Path) -> None:
    backend = ScriptedStrategyBackend(
        [
            EditProposal(
                summary="Apply an incorrect patch first to force recovery.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a - b + 1\n")],
            ),
            EditProposal(
                summary="Apply the correct bugfix.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
            ),
            EditProposal(
                summary="Expand regression coverage.",
                files=[
                    FileUpdate(
                        path="tests/test_calc.py",
                        content="from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
                    )
                ],
            ),
        ]
    )
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=backend,
    )
    assert state.outcome is not None
    assert state.outcome.value == "success"
    assert state.summary.branch_name

    mission_root = python_bug_repo / ".arbiter" / state.mission.mission_id
    assert (mission_root / "events.jsonl").exists()
    events = (mission_root / "events.jsonl").read_text(encoding="utf-8")
    assert "standby.promoted" in events
    assert "checkpoint.accepted" in events

    status = mission_status(state.mission.mission_id, str(python_bug_repo))
    assert status["outcome"] == "success"

    branches = subprocess.run(["git", "branch", "--list", state.summary.branch_name], cwd=str(python_bug_repo), capture_output=True, text=True, check=True)
    assert state.summary.branch_name in branches.stdout

    db_path = mission_root / "mission.db"
    connection = sqlite3.connect(db_path)
    mission_cp = connection.execute("SELECT COUNT(*) FROM mission_state_checkpoints").fetchone()[0]
    repo_cp = connection.execute("SELECT COUNT(*) FROM repo_state_checkpoints").fetchone()[0]
    assert mission_cp >= 1
    assert repo_cp >= 1
    connection.close()


def test_start_mission_requires_initial_commit(tmp_path: Path) -> None:
    repo = tmp_path / "no_commit_repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True, text=True)

    with pytest.raises(WorktreeSetupError, match="at least one commit"):
        start_mission(
            repo=str(repo),
            objective="Fix failing tests",
            strategy_backend=ScriptedStrategyBackend([]),
        )
