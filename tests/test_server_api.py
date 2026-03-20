from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from arbiter.agents.backend import EditProposal, FileUpdate, ScriptedStrategyBackend
from arbiter.server.app import create_app


class SlowScriptedStrategyBackend(ScriptedStrategyBackend):
    def __init__(self, scripted: list[EditProposal], delay_seconds: float = 0.6) -> None:
        super().__init__(scripted)
        self.delay_seconds = delay_seconds

    def generate_edit_proposal(self, *args, **kwargs):
        time.sleep(self.delay_seconds)
        return super().generate_edit_proposal(*args, **kwargs)


def wait_for(predicate: Callable[[], bool], timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Condition was not met before timeout.")


def scripted_factory(delay_seconds: float = 0.0):
    proposals = [
        EditProposal(
            summary="Apply the correct calculator fix.",
            files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
        ),
        EditProposal(
            summary="Add regression coverage for zero inputs.",
            files=[
                FileUpdate(
                    path="tests/test_calc.py",
                    content=(
                        "from calc import add\n\n\n"
                        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
                        "def test_zero():\n    assert add(0, 0) == 0\n"
                    ),
                )
            ],
        ),
    ]
    backend_type = SlowScriptedStrategyBackend if delay_seconds else ScriptedStrategyBackend
    return lambda: backend_type(proposals, delay_seconds) if delay_seconds else backend_type(proposals)


def payload(repo: Path) -> dict[str, object]:
    return {
        "repo": str(repo),
        "objective": "Fix failing tests and improve reliability",
        "constraints": ["no public api changes"],
        "preferences": ["minimal file churn"],
        "max_runtime": 5,
    }


def test_api_creates_snapshot_and_history(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    client = TestClient(create_app(strategy_backend_factory=scripted_factory()))
    response = client.post("/api/missions", json=payload(python_bug_repo))
    assert response.status_code == 200
    mission_id = response.json()["mission_id"]

    def mission_finished() -> bool:
        snapshot = client.get(f"/api/missions/{mission_id}")
        return snapshot.status_code == 200 and snapshot.json()["outcome"] is not None

    wait_for(mission_finished)
    snapshot = client.get(f"/api/missions/{mission_id}")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["mission_id"] == mission_id
    assert body["repo_path"] == str(python_bug_repo.resolve())
    assert body["outcome"] == "success"
    assert body["latest_event_id"] > 0
    assert body["tasks"]
    assert body["bids"]
    assert body["events"]

    history = client.get("/api/missions")
    assert history.status_code == 200
    missions = history.json()
    assert missions
    assert missions[0]["mission_id"] == mission_id
    assert missions[0]["outcome"] == "success"


def test_api_streams_ordered_sse_events(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    client = TestClient(create_app(strategy_backend_factory=scripted_factory(0.15)))
    mission_id = client.post("/api/missions", json=payload(python_bug_repo)).json()["mission_id"]

    received: list[dict[str, object]] = []
    response = client.get(f"/api/missions/{mission_id}/events?after_id=0")
    assert response.status_code == 200
    current_event: dict[str, object] = {}
    for line in response.text.splitlines():
        if not line:
            continue
        if line.startswith("id:"):
            current_event["id"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("event:"):
            current_event["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current_event["data"] = json.loads(line.split(":", 1)[1].strip())
            received.append(current_event)
            current_event = {}

    assert [item["id"] for item in received] == sorted(item["id"] for item in received)
    event_types = [item["event"] for item in received]
    assert "mission.started" in event_types
    assert "repo.scan.completed" in event_types


def test_api_pause_and_resume_mission(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    client = TestClient(create_app(strategy_backend_factory=scripted_factory(0.6)))
    mission_id = client.post("/api/missions", json=payload(python_bug_repo)).json()["mission_id"]

    pause = client.post(f"/api/missions/{mission_id}/pause")
    assert pause.status_code == 200
    assert pause.json()["run_state"] == "pause_requested"

    def mission_paused() -> bool:
        snapshot = client.get(f"/api/missions/{mission_id}")
        return snapshot.status_code == 200 and snapshot.json()["run_state"] == "paused"

    wait_for(mission_paused)
    resumed = client.post(f"/api/missions/{mission_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["run_state"] == "running"

    def mission_finished() -> bool:
        snapshot = client.get(f"/api/missions/{mission_id}")
        return snapshot.status_code == 200 and snapshot.json()["outcome"] == "success"

    wait_for(mission_finished)
    snapshot = client.get(f"/api/missions/{mission_id}").json()
    event_types = [event["event_type"] for event in snapshot["events"]]
    assert "mission.paused" in event_types
    assert "mission.resumed" in event_types


def test_api_blocks_second_active_mission_and_allows_cancel(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    client = TestClient(create_app(strategy_backend_factory=scripted_factory(0.8)))
    first = client.post("/api/missions", json=payload(python_bug_repo))
    assert first.status_code == 200
    mission_id = first.json()["mission_id"]

    second = client.post("/api/missions", json=payload(python_bug_repo))
    assert second.status_code == 409

    cancelled = client.post(f"/api/missions/{mission_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["run_state"] == "cancelling"

    def mission_cancelled() -> bool:
        snapshot = client.get(f"/api/missions/{mission_id}")
        return snapshot.status_code == 200 and snapshot.json()["outcome"] == "failed_safe_stop"

    wait_for(mission_cancelled)
    snapshot = client.get(f"/api/missions/{mission_id}").json()
    assert snapshot["run_state"] == "finalized"
    assert snapshot["outcome"] == "failed_safe_stop"
