from __future__ import annotations

from typing import Any

import pytest

from arbiter.civic.runtime import CivicRuntime
from arbiter.core.contracts import CapabilitySet, CivicCapability, CivicConnectionStatus, PolicyState, RepoSnapshot
from arbiter.runtime.config import RuntimeConfig


def test_refresh_capability_state_fails_closed_when_discovery_raises() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )

    runtime.check_connection = lambda force=False: CivicConnectionStatus(  # type: ignore[method-assign]
        configured=True,
        connected=True,
        available=True,
        required=False,
        status="connected",
        base_url="https://civic.example",
        toolkit_id="toolkit-demo",
    )
    runtime.discover_capabilities = lambda force=False: (_ for _ in ()).throw(PermissionError("403 Forbidden"))  # type: ignore[method-assign]

    state = runtime.refresh_capability_state(force=True)

    assert state["connection"].status == "unavailable"
    assert state["connection"].connected is False
    assert "403 Forbidden" in " ".join(state["connection"].errors)
    assert state["capabilities"] == []
    assert state["available_skills"] == []
    assert state["skill_health"] == {}


class DummyAsyncTool:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.last_payload: dict[str, Any] | None = None

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("sync invocation is not supported")

    async def ainvoke(self, payload: dict[str, Any]) -> Any:
        self.last_payload = payload
        return self.result


def test_discover_capabilities_filters_non_github_issue_tools() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    runtime.check_connection = lambda force=False: CivicConnectionStatus(  # type: ignore[method-assign]
        configured=True,
        connected=True,
        available=True,
        required=False,
        status="connected",
        base_url="https://civic.example",
        toolkit_id="toolkit-demo",
    )
    runtime._discover_tools = lambda force=False: {  # type: ignore[method-assign]
        "github-remote-get_commit": object(),
        "github-remote-pull_request_read": object(),
        "linear-list_issues": object(),
    }

    capabilities = runtime.discover_capabilities(force=True)

    github = next(capability for capability in capabilities if capability.capability_id == "github_read")
    assert "github-remote-get_commit" in github.tools
    assert "github-remote-pull_request_read" in github.tools
    assert "linear-list_issues" not in github.tools


def test_invoke_tool_uses_commit_reader_for_branch_ci_status() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    commit_tool = DummyAsyncTool({"status": "success"})
    pr_tool = DummyAsyncTool({"status": "unused"})
    runtime._tool_cache = {
        "github-remote-pull_request_read": pr_tool,
        "github-remote-get_commit": commit_tool,
    }

    result = runtime._invoke_tool(
        "fetch_ci_status",
        {"repo": "octo/example", "branch": "feature/fix"},
    )

    assert result == {"status": "success"}
    assert commit_tool.last_payload == {
        "owner": "octo",
        "repo": "example",
        "sha": "feature/fix",
        "include_diff": False,
    }
    assert pr_tool.last_payload is None


def test_invoke_tool_uses_pr_status_when_pr_number_is_available() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    commit_tool = DummyAsyncTool({"status": "unused"})
    pr_tool = DummyAsyncTool({"status": "success"})
    runtime._tool_cache = {
        "github-remote-pull_request_read": pr_tool,
        "github-remote-get_commit": commit_tool,
    }

    result = runtime._invoke_tool(
        "fetch_ci_status",
        {"repo": "octo/example", "pr_number": 42, "branch": "feature/fix"},
    )

    assert result == {"status": "success"}
    assert pr_tool.last_payload == {
        "method": "get_status",
        "owner": "octo",
        "repo": "example",
        "pullNumber": 42,
    }
    assert commit_tool.last_payload is None


def test_invoke_tool_raises_permission_error_for_auth_challenge() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    runtime._tool_cache = {
        "github-remote-get_commit": DummyAsyncTool(
            [
                {
                    "type": "text",
                    "text": "User authorization required. Please present the user with this link: https://app.civic.com/authz/demo",
                }
            ]
        )
    }

    with pytest.raises(PermissionError, match="Authorization URL"):
        runtime._invoke_tool(
            "fetch_ci_status",
            {"repo": "octo/example", "branch": "main"},
        )


def test_derive_skills_requires_all_github_actions() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    repo_snapshot = RepoSnapshot(
        repo_path="C:/repo",
        branch="feature/fix",
        head_commit="abc123",
        dirty=False,
        changed_files=[],
        untracked_files=[],
        tree_summary=["src"],
        dependency_files=[],
        complexity_hotspots=[],
        failure_signals=[],
        capabilities=CapabilitySet(runtime="python"),
        initial_test_results=[],
        initial_lint_results=[],
        initial_static_results=[],
        tracking_branch="origin/feature/fix",
        remotes={"origin": "https://github.com/octo/example.git"},
        default_remote="origin",
        remote_provider="github",
        remote_slug="octo/example",
        objective_hints={},
    )
    capabilities = [
        CivicCapability(
            capability_id="github_read",
            display_name="GitHub Read Context",
            tools=["github-remote-get_commit", "github-remote-pull_request_read"],
        )
    ]

    available_skills, skill_health = runtime.derive_skills(repo_snapshot, capabilities=capabilities)

    assert "github_context" not in available_skills
    assert skill_health["github_context"].available is False


def test_execute_governed_action_returns_authorization_metadata() -> None:
    runtime = CivicRuntime(
        RuntimeConfig(
            civic_url="https://civic.example",
            civic_token="token",
        )
    )
    runtime.check_connection = lambda force=False: CivicConnectionStatus(  # type: ignore[method-assign]
        configured=True,
        connected=True,
        available=True,
        required=False,
        status="connected",
        base_url="https://civic.example",
        toolkit_id="toolkit-demo",
    )
    runtime._tool_cache = {
        "github-remote-pull_request_read": DummyAsyncTool(
            [
                {
                    "type": "text",
                    "text": "User authorization required. Please present the user with this link: https://app.civic.com/authz/demo",
                },
                {"type": "text", "text": "https://app.civic.com/authz/demo"},
            ]
        )
    }

    result = runtime.execute_governed_action(
        mission_id="mission-1",
        task_id="collect",
        bid_id=None,
        action_type="open_pr_metadata",
        payload={"repo": "octo/example", "pr_number": 42},
        skill_id="github_context",
    )

    assert result.success is False
    assert result.record.status == "failed"
    assert result.record.policy_state == PolicyState.RESTRICTED
    assert result.result["authorization_required"] is True
    assert result.result["authorization_url"] == "https://app.civic.com/authz/demo"
