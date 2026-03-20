from __future__ import annotations

from typing import Callable
from uuid import uuid4

from langchain_mcp_adapters.client import MultiServerMCPClient

from arbiter.core.contracts import ActionOutcome, ActionStatus, CivicAuditRecord, PolicyDecision, PolicyState
from arbiter.runtime.config import RuntimeConfig


class CivicRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return bool(self.config.civic_url and self.config.civic_token)

    def client(self) -> MultiServerMCPClient:
        if not self.available():
            raise ValueError("Civic is not configured.")
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
