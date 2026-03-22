from __future__ import annotations

from arbiter.core.contracts import Bid, FailureContext
from arbiter.mission.recovery import RecoveryEngine


def _bid(*, bid_id: str, strategy_family: str, touched_files: list[str]) -> Bid:
    return Bid(
        bid_id=bid_id,
        task_id="T1",
        role="Safe",
        provider="anthropic",
        lane="bid_deep.anthropic",
        model_id="claude-sonnet-4",
        invocation_id=f"inv-{bid_id}",
        variant_id="safe-base",
        strategy_family=strategy_family,
        strategy_summary="Apply a bounded runtime fix.",
        exact_action="Edit the targeted files and rerun validators.",
        expected_benefit=0.7,
        utility=0.7,
        confidence=0.8,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        touched_files=touched_files,
        rollback_plan="revert",
    )


def test_plan_recovery_rebids_when_validator_points_outside_standby_scope() -> None:
    engine = RecoveryEngine()
    current = _bid(
        bid_id="winner",
        strategy_family="speed-first",
        touched_files=["backend/app/services/sla_service.py"],
    )
    standby = _bid(
        bid_id="standby",
        strategy_family="quality-coverage",
        touched_files=["backend/app/services/sla_service.py"],
    )
    failure = FailureContext(
        task_id="T1",
        failure_type="validation_failure",
        details="validation_failed",
        diff_summary="",
        validator_deltas=[
            "FAILED tests/test_settings.py::test_settings_round_trip_persists_retry_fields",
            "tests/test_settings.py:14: AssertionError",
        ],
        recommended_recovery_scope="standby_or_rebid",
        strategy_family=current.strategy_family,
        attempted_file_scope=current.touched_files,
        rollback_result="rollback_succeeded",
    )

    plan = engine.plan_recovery(current, standby, failure)

    assert plan.action == "rebid"
    assert "validator_scope_outside_attempted" in plan.evidence
    assert "validator_scope_outside_standby" in plan.evidence
