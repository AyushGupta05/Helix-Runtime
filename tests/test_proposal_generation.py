from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from arbiter.agents.backend import DefaultStrategyBackend, EditOperation, load_candidate_files
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


def test_generate_edit_proposal_uses_shorter_preview_timeout_and_scoped_files() -> None:
    captured: dict[str, object] = {}
    lane_config = SimpleNamespace(provider="anthropic", model_id="claude-sonnet-4", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["anthropic"],
                default_provider="anthropic",
                preview_request_timeout_seconds=11.0,
                model_lanes={"proposal_gen": lane_config, "proposal_gen.anthropic": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            captured["lane"] = lane
            captured["timeout"] = request_timeout_seconds
            captured["prompt"] = prompt["user"]
            content = json.dumps(
                {
                    "summary": "Apply a minimal preview patch.",
                    "files": [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}],
                    "notes": ["preview"],
                }
            )
            from arbiter.agents.backend import ModelInvocationResult

            return ModelInvocationResult(
                content=content,
                provider="anthropic",
                model_id="claude-sonnet-4",
                lane=lane,
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    backend = DefaultStrategyBackend(_Router())
    candidate_files = {
        "calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "docs/notes.md": "# notes\n" * 10,
    }

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="anthropic"),
        mission_objective="Fix failing tests",
        candidate_files=candidate_files,
        preview=True,
    )

    assert proposal.files
    assert invocation.provider == "anthropic"
    assert captured["lane"] == "proposal_gen.anthropic"
    assert captured["timeout"] == 11.0
    assert "FILE: calc.py" in str(captured["prompt"])
    assert "FILE: tests/test_calc.py" in str(captured["prompt"])
    assert "FILE: docs/notes.md" not in str(captured["prompt"])
    assert "Preview goal:" in str(captured["prompt"])


def test_generate_edit_proposal_uses_execution_timeout_for_real_edits() -> None:
    captured: dict[str, object] = {}
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                preview_request_timeout_seconds=11.0,
                proposal_request_timeout_seconds=23.0,
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            captured["lane"] = lane
            captured["timeout"] = request_timeout_seconds
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Apply a compact runtime fix.",
                    "operations": [
                        {"type": "replace", "path": "calc.py", "target": "return a - b", "content": "return a + b"}
                    ],
                    "notes": ["execution"],
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

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="openai"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        preview=False,
    )

    assert proposal.operations
    assert invocation.provider == "openai"
    assert captured["lane"] == "proposal_gen.openai"
    assert captured["timeout"] == 23.0


def test_load_candidate_files_adds_framework_context_from_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "backend" / "app" / "routes").mkdir(parents=True)
    (repo / "backend" / "app" / "models").mkdir(parents=True)
    (repo / "backend" / "app" / "services").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("fastapi==0.115.8\n", encoding="utf-8")
    (repo / "backend" / "app" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.routes.settings import router as settings_router\n"
        "app = FastAPI()\n"
        "app.include_router(settings_router)\n",
        encoding="utf-8",
    )
    (repo / "backend" / "app" / "models" / "settings.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class WebhookSettings(BaseModel):\n"
        "    retry_enabled: bool\n",
        encoding="utf-8",
    )
    (repo / "backend" / "app" / "services" / "webhook_service.py").write_text(
        "def load_settings():\n"
        "    return {}\n",
        encoding="utf-8",
    )
    (repo / "backend" / "app" / "routes" / "settings.py").write_text(
        "from fastapi import APIRouter\n"
        "from app.models.settings import WebhookSettings\n"
        "from app.services.webhook_service import load_settings\n\n"
        "router = APIRouter()\n",
        encoding="utf-8",
    )

    loaded = load_candidate_files(
        str(repo),
        ["backend/app/models/settings.py", "backend/app/routes/settings.py"],
    )

    assert "backend/app/models/settings.py" in loaded
    assert "backend/app/routes/settings.py" in loaded
    assert "backend/requirements.txt" in loaded
    assert "backend/app/main.py" in loaded
    assert "backend/app/services/webhook_service.py" in loaded


def test_generate_edit_proposal_keeps_execution_scope_broader_than_bid_touched_files() -> None:
    captured: dict[str, object] = {}
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                preview_request_timeout_seconds=11.0,
                proposal_request_timeout_seconds=23.0,
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del lane, request_timeout_seconds
            captured["prompt"] = prompt["user"]
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Apply a compact runtime fix.",
                    "operations": [
                        {"type": "replace", "path": "calc.py", "target": "return a - b", "content": "return a + b"}
                    ],
                    "notes": ["execution"],
                }
            )
            return ModelInvocationResult(
                content=content,
                provider="openai",
                model_id="gpt-5-mini",
                lane="proposal_gen.openai",
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    backend = DefaultStrategyBackend(_Router())
    bid = _bid(provider="openai")
    bid.touched_files = ["calc.py"]
    candidate_files = {
        "calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "docs/notes.md": "# notes\n" * 10,
    }

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=bid,
        mission_objective="Fix failing tests",
        candidate_files=candidate_files,
        preview=False,
    )

    assert proposal.operations
    assert invocation.provider == "openai"
    assert "FILE: calc.py" in str(captured["prompt"])
    assert "FILE: tests/test_calc.py" in str(captured["prompt"])


def test_generate_edit_proposal_prioritizes_task_recovery_files_in_prompt() -> None:
    captured: dict[str, object] = {}
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                preview_request_timeout_seconds=11.0,
                proposal_request_timeout_seconds=23.0,
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del lane, request_timeout_seconds
            captured["prompt"] = prompt["user"]
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Apply a compact runtime fix.",
                    "operations": [
                        {
                            "type": "replace",
                            "path": "backend/app/services/webhook_service.py",
                            "target": "return {}",
                            "content": "return {'retry_delay_seconds': 45}",
                        }
                    ],
                    "notes": ["execution"],
                }
            )
            return ModelInvocationResult(
                content=content,
                provider="openai",
                model_id="gpt-5-mini",
                lane="proposal_gen.openai",
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    backend = DefaultStrategyBackend(_Router())
    task = TaskNode(
        task_id="T2",
        title="Fix webhook retry persistence",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="settings persist"),
        candidate_files=[
            "backend/tests/test_settings.py",
            "backend/app/routes/settings.py",
            "backend/app/services/webhook_service.py",
            "backend/app/models/settings.py",
        ],
    )
    bid = Bid(
        bid_id="b2",
        task_id="T2",
        role="Speed",
        provider="openai",
        lane="bid_fast.openai",
        model_id="gpt-5-mini",
        invocation_id="inv-2",
        variant_id="speed-base",
        strategy_family="speed-localized",
        strategy_summary="Fix webhook retry persistence in the backend settings flow.",
        exact_action="Inspect the settings route and service, then fix the persistence bug.",
        expected_benefit=0.8,
        utility=0.82,
        confidence=0.9,
        risk=0.18,
        cost=0.05,
        estimated_runtime_seconds=45,
        touched_files=["backend/app/models/settings.py", "backend/app/routes/settings.py"],
        rollback_plan="Revert the settings patch.",
    )
    candidate_files = {
        "backend/app/models/settings.py": "from pydantic import BaseModel\n",
        "backend/app/routes/settings.py": "from app.services.webhook_service import load_settings, save_settings\n",
        "backend/app/services/webhook_service.py": "def load_settings():\n    return {}\n",
        "backend/tests/test_settings.py": "def test_settings_round_trip_persists_retry_fields():\n    assert True\n",
        "frontend/src/pages/SettingsPage.jsx": "export default function SettingsPage() {\n  return null\n}\n",
        "backend/app/main.py": "from app.routes.settings import router\n",
    }

    proposal, invocation = backend.generate_edit_proposal(
        task=task,
        bid=bid,
        mission_objective="Fix webhook retry persistence",
        candidate_files=candidate_files,
        preview=False,
    )

    prompt = str(captured["prompt"])

    assert proposal.operations
    assert invocation.provider == "openai"
    assert "FILE: backend/tests/test_settings.py" in prompt
    assert "FILE: backend/app/services/webhook_service.py" in prompt
    assert prompt.index("FILE: backend/tests/test_settings.py") < prompt.index("FILE: backend/app/models/settings.py")
    assert "Do not request additional files or permissions" in prompt


def test_generate_edit_proposal_retries_with_compact_prompt_after_unusable_payload() -> None:
    captured_prompts: list[str] = []
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                preview_request_timeout_seconds=11.0,
                proposal_request_timeout_seconds=23.0,
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )
            self.invocations = 0

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del lane, request_timeout_seconds
            from arbiter.agents.backend import ModelInvocationResult

            self.invocations += 1
            captured_prompts.append(prompt["user"])
            if self.invocations == 1:
                content = json.dumps([{"id": "rs_1", "summary": [], "type": "reasoning"}])
            else:
                content = json.dumps(
                    {
                        "summary": "Apply a compact retry patch.",
                        "operations": [
                            {
                                "type": "replace",
                                "path": "calc.py",
                                "target": "return a - b",
                                "content": "return a + b",
                            }
                        ],
                        "notes": ["compact_retry"],
                    }
                )
            return ModelInvocationResult(
                content=content,
                provider="openai",
                model_id="gpt-5-mini",
                lane="proposal_gen.openai",
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    router = _Router()
    backend = DefaultStrategyBackend(router)

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="openai"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
        preview=False,
    )

    assert router.invocations == 2
    assert proposal.operations[0].path == "calc.py"
    assert invocation.provider == "openai"
    assert "Retry requirement:" in captured_prompts[-1]


def test_generate_edit_proposal_includes_governed_research_context_in_prompt() -> None:
    captured: dict[str, object] = {}
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del lane, request_timeout_seconds
            captured["prompt"] = prompt["user"]
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Apply the patch with governed context.",
                    "files": [{"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}],
                    "notes": ["research_informed"],
                }
            )
            return ModelInvocationResult(
                content=content,
                provider="openai",
                model_id="gpt-5-mini",
                lane="proposal_gen.openai",
                prompt_preview=prompt["user"],
                response_preview=content,
                token_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                cost_usage={"usd": 0.001},
            )

    backend = DefaultStrategyBackend(_Router())

    proposal, invocation = backend.generate_edit_proposal(
        task=_task(),
        bid=_bid(provider="openai"),
        mission_objective="Fix the LangGraph checkpoint issue",
        candidate_files=_candidate_files(),
        research_context={
            "summary": "Latest LangGraph docs recommend persisting checkpoints through the configured saver.",
            "queries": ["langgraph checkpoint saver best practices"],
            "source_urls": ["https://docs.langchain.com/langgraph"],
        },
    )

    assert proposal.files
    assert invocation.provider == "openai"
    assert "Governed external research:" in str(captured["prompt"])
    assert "langgraph checkpoint saver best practices" in str(captured["prompt"])


def test_parse_edit_proposal_accepts_compact_operations_payload() -> None:
    payload = json.dumps(
        {
            "summary": "Tighten config handling with a targeted replacement.",
            "operations": [
                {
                    "type": "replace",
                    "path": "arbiter/runtime/config.py",
                    "target": "return RuntimeConfig()\n",
                    "content": "return RuntimeConfig()  # validated runtime config\n",
                }
            ],
            "notes": ["compact_patch"],
        }
    )

    proposal = DefaultStrategyBackend._parse_edit_proposal(payload)

    assert proposal.has_changes
    assert proposal.operations == [
        EditOperation(
            type="replace",
            path="arbiter/runtime/config.py",
            target="return RuntimeConfig()\n",
            content="return RuntimeConfig()  # validated runtime config\n",
            occurrence=1,
        )
    ]
    assert proposal.affected_paths == ["arbiter/runtime/config.py"]


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


def test_parse_edit_proposal_accepts_fenced_json_with_string_notes() -> None:
    payload = """```json
{
  "summary": "Fix a small config issue.",
  "files": [
    {
      "path": "arbiter/settings.py",
      "content": "from arbiter.runtime.config import RuntimeConfig\\n"
    }
  ],
  "notes": "provider_generated"
}
```"""

    proposal = DefaultStrategyBackend._parse_edit_proposal(payload)

    assert proposal.summary == "Fix a small config issue."
    assert proposal.files[0].path == "arbiter/settings.py"
    assert proposal.notes == ["provider_generated"]


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

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del request_timeout_seconds
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
    assert "no executable edits" in str(invocations[-1]["error"]).lower()


def test_generate_edit_proposals_accepts_operation_only_patch_for_edit_tasks() -> None:
    lane_config = SimpleNamespace(provider="openai", model_id="gpt-5-mini", temperature=0.0, max_tokens=2048)

    class _Router:
        def __init__(self) -> None:
            self.replay = SimpleNamespace(mode="off")
            self.config = SimpleNamespace(
                enabled_providers=["openai"],
                default_provider="openai",
                model_lanes={"proposal_gen": lane_config, "proposal_gen.openai": lane_config},
            )

        def invoke(
            self,
            lane: str,
            prompt: dict[str, str],
            *,
            request_timeout_seconds: float | None = None,
        ):
            del request_timeout_seconds
            from arbiter.agents.backend import ModelInvocationResult

            content = json.dumps(
                {
                    "summary": "Patch the buggy return with a compact operation.",
                    "operations": [
                        {
                            "type": "replace",
                            "path": "calc.py",
                            "target": "return a - b",
                            "content": "return a + b",
                        }
                    ],
                    "notes": ["operation_only"],
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

    candidates = backend.generate_edit_proposals(
        task=_task(),
        bid=_bid(provider="openai"),
        mission_objective="Fix failing tests",
        candidate_files=_candidate_files(),
    )

    assert len(candidates) == 1
    assert candidates[0].proposal.files == []
    assert candidates[0].proposal.operations[0].path == "calc.py"
