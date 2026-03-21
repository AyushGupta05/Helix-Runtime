from __future__ import annotations

from arbiter.core.contracts import Bid, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskType
from tests.fake_provider_backend import make_provider_backend


def _task() -> TaskNode:
    return TaskNode(
        task_id="T1",
        title="Fix calculator maintainability issue",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="tests pass"),
        allowed_tools=["read_file", "edit_file", "run_tests"],
        candidate_files=["calc.py", "tests/test_calc.py"],
        validator_requirements=["tests"],
    )


def _bid(provider: str = "anthropic") -> Bid:
    return Bid(
        bid_id="b1",
        task_id="T1",
        role="Safe",
        provider=provider,
        lane=f"bid_deep.{provider}",
        model_id=f"{provider}-bid_deep",
        invocation_id="inv-1",
        variant_id="safe-base",
        strategy_family="localized-fix",
        strategy_summary="Patch the calculator defect with minimal churn.",
        exact_action="Edit calc.py and tests/test_calc.py.",
        expected_benefit=0.75,
        utility=0.8,
        confidence=0.82,
        risk=0.2,
        cost=0.1,
        estimated_runtime_seconds=45,
        touched_files=["calc.py", "tests/test_calc.py"],
        rollback_plan="Revert the patch.",
    )


def _candidate_files() -> dict[str, str]:
    return {
        "calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    }


def test_generate_edit_proposal_uses_the_bid_provider_for_preview() -> None:
    backend = make_provider_backend(providers=("openai", "anthropic"))

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="anthropic"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        preview=True,
    )

    assert proposal.files
    assert invocation.provider == "anthropic"
    assert invocation.lane == "bid_deep.anthropic"


def test_generate_edit_proposal_returns_safe_empty_result_when_provider_generation_fails() -> None:
    backend = make_provider_backend(providers=("anthropic",), fail_proposal_generation=True)

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="anthropic"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        preview=True,
    )

    assert proposal.files == []
    assert proposal.notes == ["provider_generation_failed"]
    assert invocation.status == "failed"
    assert invocation.error == "Provider proposal generation produced no viable candidate."
