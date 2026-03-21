from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from arbiter.agents.backend import EditProposal, FileUpdate, ScriptedStrategyBackend
from arbiter.core.contracts import MissionSummary, ModelInvocation, RunState, utc_now
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.app import create_app
from arbiter.server.manager import MissionService
from arbiter.server.schemas import MissionCreateRequest


class SlowScriptedStrategyBackend(ScriptedStrategyBackend):
    def __init__(self, scripted: list[EditProposal], delay_seconds: float = 0.6) -> None:
        super().__init__(scripted)
        self.delay_seconds = delay_seconds

    def generate_edit_proposal(self, *args, **kwargs):
        time.sleep(self.delay_seconds)
        return super().generate_edit_proposal(*args, **kwargs)


def wait_for(predicate: Callable[[], bool], timeout: float = 40.0) -> None:
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


def repo_query(repo: Path) -> str:
    return f"?repo={quote(str(repo.resolve()), safe='')}"


def test_api_creates_snapshot_and_history(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    with TestClient(create_app(strategy_backend_factory=scripted_factory())) as client:
        response = client.post("/api/missions", json=payload(python_bug_repo))
        assert response.status_code == 200
        mission_id = response.json()["mission_id"]

        def mission_finished() -> bool:
            snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}")
            return snapshot.status_code == 200 and snapshot.json()["outcome"] is not None

        wait_for(mission_finished)
        snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["mission_id"] == mission_id
        assert body["repo_path"] == str(python_bug_repo.resolve())
        assert body["outcome"] == "success"
        assert body["created_at"]
        assert body["updated_at"]
        assert body["runtime_seconds"] >= 0
        assert body["latest_event_id"] > 0
        assert body["tasks"]
        assert body["bids"]
        assert body["events"]
        assert body["guardrail_state"]["policy_state"] in {"clear", "restricted", "blocked"}
        assert body["mission_meta"]["repo_name"] == python_bug_repo.name
        assert body["mission_meta"]["elapsed_seconds"] >= 0
        assert body["history_metrics"]["checkpoint_count"] >= 1
        assert body["repo_insights"]["runtime"] == "python"
        assert body["outcome_summary"]["plain_summary"]
        assert "ledger" in body["civic_activity"]
        assert "stream" in body["activity_summary"]
        assert body["usage_summary"]["mission"]["total_tokens"] >= 0
        assert "worktree_state" in body
        assert "mission_output" in body
        assert "mission_state_checkpoints" in body
        assert "repo_state_checkpoints" in body
        assert "recent_trace" in body
        assert "provider_market_summary" in body
        assert "civic_connection" in body
        assert "civic_capabilities" in body
        assert "available_skills" in body
        assert "skill_health" in body
        assert "skill_outputs" in body
        assert "governed_bid_envelopes" in body
        assert "recent_civic_actions" in body

        trace = client.get(f"/api/missions/{mission_id}/trace{repo_query(python_bug_repo)}")
        assert trace.status_code == 200
        assert any(item["trace_type"] == "proposal.selected" for item in trace.json())

        diff = client.get(f"/api/missions/{mission_id}/diff{repo_query(python_bug_repo)}")
        assert diff.status_code == 200
        assert "worktree_state" in diff.json()
        assert "mission_output" in diff.json()

        usage = client.get(f"/api/missions/{mission_id}/usage{repo_query(python_bug_repo)}")
        assert usage.status_code == 200
        assert "mission" in usage.json()

        history = client.get(f"/api/missions{repo_query(python_bug_repo)}")
        assert history.status_code == 200
        missions = history.json()
        assert missions
        assert missions[0]["mission_id"] == mission_id
        assert missions[0]["outcome"] == "success"
        assert missions[0]["runtime_seconds"] >= 0
        assert missions[0]["checkpoint_count"] >= 1
        assert missions[0]["validator_status"] in {"passed", "failed", "pending"}


def test_api_streams_ordered_sse_events(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    with TestClient(create_app(strategy_backend_factory=scripted_factory(0.15))) as client:
        mission_id = client.post("/api/missions", json=payload(python_bug_repo)).json()["mission_id"]

        received: list[dict[str, object]] = []
        response = client.get(f"/api/missions/{mission_id}/events?after_id=0&repo={quote(str(python_bug_repo.resolve()), safe='')}")
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
    with TestClient(create_app(strategy_backend_factory=scripted_factory(0.6))) as client:
        mission_id = client.post("/api/missions", json=payload(python_bug_repo)).json()["mission_id"]

        pause = client.post(f"/api/missions/{mission_id}/pause{repo_query(python_bug_repo)}")
        assert pause.status_code == 200
        assert pause.json()["run_state"] == "pause_requested"

        def mission_paused() -> bool:
            snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}")
            return snapshot.status_code == 200 and snapshot.json()["run_state"] == "paused"

        wait_for(mission_paused)
        resumed = client.post(f"/api/missions/{mission_id}/resume{repo_query(python_bug_repo)}")
        assert resumed.status_code == 200
        assert resumed.json()["run_state"] == "running"

        def mission_finished() -> bool:
            snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}")
            return snapshot.status_code == 200 and snapshot.json()["outcome"] == "success"

        wait_for(mission_finished)
        snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}").json()
        event_types = [event["event_type"] for event in snapshot["events"]]
        assert "mission.paused" in event_types
        assert "mission.resumed" in event_types


def test_api_blocks_second_active_mission_and_allows_cancel(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    with TestClient(create_app(strategy_backend_factory=scripted_factory(0.8))) as client:
        first = client.post("/api/missions", json=payload(python_bug_repo))
        assert first.status_code == 200
        mission_id = first.json()["mission_id"]

        second = client.post("/api/missions", json=payload(python_bug_repo))
        assert second.status_code == 409

        cancelled = client.post(f"/api/missions/{mission_id}/cancel{repo_query(python_bug_repo)}")
        assert cancelled.status_code == 200
        assert cancelled.json()["run_state"] == "cancelling"

        def mission_cancelled() -> bool:
            snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}")
            return snapshot.status_code == 200 and snapshot.json()["outcome"] == "failed_safe_stop"

        wait_for(mission_cancelled)
        snapshot = client.get(f"/api/missions/{mission_id}{repo_query(python_bug_repo)}").json()
        assert snapshot["run_state"] == "finalized"
        assert snapshot["outcome"] == "failed_safe_stop"


def test_api_returns_400_for_invalid_provider_config(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    with TestClient(create_app(strategy_backend_factory=scripted_factory())) as client:
        response = client.post("/api/missions", json=payload(python_bug_repo))

    assert response.status_code == 400
    assert "MODEL_PROVIDER" in response.json()["detail"]


def test_api_reports_civic_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    monkeypatch.delenv("CIVIC_URL", raising=False)
    monkeypatch.delenv("CIVIC_TOKEN", raising=False)
    with TestClient(create_app(strategy_backend_factory=scripted_factory())) as client:
        response = client.get("/api/civic/health")

    assert response.status_code == 200
    body = response.json()
    assert "civic_connection" in body
    assert "civic_capabilities" in body
    assert "available_skills" in body
    assert "skill_health" in body


def test_api_list_missions_requires_explicit_repo(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    with TestClient(create_app(strategy_backend_factory=scripted_factory(0.2))) as client:
        response = client.post("/api/missions", json=payload(python_bug_repo))
        assert response.status_code == 200

        history = client.get("/api/missions")

    assert history.status_code == 200
    assert history.json() == []


def test_list_history_refreshes_stale_cached_run_state(python_bug_repo: Path) -> None:
    repo_path = str(python_bug_repo.resolve())
    mission_id = "stale-view-cache"
    base_time = utc_now()
    created_at = (base_time - timedelta(minutes=2)).isoformat()
    running_updated_at = (base_time - timedelta(minutes=1)).isoformat()
    finalized_updated_at = (base_time + timedelta(minutes=1)).isoformat()
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        store.upsert_mission(
            mission_id=mission_id,
            status="running",
            repo_path=repo_path,
            objective="Fix the failing checkout tests",
            branch_name="codex/test",
            outcome=None,
            spec=MissionCreateRequest(repo=repo_path, objective="Fix the failing checkout tests"),
            summary=MissionSummary(
                mission_id=mission_id,
                repo_path=repo_path,
                objective="Fix the failing checkout tests",
            ),
            created_at=created_at,
        )
        store.connection.execute(
            "UPDATE mission SET updated_at = ? WHERE id = ?",
            (running_updated_at, mission_id),
        )
        store.connection.commit()
        store.upsert_control_state(
            mission_id,
            RunState.RUNNING.value,
            None,
            None,
            running_updated_at,
        )
        stale_view = store.refresh_mission_view(mission_id)
        assert stale_view["run_state"] == RunState.RUNNING.value

        store.upsert_control_state(
            mission_id,
            RunState.FINALIZED.value,
            None,
            "session_ended",
            finalized_updated_at,
        )
    finally:
        store.close()

    history = MissionService().list_history(repo_path)
    stale_entry = next(item for item in history if item.mission_id == mission_id)

    assert stale_entry.run_state == RunState.FINALIZED.value
    assert stale_entry.status == RunState.FINALIZED.value
    assert stale_entry.outcome == "failed_safe_stop"
    assert stale_entry.created_at == created_at
    assert stale_entry.updated_at == finalized_updated_at


def test_refresh_view_does_not_invent_failed_safe_stop_for_successful_finalize(python_bug_repo: Path) -> None:
    repo_path = str(python_bug_repo.resolve())
    mission_id = "finalize-race"
    created_at = utc_now().isoformat()
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        store.upsert_mission(
            mission_id=mission_id,
            status="running",
            repo_path=repo_path,
            objective="Fix the failing checkout tests",
            branch_name="codex/test",
            outcome=None,
            spec=MissionCreateRequest(repo=repo_path, objective="Fix the failing checkout tests"),
            summary=MissionSummary(
                mission_id=mission_id,
                repo_path=repo_path,
                objective="Fix the failing checkout tests",
            ),
            created_at=created_at,
        )
        store.upsert_runtime(
            mission_id,
            active_phase="finalize",
            active_task_id="T1",
            active_bid_round=1,
            simulation_round=0,
            recovery_round=0,
            winner_bid_id=None,
            standby_bid_id=None,
            latest_diff_summary="",
            stop_reason="mission_objective_met",
            policy_state="clear",
            current_risk_score=0.0,
            simulation_summary=None,
            worktree_state={},
            bidding_state={},
            latest_validation_task_id=None,
            latest_failure_task_id=None,
            accepted_checkpoint_id=None,
            civic_connection={},
            civic_capabilities=[],
            available_skills=[],
            skill_health={},
            skill_outputs={},
        )
        store.upsert_control_state(
            mission_id,
            RunState.FINALIZED.value,
            None,
            "mission_objective_met",
            created_at,
        )

        view = store.refresh_mission_view(mission_id)
    finally:
        store.close()

    assert view["run_state"] == RunState.FINALIZED.value
    assert view["outcome"] is None


def test_list_history_keeps_recent_running_mission_live(python_bug_repo: Path) -> None:
    repo_path = str(python_bug_repo.resolve())
    mission_id = "recent-running"
    recent_time = utc_now().isoformat()
    created_at = (utc_now() - timedelta(minutes=1)).isoformat()
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        store.upsert_mission(
            mission_id=mission_id,
            status="running",
            repo_path=repo_path,
            objective="Fix the failing checkout tests",
            branch_name="codex/test",
            outcome=None,
            spec=MissionCreateRequest(repo=repo_path, objective="Fix the failing checkout tests"),
            summary=MissionSummary(
                mission_id=mission_id,
                repo_path=repo_path,
                objective="Fix the failing checkout tests",
            ),
            created_at=created_at,
        )
        store.connection.execute(
            "UPDATE mission SET updated_at = ? WHERE id = ?",
            (recent_time, mission_id),
        )
        store.connection.commit()
        store.upsert_runtime(
            mission_id,
            active_phase="strategize",
            active_task_id="T1",
            active_bid_round=1,
            simulation_round=0,
            recovery_round=0,
            winner_bid_id=None,
            standby_bid_id=None,
            latest_diff_summary="",
            stop_reason=None,
            policy_state="clear",
            current_risk_score=0.0,
            simulation_summary=None,
            worktree_state={},
            bidding_state={},
            civic_connection={},
            civic_capabilities=[],
            available_skills=[],
            skill_health={},
            skill_outputs={},
            latest_validation_task_id=None,
            latest_failure_task_id=None,
            accepted_checkpoint_id=None,
        )
        store.upsert_control_state(
            mission_id,
            RunState.RUNNING.value,
            None,
            None,
            recent_time,
        )
    finally:
        store.close()

    history = MissionService().list_history(repo_path)
    recent_entry = next(item for item in history if item.mission_id == mission_id)

    assert recent_entry.run_state == RunState.RUNNING.value
    assert recent_entry.outcome is None


def test_orphan_finalization_waits_for_recent_inflight_model_work(python_bug_repo: Path) -> None:
    repo_path = str(python_bug_repo.resolve())
    mission_id = "recent-inflight-model"
    base_time = utc_now()
    created_at = (base_time - timedelta(minutes=2)).isoformat()
    stale_time = (base_time - timedelta(minutes=1)).isoformat()
    in_flight_started_at = (base_time - timedelta(seconds=10)).isoformat()
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        store.upsert_mission(
            mission_id=mission_id,
            status="running",
            repo_path=repo_path,
            objective="Fix the failing checkout tests",
            branch_name="codex/test",
            outcome=None,
            spec=MissionCreateRequest(repo=repo_path, objective="Fix the failing checkout tests"),
            summary=MissionSummary(
                mission_id=mission_id,
                repo_path=repo_path,
                objective="Fix the failing checkout tests",
            ),
            created_at=created_at,
        )
        store.connection.execute(
            "UPDATE mission SET updated_at = ? WHERE id = ?",
            (stale_time, mission_id),
        )
        store.connection.commit()
        store.upsert_runtime(
            mission_id,
            active_phase="strategize",
            active_task_id=None,
            active_bid_round=0,
            simulation_round=0,
            recovery_round=0,
            winner_bid_id=None,
            standby_bid_id=None,
            latest_diff_summary="",
            stop_reason=None,
            policy_state="clear",
            current_risk_score=0.0,
            simulation_summary=None,
            worktree_state={},
            bidding_state={},
            latest_validation_task_id=None,
            latest_failure_task_id=None,
            accepted_checkpoint_id=None,
            civic_connection={},
            civic_capabilities=[],
            available_skills=[],
            skill_health={},
            skill_outputs={},
        )
        store.connection.execute(
            "UPDATE mission_runtime SET updated_at = ? WHERE mission_id = ?",
            (stale_time, mission_id),
        )
        store.connection.commit()
        store.upsert_control_state(
            mission_id,
            RunState.RUNNING.value,
            None,
            None,
            stale_time,
        )
        store.save_model_invocation(
            mission_id=mission_id,
            invocation=ModelInvocation(
                invocation_id="inv-recent",
                mission_id=mission_id,
                task_id=None,
                bid_id=None,
                provider="openai",
                lane="triage.openai",
                model_id="gpt-5-mini",
                invocation_kind="mission_planning",
                status="started",
                generation_mode="provider_model",
                started_at=in_flight_started_at,
                completed_at=None,
            ),
            invocation_id="inv-recent",
            task_id=None,
            bid_id=None,
            provider="openai",
            lane="triage.openai",
            model_id="gpt-5-mini",
            invocation_kind="mission_planning",
            status="started",
            generation_mode="provider_model",
            started_at=in_flight_started_at,
            completed_at=None,
            prompt_preview="Objective: investigate",
        )
    finally:
        store.close()

    service = MissionService()
    service._finalize_orphaned_mission(repo_path, mission_id, reason="session_ended")

    store = MissionStore(paths.db_path)
    try:
        control = store.fetch_control_state(mission_id)
        mission = store.fetch_mission(mission_id)
    finally:
        store.close()

    assert control["run_state"] == RunState.RUNNING.value
    assert mission["outcome"] is None


def test_refresh_view_keeps_transient_session_end_running_when_model_work_is_inflight(python_bug_repo: Path) -> None:
    repo_path = str(python_bug_repo.resolve())
    mission_id = "transient-session-ended"
    created_at = utc_now().isoformat()
    paths = build_mission_paths(repo_path, mission_id)
    store = MissionStore(paths.db_path)
    try:
        store.upsert_mission(
            mission_id=mission_id,
            status="finalized",
            repo_path=repo_path,
            objective="Fix the failing checkout tests",
            branch_name="codex/test",
            outcome="failed_safe_stop",
            spec=MissionCreateRequest(repo=repo_path, objective="Fix the failing checkout tests"),
            summary=MissionSummary(
                mission_id=mission_id,
                repo_path=repo_path,
                objective="Fix the failing checkout tests",
                outcome="failed_safe_stop",
            ),
            created_at=created_at,
        )
        store.upsert_runtime(
            mission_id,
            active_phase="strategize",
            active_task_id=None,
            active_bid_round=0,
            simulation_round=0,
            recovery_round=0,
            winner_bid_id=None,
            standby_bid_id=None,
            latest_diff_summary="",
            stop_reason=None,
            policy_state="clear",
            current_risk_score=0.0,
            simulation_summary=None,
            worktree_state={},
            bidding_state={},
            latest_validation_task_id=None,
            latest_failure_task_id=None,
            accepted_checkpoint_id=None,
            civic_connection={},
            civic_capabilities=[],
            available_skills=[],
            skill_health={},
            skill_outputs={},
        )
        store.upsert_control_state(
            mission_id,
            RunState.FINALIZED.value,
            None,
            "session_ended",
            created_at,
        )
        store.save_model_invocation(
            mission_id=mission_id,
            invocation=ModelInvocation(
                invocation_id="inv-inflight",
                mission_id=mission_id,
                task_id=None,
                bid_id=None,
                provider="openai",
                lane="triage.openai",
                model_id="gpt-5-mini",
                invocation_kind="mission_planning",
                status="started",
                generation_mode="provider_model",
                started_at=created_at,
                completed_at=None,
            ),
            invocation_id="inv-inflight",
            task_id=None,
            bid_id=None,
            provider="openai",
            lane="triage.openai",
            model_id="gpt-5-mini",
            invocation_kind="mission_planning",
            status="started",
            generation_mode="provider_model",
            started_at=created_at,
            completed_at=None,
            prompt_preview="Objective: investigate",
        )

        view = store.refresh_mission_view(mission_id)
    finally:
        store.close()

    assert view["run_state"] == RunState.RUNNING.value
    assert view["status"] == "strategize"
    assert view["outcome"] is None
