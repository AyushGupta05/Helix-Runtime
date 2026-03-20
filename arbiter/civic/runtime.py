from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from arbiter.core.contracts import ActionOutcome, ActionStatus, CivicAuditRecord, PolicyDecision, PolicyState
from arbiter.runtime.config import RuntimeConfig

if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient


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


class CivicRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return bool(self.config.civic_url and self.config.civic_token)

    def client(self) -> MultiServerMCPClient:
        if not self.available():
            raise ValueError("Civic is not configured.")
        MultiServerMCPClient = _load_multi_server_mcp_client()
        return MultiServerMCPClient(
            {
                "civic": {
                    "url": self.config.civic_url,
                    "transport": "streamable_http",
                    "headers": {"Authorization": f"Bearer {self.config.civic_token}"},
                }
            }
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
        result = executor()
        audit.status = ActionStatus.EXECUTED
        if self.available():
            try:
                # The remote Civic gateway is the trust boundary when configured.
                # For V1 local execution still happens here, but every privileged action
                # gets a Civic-shaped audit envelope regardless of transport.
                _ = self.client()
            except Exception:
                audit.policy_state = PolicyState.RESTRICTED
                audit.reasons.append("civic_transport_unavailable")
        return ActionOutcome(success=True, result=result, audit=audit)
