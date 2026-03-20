from __future__ import annotations

from arbiter.core.contracts import Bid, FailureContext


class RecoveryEngine:
    def should_promote_standby(self, standby: Bid | None, failure: FailureContext) -> bool:
        if standby is None:
            return False
        return failure.failure_type in standby.promotion_hints

