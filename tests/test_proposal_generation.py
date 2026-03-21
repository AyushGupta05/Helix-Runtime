from __future__ import annotations

import json
from types import SimpleNamespace

from arbiter.agents.backend import DefaultStrategyBackend
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
    assert invocation.lane == "proposal_gen.anthropic"


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


def test_generate_edit_proposal_emits_invocation_callbacks_for_preview() -> None:
    backend = make_provider_backend(providers=("anthropic",))
    invocations: list[dict[str, object]] = []

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="anthropic"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        preview=True,
        on_invocation=lambda payload: invocations.append(payload),
    )

    assert proposal.files
    assert invocation.provider == "anthropic"
    assert any(item["status"] == "started" for item in invocations)
    completed = [item for item in invocations if item["status"] == "completed"]
    assert completed
    assert completed[-1]["invocation_kind"] == "proposal_generation"
    assert completed[-1]["lane"] == "proposal_gen.anthropic"


def test_parse_edit_proposal_accepts_openai_response_items_payload() -> None:
    payload = json.dumps(
        [
            {"id": "rs_1", "summary": [], "type": "reasoning"},
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "summary": "Apply the calculator fix.",
                        "files": [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}],
                        "notes": ["provider_generated"],
                    }
                ),
            },
        ]
    )

    proposal = DefaultStrategyBackend._parse_edit_proposal(payload)

    assert proposal.summary == "Apply the calculator fix."
    assert proposal.files[0].path == "calc.py"


def test_generate_edit_proposals_rejects_analysis_only_output_for_edit_tasks() -> None:
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(self, lane: str, prompt: dict[str, str]):
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Investigate the issue first.",
                    "files": [],
                    "notes": ["analysis_only"],
                }
            )
            return ModelInvocationResult(
                content=content,
                provider="openai",
                model_id="gpt-5-mini",
                lane=lane,
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    backend = DefaultStrategyBackend(_Router())
    invocations: list[dict[str, object]] = []

    candidates = backend.generate_edit_proposals(
        task=_task(),
        bid=_bid(provider="openai"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        on_invocation=lambda payload: invocations.append(payload),
    )

    assert candidates == []
    assert invocations[-1]["status"] == "failed"
    assert "no file updates" in str(invocations[-1]["error"]).lower()
