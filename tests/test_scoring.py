from __future__ import annotations

import pytest

from arbiter.core.contracts import Bid, MissionSpec, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskType
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
    assert score_bid(bid) == pytest.approx(0.4 * 0.8 + 0.25 * 0.7 - 0.2 * 0.2 - 0.15 * 0.1, abs=1e-4)


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
