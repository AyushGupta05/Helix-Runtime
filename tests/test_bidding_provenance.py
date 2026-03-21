from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from arbiter.mission.runner import start_mission
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.app import create_app
from tests.fake_provider_backend import make_provider_backend


def _configure_provider_env(monkeypatch, *, allow_fallback: bool) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ARBITER_ENABLED_PROVIDERS", "openai")
    monkeypatch.setenv("ARBITER_REQUIRE_REAL_PROVIDER_BIDDING", "1")
    monkeypatch.setenv("ARBITER_ALLOW_DEGRADED_BID_FALLBACK", "1" if allow_fallback else "0")
    monkeypatch.setenv("ARBITER_REPLAY_MODE", "off")


def _wait_for(predicate: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Condition was not met before timeout.")


def _db_path(repo: Path, mission_id: str) -> Path:
    return Path(build_mission_paths(str(repo.resolve()), mission_id).db_path)


def test_provider_backed_bidding_persists_invocations_provenance_and_usage(python_bug_repo: Path, monkeypatch) -> None:
    _configure_provider_env(monkeypatch, allow_fallback=False)
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=make_provider_backend(),
    )

    connection = sqlite3.connect(_db_path(python_bug_repo, state.mission.mission_id))
    try:
        connection.row_factory = sqlite3.Row
        invocations = connection.execute(
            "SELECT * FROM model_invocations WHERE invocation_kind = 'bid_generation' AND status = 'completed' ORDER BY started_at ASC"
        ).fetchall()
        bids = [
            json.loads(row["payload_json"])
            for row in connection.execute("SELECT payload_json FROM bids ORDER BY updated_at ASC").fetchall()
        ]
    finally:
        connection.close()

    assert invocations
    assert all(row["provider"] == "openai" for row in invocations)
    assert all(row["lane"].startswith("bid_") or row["lane"].startswith("test_gen") or row["lane"].startswith("perf_reason") for row in invocations)
    assert all(row["model_id"] for row in invocations)
    assert all(row["generation_mode"] == "provider_model" for row in invocations)
    assert any(json.loads(row["token_usage_json"]) for row in invocations)
    assert any(json.loads(row["cost_usage_json"]) for row in invocations)

    provider_bids = [bid for bid in bids if bid["generation_mode"] == "provider_model"]
    assert provider_bids
    assert all(bid["provider"] == "openai" for bid in provider_bids)
    assert all(bid["lane"] for bid in provider_bids)
    assert all(bid["model_id"] for bid in provider_bids)
    assert all(bid["invocation_id"] for bid in provider_bids)
    assert any(bid["token_usage"] for bid in provider_bids)
    assert any(bid["cost_usage"] for bid in provider_bids)


def test_snapshot_exposes_provider_backed_bid_provenance(python_bug_repo: Path, tmp_path: Path, monkeypatch) -> None:
    _configure_provider_env(monkeypatch, allow_fallback=False)
    monkeypatch.setenv("ARBITER_CONTROL_ROOT", str(tmp_path / "control"))
    with TestClient(create_app(strategy_backend_factory=lambda: make_provider_backend())) as client:
        response = client.post(
            "/api/missions",
            json={
                "repo": str(python_bug_repo),
                "objective": "Fix failing tests and improve reliability",
                "constraints": [],
                "preferences": [],
                "max_runtime": 5,
            },
        )
        mission_id = response.json()["mission_id"]

        def snapshot_has_provider_bids() -> bool:
            snapshot = client.get(f"/api/missions/{mission_id}?repo={quote(str(python_bug_repo.resolve()), safe='')}")
            if snapshot.status_code != 200:
                return False
            body = snapshot.json()
            return bool(body["bids"]) and body["bidding_state"].get("total_provider_invocations", 0) > 0

        _wait_for(snapshot_has_provider_bids, timeout=30.0)
        snapshot = client.get(f"/api/missions/{mission_id}?repo={quote(str(python_bug_repo.resolve()), safe='')}").json()
        client.post(f"/api/missions/{mission_id}/cancel?repo={quote(str(python_bug_repo.resolve()), safe='')}")

        def mission_cancelled() -> bool:
            response = client.get(f"/api/missions/{mission_id}?repo={quote(str(python_bug_repo.resolve()), safe='')}")
            return response.status_code == 200 and response.json()["run_state"] == "finalized"

        _wait_for(mission_cancelled, timeout=30.0)

    provider_bid = next(bid for bid in snapshot["bids"] if bid["generation_mode"] == "provider_model")
    assert snapshot["bidding_state"]["total_provider_invocations"] > 0
    assert snapshot["usage_summary"]["invocations"]
    assert provider_bid["provider"] == "openai"
    assert provider_bid["model_id"]
    assert provider_bid["invocation_id"]
    assert provider_bid["generation_mode"] == "provider_model"


def test_fallback_bids_are_explicit_when_provider_generation_fails_and_fallback_allowed(python_bug_repo: Path, monkeypatch) -> None:
    _configure_provider_env(monkeypatch, allow_fallback=True)
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=make_provider_backend(fail_bid_generation=True, fail_proposal_generation=False),
    )
    store = MissionStore(_db_path(python_bug_repo, state.mission.mission_id))
    try:
        snapshot = store.get_mission_view(state.mission.mission_id)
    finally:
        store.close()

    assert snapshot["bidding_state"]["degraded"] is True
    assert snapshot["bidding_state"]["generation_mode"] == "deterministic_fallback"
    assert any(bid["generation_mode"] == "deterministic_fallback" for bid in snapshot["bids"])
    assert not any(bid["generation_mode"] == "provider_model" for bid in snapshot["bids"])
    assert all((bid["provider"] in {None, "system"}) for bid in snapshot["bids"])
    assert snapshot["usage_summary"]["invocations"]
    assert any(
        invocation["status"] == "failed" and invocation["generation_mode"] == "provider_model"
        for invocation in snapshot["usage_summary"]["invocations"]
    )


def test_architecture_violation_is_emitted_when_fallback_is_disallowed(python_bug_repo: Path, monkeypatch) -> None:
    _configure_provider_env(monkeypatch, allow_fallback=False)
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=make_provider_backend(fail_bid_generation=True, fail_proposal_generation=False),
    )
    store = MissionStore(_db_path(python_bug_repo, state.mission.mission_id))
    try:
        snapshot = store.get_mission_view(state.mission.mission_id)
    finally:
        store.close()

    assert state.outcome is not None
    assert state.outcome.value == "failed_execution"
    assert snapshot["bidding_state"]["architecture_violation"]
    assert snapshot["bidding_state"]["total_provider_invocations"] > 0
    assert snapshot["bids"] == []
    assert not any(event["event_type"] == "bid.submitted" and event["payload"].get("provider") == "system" for event in snapshot["events"])
    assert any(event["event_type"] == "bidding.architecture_violation" for event in snapshot["events"])


def test_provider_usage_is_persisted_as_null_with_reason_when_unavailable(python_bug_repo: Path, monkeypatch) -> None:
    _configure_provider_env(monkeypatch, allow_fallback=False)
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=make_provider_backend(include_usage=False),
    )

    connection = sqlite3.connect(_db_path(python_bug_repo, state.mission.mission_id))
    try:
        connection.row_factory = sqlite3.Row
        invocation = connection.execute(
            "SELECT * FROM model_invocations WHERE invocation_kind = 'bid_generation' AND status = 'completed' LIMIT 1"
        ).fetchone()
        bid = json.loads(
            connection.execute(
                "SELECT payload_json FROM bids WHERE payload_json LIKE '%\"generation_mode\":\"provider_model\"%' LIMIT 1"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert invocation is not None
    assert invocation["token_usage_json"] == "null"
    assert invocation["cost_usage_json"] == "null"
    assert invocation["usage_unavailable_reason"]
    assert bid["token_usage"] is None
    assert bid["cost_usage"] is None
    assert bid["usage_unavailable_reason"]
