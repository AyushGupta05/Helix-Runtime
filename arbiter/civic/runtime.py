from __future__ import annotations

import asyncio
import inspect
import re
import threading
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from arbiter.core.contracts import (
    ActionOutcome,
    ActionStatus,
    CivicAuditRecord,
    CivicCapability,
    CivicConnectionStatus,
    GovernedActionRecord,
    GovernedBidEnvelope,
    PolicyDecision,
    PolicyState,
    RepoSnapshot,
    SkillHealth,
    utc_now,
)
from arbiter.runtime.config import RuntimeConfig

if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient


_CANONICAL_ACTION_ALIASES = {
    "fetch_ci_status": [
        "fetch_ci_status",
        "github-remote-pull_request_read",
        "pull_request_read",
        "github-remote-get_commit",
        "get_commit",
        "github-remote-list_pull_requests",
        "list_pull_requests",
        "github-remote-search_pull_requests",
        "search_pull_requests",
    ],
    "open_pr_metadata": [
        "open_pr_metadata",
        "github-remote-pull_request_read",
        "pull_request_read",
        "github-remote-list_pull_requests",
        "list_pull_requests",
        "github-remote-search_pull_requests",
        "search_pull_requests",
    ],
    "open_issue_metadata": [
        "open_issue_metadata",
        "github-remote-issue_read",
        "issue_read",
        "github-remote-search_issues",
    ],
    "fetch_discussion_context": [
        "fetch_discussion_context",
        "github-remote-pull_request_read",
        "pull_request_read",
        "github-remote-issue_read",
        "issue_read",
    ],
    "knowledge_retrieval": [
        "knowledge",
        "retrieve",
    ],
}

_ACTION_TOOL_PREFERENCES = {
    "fetch_ci_status": [
        "github-remote-pull_request_read",
        "github-remote-get_commit",
        "pull_request_read",
        "get_commit",
    ],
    "open_pr_metadata": [
        "github-remote-pull_request_read",
        "pull_request_read",
    ],
    "open_issue_metadata": [
        "github-remote-issue_read",
        "issue_read",
    ],
    "fetch_discussion_context": [
        "github-remote-pull_request_read",
        "github-remote-issue_read",
        "pull_request_read",
        "issue_read",
    ],
    "knowledge_retrieval": [
        "knowledge",
        "retrieve",
    ],
}

_ACTION_DOMAIN = {
    "fetch_ci_status": "github",
    "open_pr_metadata": "github",
    "open_issue_metadata": "github",
    "fetch_discussion_context": "github",
    "knowledge_retrieval": "knowledge",
}


def _load_multi_server_mcp_client():
    # `langchain_core` still imports `pydantic.v1` during module import, which emits
    # a Python 3.14 warning even though Arbiter uses Pydantic v2 models. Keep the
    # import local and suppress that one upstream warning until LangChain removes it.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.",
            category=UserWarning,
        )
        from langchain_mcp_adapters.client import MultiServerMCPClient

    return MultiServerMCPClient


def _resolve_maybe_awaitable(value: Any, *, timeout_seconds: float | None = None) -> Any:
    if not inspect.isawaitable(value):
        return value

    async def _await_value():
        if timeout_seconds and timeout_seconds > 0:
            return await asyncio.wait_for(value, timeout=timeout_seconds)
        return await value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_value())
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(_await_value())
        except BaseException as exc:  # pragma: no cover - surfaced to caller
            error["value"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join((timeout_seconds + 1.0) if timeout_seconds and timeout_seconds > 0 else None)
    if worker.is_alive():
        raise TimeoutError("Timed out while awaiting Civic tool response.")
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, str):
        return tool
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    metadata = getattr(tool, "metadata", None)
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return metadata["name"]
    return str(tool)


def _normalize_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return {"result": value}


def _auth_challenge_details(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    texts = [
        str(item.get("text", "")).strip()
        for item in value
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    combined = " ".join(texts).lower()
    if "authorization required" not in combined:
        return None
    url = next((text for text in texts if text.startswith("http")), None)
    if url is None:
        for text in texts:
            match = re.search(r"https?://\S+", text)
            if match:
                url = match.group(0)
                break
    return {
        "message": next((text for text in texts if "authorization required" in text.lower()), "User authorization required."),
        "url": url,
    }


@dataclass
class GovernedActionResult:
    success: bool
    result: dict[str, Any]
    record: GovernedActionRecord


class CivicRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._client_instance: MultiServerMCPClient | None = None
        self._last_connection: CivicConnectionStatus | None = None
        self._last_capabilities: list[CivicCapability] = []
        self._last_skills: list[str] = []
        self._last_skill_health: dict[str, SkillHealth] = {}
        self._tool_cache: dict[str, Any] | None = None

    def available(self) -> bool:
        return bool(self.config.civic_url and self.config.civic_token)

    def client(self) -> MultiServerMCPClient:
        if not self.available():
            raise ValueError("Civic is not configured.")
        if self._client_instance is None:
            MultiServerMCPClient = _load_multi_server_mcp_client()
            self._client_instance = MultiServerMCPClient(
                {
                    "civic": {
                        "url": self.config.civic_url,
                        "transport": "streamable_http",
                        "headers": {"Authorization": f"Bearer {self.config.civic_token}"},
                    }
                }
            )
        return self._client_instance

    @staticmethod
    def _tool_matches(tool_name: str, requested_name: str) -> bool:
        lowered = tool_name.lower()
        aliases = [requested_name.lower(), *[alias.lower() for alias in _CANONICAL_ACTION_ALIASES.get(requested_name, [])]]
        for requested in aliases:
            if lowered == requested or lowered.endswith(requested):
                return True
        return False

    @staticmethod
    def _tool_is_github(tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered.startswith("github-") or "github" in lowered

    @staticmethod
    def _tool_is_knowledge(tool_name: str) -> bool:
        lowered = tool_name.lower()
        return "knowledge" in lowered or "retrieve" in lowered

    @staticmethod
    def _split_repo_slug(repo_slug: str | None) -> tuple[str | None, str | None]:
        if not repo_slug or "/" not in repo_slug:
            return None, None
        owner, repo = repo_slug.split("/", 1)
        return owner, repo

    def _discover_tools(self, *, force: bool = False) -> dict[str, Any]:
        if self._tool_cache is not None and not force:
            return self._tool_cache
        tools: dict[str, Any] = {}
        client = self.client()
        raw_tools: Any = None
        for method_name in ("get_tools", "list_tools"):
            if hasattr(client, method_name):
                raw_tools = _resolve_maybe_awaitable(
                    getattr(client, method_name)(),
                    timeout_seconds=self.config.civic_connection_timeout_seconds,
                )
                break
        if raw_tools is None and hasattr(client, "tools"):
            raw_tools = getattr(client, "tools")
        if raw_tools is None:
            self._tool_cache = {}
            return self._tool_cache
        if isinstance(raw_tools, dict):
            iterable = raw_tools.values()
        else:
            iterable = raw_tools
        for tool in iterable:
            tools[_tool_name(tool)] = tool
        self._tool_cache = tools
        return tools

    def _matched_tool_names(self, requested_tools: list[str], discovered: dict[str, Any]) -> dict[str, str]:
        matched: dict[str, str] = {}
        for required in requested_tools:
            tool_name = self._resolve_tool_name(required, discovered)
            if tool_name is not None:
                matched[required] = tool_name
        return matched

    def _resolve_tool_name(
        self,
        action_type: str,
        discovered: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> str | None:
        payload = payload or {}
        ordered_names = list(discovered)
        domain = _ACTION_DOMAIN.get(action_type)

        def _domain_matches(tool_name: str) -> bool:
            if domain == "github":
                return self._tool_is_github(tool_name)
            if domain == "knowledge":
                return self._tool_is_knowledge(tool_name)
            return True

        for preferred in _ACTION_TOOL_PREFERENCES.get(action_type, []):
            for tool_name in ordered_names:
                if not _domain_matches(tool_name):
                    continue
                if tool_name.lower() == preferred.lower() or self._tool_matches(tool_name, preferred):
                    if action_type == "fetch_ci_status" and "pull_request_read" in tool_name and payload.get("pr_number") is None:
                        continue
                    return tool_name
        for tool_name in ordered_names:
            if not _domain_matches(tool_name):
                continue
            if self._tool_matches(tool_name, action_type):
                if action_type == "fetch_ci_status" and "pull_request_read" in tool_name and payload.get("pr_number") is None:
                    continue
                return tool_name
        return None

    def _action_available(
        self,
        action_type: str,
        discovered: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> bool:
        return self._resolve_tool_name(action_type, discovered, payload) is not None

    def check_connection(self, *, force: bool = False) -> CivicConnectionStatus:
        if self._last_connection is not None and not force:
            return self._last_connection
        status = CivicConnectionStatus(
            configured=self.available(),
            required=self.config.civic_required,
            base_url=self.config.civic_url,
            toolkit_id=self.config.civic_toolkit_id,
            required_tools=list(self.config.civic_required_tools),
            last_checked_at=utc_now(),
        )
        if not status.configured:
            status.status = "unconfigured"
            status.message = "Civic URL/token are not configured."
            self._last_connection = status
            return status
        try:
            discovered = self._discover_tools(force=force)
            matched = self._matched_tool_names(status.required_tools, discovered)
            status.connected = True
            status.available = True
            status.discovered_tool_count = len(discovered)
            status.missing_tools = [tool for tool in status.required_tools if tool not in matched]
            if status.missing_tools:
                status.status = "degraded"
                status.message = f"Connected, but missing required Civic tools: {', '.join(status.missing_tools)}."
            else:
                status.status = "connected"
                status.message = "Connected to Civic and discovered required governed tools."
        except Exception as exc:
            status.connected = False
            status.available = False
            status.status = "unavailable"
            status.errors.append(str(exc))
            status.message = "Civic transport is unavailable."
        self._last_connection = status
        return status

    def discover_capabilities(self, *, force: bool = False) -> list[CivicCapability]:
        if self._last_capabilities and not force:
            return self._last_capabilities
        connection = self.check_connection(force=force)
        if not connection.connected:
            self._last_capabilities = []
            return self._last_capabilities
        tools = self._discover_tools(force=force)
        tool_names = list(tools)
        capabilities: list[CivicCapability] = []
        github_tools = [
            name
            for name in tool_names
            if self._tool_is_github(name)
            and any(self._action_available(action, {name: tools[name]}) for action in ("fetch_ci_status", "open_pr_metadata", "open_issue_metadata", "fetch_discussion_context"))
        ]
        if github_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="github_read",
                    display_name="GitHub Read Context",
                    read_only=True,
                    tools=github_tools,
                    metadata={"domain": "github"},
                )
            )
        github_write_tools = [
            name
            for name in tool_names
            if self._tool_is_github(name)
            and any(
                self._tool_matches(name, action)
                for action in ("github-remote-create_branch", "github-remote-push_files", "github-remote-create_pull_request")
            )
        ]
        if github_write_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="github_write",
                    display_name="GitHub Branch and Pull Request Delivery",
                    read_only=False,
                    tools=github_write_tools,
                    metadata={"domain": "github"},
                )
            )
        knowledge_tools = [name for name in tool_names if self._tool_is_knowledge(name)]
        if knowledge_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="knowledge_read",
                    display_name="Knowledge Retrieval",
                    read_only=True,
                    tools=knowledge_tools,
                    metadata={"domain": "knowledge"},
                )
            )
        guardrail_tools = [name for name in tool_names if "guardrail" in name.lower()]
        passthrough_tools = [name for name in tool_names if "pass" in name.lower() and "proxy" in name.lower()]
        bodyguard_tools = [name for name in tool_names if "bodyguard" in name.lower()]
        if guardrail_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="guardrail_proxy",
                    display_name="Guardrail Proxy",
                    read_only=True,
                    tools=guardrail_tools,
                    metadata={"domain": "proxy"},
                )
            )
        if passthrough_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="pass_through_proxy",
                    display_name="Pass-through Proxy",
                    read_only=True,
                    tools=passthrough_tools,
                    metadata={"domain": "proxy"},
                )
            )
        if bodyguard_tools:
            capabilities.append(
                CivicCapability(
                    capability_id="bodyguard",
                    display_name="Bodyguard",
                    read_only=True,
                    tools=bodyguard_tools,
                    metadata={"domain": "safety"},
                )
            )
        self._last_capabilities = capabilities
        return capabilities

    def derive_skills(
        self,
        repo_snapshot: RepoSnapshot | None = None,
        *,
        capabilities: list[CivicCapability] | None = None,
    ) -> tuple[list[str], dict[str, SkillHealth]]:
        capabilities = capabilities if capabilities is not None else self.discover_capabilities()
        capability_tools = {capability.capability_id: capability.tools for capability in capabilities}
        tool_names = {tool for tools in capability_tools.values() for tool in tools}
        available_skills: list[str] = []
        skill_health: dict[str, SkillHealth] = {}

        github_required = ["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"]
        github_available_tools = sorted(
            tool_name
            for tool_name in tool_names
            if self._tool_is_github(tool_name)
            and any(self._tool_matches(tool_name, required) for required in github_required)
        )
        github_discovered = {tool_name: tool_name for tool_name in github_available_tools}
        github_available = (
            repo_snapshot is not None
            and repo_snapshot.remote_provider == "github"
            and all(self._action_available(required, github_discovered) for required in github_required)
        )
        skill_health["github_context"] = SkillHealth(
            skill_id="github_context",
            status="available" if github_available else "inactive",
            available=github_available,
            required_tools=github_required,
            available_tools=github_available_tools,
            reason=None if github_available else "GitHub remote or governed read tools are unavailable.",
            last_checked_at=utc_now(),
        )
        if github_available:
            available_skills.append("github_context")

        github_publish_required = [
            "github-remote-create_branch",
            "github-remote-push_files",
            "github-remote-create_pull_request",
        ]
        github_publish_available_tools = sorted(
            tool_name
            for tool_name in tool_names
            if self._tool_is_github(tool_name)
            and any(self._tool_matches(tool_name, required) for required in github_publish_required)
        )
        github_publish_discovered = {tool_name: tool_name for tool_name in github_publish_available_tools}
        github_publish_available = (
            repo_snapshot is not None
            and repo_snapshot.remote_provider == "github"
            and all(self._action_available(required, github_publish_discovered) for required in github_publish_required)
        )
        skill_health["github_publish"] = SkillHealth(
            skill_id="github_publish",
            status="available" if github_publish_available else "inactive",
            available=github_publish_available,
            required_tools=github_publish_required,
            available_tools=github_publish_available_tools,
            reason=None if github_publish_available else "GitHub publication tools are unavailable.",
            last_checked_at=utc_now(),
        )
        if github_publish_available:
            available_skills.append("github_publish")

        knowledge_available_tools = sorted(tool_name for tool_name in tool_names if self._tool_is_knowledge(tool_name))
        knowledge_available = bool(knowledge_available_tools)
        skill_health["knowledge_context"] = SkillHealth(
            skill_id="knowledge_context",
            status="available" if knowledge_available else "inactive",
            available=knowledge_available,
            required_tools=["knowledge_retrieval"],
            available_tools=knowledge_available_tools,
            reason=None if knowledge_available else "Civic knowledge retrieval tools were not discovered.",
            last_checked_at=utc_now(),
        )
        if knowledge_available:
            available_skills.append("knowledge_context")

        trusted_stack = {
            "guardrail": any("guardrail" in tool.lower() for tool in tool_names),
            "proxy": any("proxy" in tool.lower() for tool in tool_names),
            "bodyguard": any("bodyguard" in tool.lower() for tool in tool_names),
        }
        trusted_available = all(trusted_stack.values())
        skill_health["trusted_external_context"] = SkillHealth(
            skill_id="trusted_external_context",
            status="available" if trusted_available else "inactive",
            available=trusted_available,
            required_tools=["guardrail_proxy", "pass_through_proxy", "bodyguard"],
            available_tools=sorted(tool_names),
            reason=None if trusted_available else "Guardrail Proxy, Pass-through Proxy, and Bodyguard are not all active.",
            last_checked_at=utc_now(),
        )
        if trusted_available:
            available_skills.append("trusted_external_context")

        self._last_skills = available_skills
        self._last_skill_health = skill_health
        return available_skills, skill_health

    def refresh_capability_state(
        self,
        repo_snapshot: RepoSnapshot | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        connection = self.check_connection(force=force)
        try:
            capabilities = self.discover_capabilities(force=force)
            skills, skill_health = self.derive_skills(repo_snapshot, capabilities=capabilities)
        except Exception as exc:
            connection = CivicConnectionStatus.model_validate(connection.model_dump(mode="json"))
            connection.connected = False
            connection.available = False
            connection.status = "unavailable"
            connection.message = "Civic capability discovery failed."
            connection.errors = [*connection.errors, str(exc)]
            connection.last_checked_at = utc_now()
            self._last_connection = connection
            self._last_capabilities = []
            self._last_skills = []
            self._last_skill_health = {}
            capabilities = []
            skills = []
            skill_health = {}
        return {
            "connection": connection,
            "capabilities": capabilities,
            "available_skills": skills,
            "skill_health": skill_health,
        }

    def preflight_bid(
        self,
        *,
        mission_id: str,
        task_id: str,
        bid_id: str,
        required_skills: list[str],
        optional_skills: list[str],
        governed_action_plan: list[str],
        estimated_runtime_seconds: float | None = None,
        token_budget: int | None = None,
        repo_snapshot: RepoSnapshot | None = None,
    ) -> GovernedBidEnvelope:
        state = self.refresh_capability_state(repo_snapshot)
        connection: CivicConnectionStatus = state["connection"]
        available_skills: list[str] = state["available_skills"]
        capabilities: list[CivicCapability] = state["capabilities"]
        available_tools = {tool for capability in capabilities for tool in capability.tools}
        missing_skills = [skill for skill in required_skills if skill not in available_skills]
        discovered = {tool_name: object() for tool_name in available_tools}
        missing_actions = [action for action in governed_action_plan if not self._action_available(action, discovered)]
        needs_governed_capability = bool(required_skills or optional_skills or governed_action_plan)
        blocked = bool(missing_skills or missing_actions or (needs_governed_capability and not connection.connected))
        reasoning: list[str] = []
        constraints = ["read_write_scope:read_only"]
        constraints.append("civic_connection:connected" if connection.connected else "civic_connection:unavailable")
        if missing_skills:
            reasoning.append(f"Missing required skills: {', '.join(missing_skills)}.")
            constraints.append(f"missing_skills:{','.join(missing_skills)}")
        if missing_actions:
            reasoning.append(f"Missing governed actions: {', '.join(missing_actions)}.")
            constraints.append(f"missing_governed_actions:{','.join(missing_actions)}")
        if needs_governed_capability and not connection.connected:
            reasoning.append("Civic connection is unavailable for governed capabilities.")
        if not reasoning:
            reasoning.append("Bid is admissible under the current Civic capability surface.")
        matched_actions = [
            self._resolve_tool_name(action, discovered) or action
            for action in governed_action_plan
            if self._action_available(action, discovered)
        ]
        policy_decision = "block" if blocked else "allow"
        return GovernedBidEnvelope(
            envelope_id=uuid4().hex,
            mission_id=mission_id,
            task_id=task_id,
            bid_id=bid_id,
            status="blocked" if blocked else "approved",
            allowed_skills=[skill for skill in [*required_skills, *optional_skills] if skill in available_skills],
            allowed_actions=matched_actions,
            toolkit_id=self.config.civic_toolkit_id,
            read_only=True,
            read_write_scope="read_only",
            runtime_budget_seconds=estimated_runtime_seconds,
            token_budget=token_budget,
            constraints=constraints,
            policy_state=PolicyState.BLOCKED if blocked else PolicyState.CLEAR,
            policy_decision=policy_decision,
            reasoning=reasoning,
            audit_id=uuid4().hex,
        )

    def preflight_action(
        self,
        *,
        mission_id: str,
        task_id: str | None,
        bid_id: str | None,
        action_type: str,
        payload: dict[str, Any],
        skill_id: str | None = None,
        envelope: GovernedBidEnvelope | None = None,
    ) -> GovernedActionRecord:
        connection = self.check_connection()
        tools = self._discover_tools() if connection.connected else {}
        matched_tool = self._resolve_tool_name(action_type, tools, payload)
        allowed = connection.connected and matched_tool is not None
        reasoning: list[str] = []
        if envelope and envelope.status != "approved":
            allowed = False
            reasoning.append(f"Envelope {envelope.envelope_id} is {envelope.status}.")
        if not connection.connected:
            allowed = False
            reasoning.append("Civic connection is unavailable.")
        if matched_tool is None:
            allowed = False
            reasoning.append(f"No governed tool matched action `{action_type}`.")
        if not reasoning and allowed:
            reasoning.append("Governed action passed Civic preflight.")
        return GovernedActionRecord(
            action_id=uuid4().hex,
            mission_id=mission_id,
            task_id=task_id,
            bid_id=bid_id,
            envelope_id=envelope.envelope_id if envelope else None,
            skill_id=skill_id,
            action_type=action_type,
            tool_name=matched_tool or action_type,
            status="preflight_allowed" if allowed else "preflight_blocked",
            allowed=allowed,
            policy_state=PolicyState.CLEAR if allowed else PolicyState.BLOCKED,
            reasoning=reasoning,
            input_payload=payload,
        )

    def _invoke_tool(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        tools = self._discover_tools()
        tool_name = self._resolve_tool_name(action_type, tools, payload)
        tool = tools.get(tool_name) if tool_name else None
        if tool is None:
            raise ValueError(f"No Civic tool matched action `{action_type}`.")
        normalized_payload = self._normalize_tool_payload(tool_name, action_type, payload)
        last_error: Exception | None = None
        for method_name in ("ainvoke", "invoke", "run"):
            if hasattr(tool, method_name):
                try:
                    result = _resolve_maybe_awaitable(
                        getattr(tool, method_name)(normalized_payload),
                        timeout_seconds=self.config.civic_connection_timeout_seconds,
                    )
                except NotImplementedError as exc:
                    last_error = exc
                    continue
                challenge = _auth_challenge_details(result)
                if challenge is not None:
                    detail = challenge["message"]
                    if challenge.get("url"):
                        detail = f"{detail} Authorization URL: {challenge['url']}"
                    raise PermissionError(detail)
                return _normalize_payload(result)
        if callable(tool):
            result = _resolve_maybe_awaitable(
                tool(normalized_payload),
                timeout_seconds=self.config.civic_connection_timeout_seconds,
            )
            challenge = _auth_challenge_details(result)
            if challenge is not None:
                detail = challenge["message"]
                if challenge.get("url"):
                    detail = f"{detail} Authorization URL: {challenge['url']}"
                raise PermissionError(detail)
            return _normalize_payload(result)
        if last_error is not None:
            raise last_error
        raise ValueError(f"Civic tool `{_tool_name(tool)}` is not invokable.")

    def _normalize_tool_payload(self, tool_name: str | None, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not tool_name:
            return payload
        owner, repo = self._split_repo_slug(payload.get("repo"))
        if "pull_request_read" in tool_name:
            pull_number = payload.get("pr_number") or payload.get("pullNumber")
            if action_type == "fetch_ci_status" and pull_number is not None:
                return {"method": "get_status", "owner": owner, "repo": repo, "pullNumber": pull_number}
            if action_type == "open_pr_metadata" and pull_number is not None:
                return {"method": "get", "owner": owner, "repo": repo, "pullNumber": pull_number}
            if action_type == "fetch_discussion_context" and pull_number is not None:
                return {"method": "get_comments", "owner": owner, "repo": repo, "pullNumber": pull_number}
        if "issue_read" in tool_name:
            issue_number = payload.get("issue_number")
            if issue_number is None:
                return payload
            if action_type == "open_issue_metadata":
                return {"method": "get", "owner": owner, "repo": repo, "issue_number": issue_number}
            if action_type == "fetch_discussion_context":
                return {"method": "get_comments", "owner": owner, "repo": repo, "issue_number": issue_number}
        if "get_commit" in tool_name and action_type == "fetch_ci_status":
            sha = payload.get("sha") or payload.get("tracking_branch") or payload.get("branch")
            if sha:
                return {"owner": owner, "repo": repo, "sha": sha, "include_diff": False}
        return payload

    def execute_governed_action(
        self,
        *,
        mission_id: str,
        task_id: str | None,
        bid_id: str | None,
        action_type: str,
        payload: dict[str, Any],
        skill_id: str | None = None,
        envelope: GovernedBidEnvelope | None = None,
        executor: Callable[[], dict[str, Any]] | None = None,
    ) -> GovernedActionResult:
        record = self.preflight_action(
            mission_id=mission_id,
            task_id=task_id,
            bid_id=bid_id,
            action_type=action_type,
            payload=payload,
            skill_id=skill_id,
            envelope=envelope,
        )
        if not record.allowed:
            return GovernedActionResult(success=False, result={"blocked": True, "reasons": record.reasoning}, record=record)
        try:
            result = executor() if executor is not None else self._invoke_tool(action_type, payload)
            record.status = "executed"
            record.output_payload = _normalize_payload(result)
            return GovernedActionResult(success=True, result=record.output_payload, record=record)
        except PermissionError as exc:
            record.status = "failed"
            record.policy_state = PolicyState.RESTRICTED
            message = str(exc)
            record.reasoning.append(f"authorization_required: {message}")
            output = {"error": message, "authorization_required": True}
            if "Authorization URL:" in message:
                output["authorization_url"] = message.split("Authorization URL:", 1)[1].strip()
            record.output_payload = output
            return GovernedActionResult(success=False, result=record.output_payload, record=record)
        except Exception as exc:
            record.status = "failed"
            record.reasoning.append(f"execution_failed: {exc}")
            record.output_payload = {"error": str(exc)}
            return GovernedActionResult(success=False, result=record.output_payload, record=record)

    def record_audit(self, record: GovernedActionRecord) -> CivicAuditRecord:
        if record.status in {"preflight_blocked", "revoked", "unavailable"}:
            status = ActionStatus.BLOCKED
        elif record.status == "executed":
            status = ActionStatus.EXECUTED
        else:
            status = ActionStatus.APPROVED
        return CivicAuditRecord(
            audit_id=record.audit_id or uuid4().hex,
            mission_id=record.mission_id,
            task_id=record.task_id or "mission",
            action_type=record.action_type,
            status=status,
            policy_state=record.policy_state,
            reasons=list(record.reasoning),
            payload={
                "skill_id": record.skill_id,
                "envelope_id": record.envelope_id,
                "tool_name": record.tool_name,
                "input_payload": record.input_payload,
                "output_payload": record.output_payload,
            },
        )

    def authorize_and_execute(
        self,
        mission_id: str,
        task_id: str,
        action_type: str,
        decision: PolicyDecision,
        payload: dict,
        executor: Callable[[], dict],
    ) -> ActionOutcome:
        audit = CivicAuditRecord(
            audit_id=uuid4().hex,
            mission_id=mission_id,
            task_id=task_id,
            action_type=action_type,
            status=ActionStatus.BLOCKED if not decision.allowed else ActionStatus.APPROVED,
            policy_state=decision.state,
            reasons=list(decision.reasons),
            payload=payload,
        )
        if not decision.allowed:
            return ActionOutcome(success=False, result={"blocked": True, "reasons": decision.reasons}, audit=audit)
        if self.available():
            connection = self.check_connection(force=True)
            if not connection.connected:
                audit.status = ActionStatus.BLOCKED
                audit.policy_state = PolicyState.RESTRICTED
                audit.reasons.append("civic_transport_unavailable")
                return ActionOutcome(
                    success=False,
                    result={"blocked": True, "reasons": list(audit.reasons)},
                    audit=audit,
                )
        try:
            result = executor()
        except Exception as exc:
            audit.reasons.append(f"execution_failed: {exc}")
            return ActionOutcome(success=False, result={"blocked": False, "error": str(exc)}, audit=audit)
        audit.status = ActionStatus.EXECUTED
        return ActionOutcome(success=True, result=result, audit=audit)
