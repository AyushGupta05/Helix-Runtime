from __future__ import annotations

import pytest

from arbiter.core.contracts import Bid, FailureContext, MissionSpec, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskType
from arbiter.market.scoring import hard_filter_reason, score_bid


def test_score_bid_uses_declared_formula() -> None:
    bid = Bid(
        bid_id="b1",
        task_id="T1",
        role="Safe",
        variant_id="safe-base",
        strategy_family="bugfix-base",
        strategy_summary="safe fix",
        exact_action="edit file",
        expected_benefit=0.7,
        utility=0.8,
        confidence=0.7,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        rollback_plan="revert",
    )
    expected = 0.4 * 0.8 + 0.25 * 0.7 - 0.2 * 0.2 - 0.15 * 0.1
    assert score_bid(bid) == pytest.approx(expected, abs=1e-4)


def test_score_bid_penalizes_policy_friction_and_revocation_risk() -> None:
    bid = Bid(
        bid_id="b1",
        task_id="T1",
        role="Safe",
        variant_id="safe-base",
        strategy_family="bugfix-base",
        strategy_summary="safe fix",
        exact_action="edit file",
        expected_benefit=0.7,
        utility=0.8,
        confidence=0.7,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        rollback_plan="revert",
        capability_reliance_score=0.65,
        policy_friction_score=0.4,
        revocation_risk_score=0.3,
    )
    assert score_bid(bid) < 0.4 * 0.8 + 0.25 * 0.7 - 0.2 * 0.2 - 0.15 * 0.1 + 0.06 * 0.65


def test_hard_filter_blocks_protected_paths() -> None:
    task = TaskNode(
        task_id="T1",
        title="Fix bug",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="done"),
        allowed_tools=["edit"],
        validator_requirements=[],
    )
    spec = MissionSpec(mission_id="m1", repo_path="C:/repo", objective="Fix bug", protected_paths=["api.py"])
    bid = Bid(
        bid_id="b1",
        task_id="T1",
        role="Safe",
        variant_id="safe-base",
        strategy_family="bugfix-base",
        strategy_summary="safe fix",
        exact_action="edit file",
        expected_benefit=0.7,
        utility=0.8,
        confidence=0.7,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        touched_files=["api.py"],
        rollback_plan="revert",
    )
    assert hard_filter_reason(bid, task, spec, {"edit"}, set()) == "touches_protected_path"


def test_score_bid_rewards_recovery_moves_that_span_failed_scope_and_new_focus() -> None:
    task = TaskNode(
        task_id="T1",
        title="Recover bugfix",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="done"),
        candidate_files=[
            "backend/app/services/sla_service.py",
            "backend/app/models/settings.py",
            "backend/app/routes/settings.py",
        ],
        validator_requirements=["tests"],
    )
    failure = FailureContext(
        task_id="T1",
        failure_type="validation_failure",
        details="validation_failed",
        diff_summary="",
        validator_deltas=["backend/tests/test_settings.py", "backend/app/routes/settings.py"],
        recommended_recovery_scope="rebid",
        attempted_file_scope=["backend/app/services/sla_service.py"],
    )
    repeated_bid = Bid(
        bid_id="b-repeat",
        task_id="T1",
        role="Safe",
        variant_id="safe-base",
        strategy_family="bugfix-base",
        strategy_summary="Repeat the previous narrow fix.",
        exact_action="edit file",
        expected_benefit=0.7,
        utility=0.8,
        confidence=0.7,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        touched_files=["backend/app/services/sla_service.py"],
        validator_plan=["tests"],
        rollback_plan="revert",
    )
    recovery_bid = repeated_bid.model_copy(
        update={
            "bid_id": "b-recovery",
            "touched_files": [
                "backend/app/services/sla_service.py",
                "backend/app/models/settings.py",
                "backend/app/routes/settings.py",
            ],
        }
    )

    assert score_bid(recovery_bid, task=task, failure_context=failure) > score_bid(repeated_bid, task=task, failure_context=failure)


def test_hard_filter_allows_tightly_focused_bugfix_scope_spillover_for_tests() -> None:
    task = TaskNode(
        task_id="T1",
        title="Recover bugfix",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="done"),
        allowed_tools=["edit", "run_tests"],
        validator_requirements=["tests"],
        candidate_files=[
            "backend/app/services/sla_service.py",
            "backend/app/models/settings.py",
            "backend/app/routes/settings.py",
            "frontend/src/pages/DashboardPage.jsx",
            "frontend/src/components/FilterBar.jsx",
            "frontend/src/pages/SettingsPage.jsx",
            "frontend/src/lib/api.js",
        ],
    )
    spec = MissionSpec(mission_id="m1", repo_path="C:/repo", objective="Fix bug")
    bid = Bid(
        bid_id="b1",
        task_id="T1",
        role="Safe",
        variant_id="safe-base",
        strategy_family="bugfix-base",
        strategy_summary="safe fix",
        exact_action="edit file",
        expected_benefit=0.7,
        utility=0.8,
        confidence=0.7,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=30,
        touched_files=[
            "backend/app/services/sla_service.py",
            "backend/app/models/settings.py",
            "backend/app/routes/settings.py",
            "frontend/src/pages/DashboardPage.jsx",
            "frontend/src/components/FilterBar.jsx",
            "frontend/src/pages/SettingsPage.jsx",
            "frontend/src/lib/api.js",
            "backend/tests/test_sla_service.py",
            "backend/tests/test_settings.py",
        ],
        validator_plan=["tests"],
        rollback_plan="revert",
    )

    assert hard_filter_reason(bid, task, spec, {"edit", "run_tests"}, set()) is None
