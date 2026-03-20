from __future__ import annotations

from arbiter.core.contracts import Bid, FailureContext


class RecoveryEngine:
    def should_promote_standby(self, standby: Bid | None, failure: FailureContext) -> bool:
        if standby is None:
            return False
        if standby.strategy_family == failure.strategy_family:
            return False
        if standby.rejection_reason:
            return False
        if failure.failure_type in standby.promotion_hints:
            return True
        if failure.failure_type == "validation_failure" and standby.risk <= 0.55:
            return True
        return False

    def family_penalty(self, failure: FailureContext) -> tuple[str | None, float]:
        if not failure.strategy_family:
            return None, 0.0
        penalty = 0.35 if failure.failure_type == "policy_block" else 0.20 if failure.failure_type == "validation_failure" else 0.10
        return failure.strategy_family, penalty
