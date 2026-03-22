from __future__ import annotations

from dataclasses import dataclass, field
import re

from arbiter.core.contracts import Bid, FailureContext


_SOURCE_FILE_PATTERN = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:py|js|jsx|ts|tsx))")


@dataclass
class RecoveryPlan:
    action: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    failed_family: str | None = None
    family_penalty: float = 0.0


class RecoveryEngine:
    @staticmethod
    def _validator_referenced_files(failure: FailureContext) -> set[str]:
        files: set[str] = set()
        for delta in failure.validator_deltas:
            for match in _SOURCE_FILE_PATTERN.findall(str(delta)):
                files.add(match.replace("\\", "/"))
        return files

    def family_penalty(self, failure: FailureContext) -> tuple[str | None, float]:
        if not failure.strategy_family:
            return None, 0.0
        penalty = 0.35 if failure.failure_type == "policy_block" else 0.20 if failure.failure_type == "validation_failure" else 0.10
        return failure.strategy_family, penalty

    def plan_recovery(self, current: Bid | None, standby: Bid | None, failure: FailureContext) -> RecoveryPlan:
        failed_family, penalty = self.family_penalty(failure)
        evidence: list[str] = []

        if failure.rollback_result == "rollback_failed":
            return RecoveryPlan(
                action="stop",
                reason="Rollback to the accepted checkpoint failed, so recovery cannot continue safely.",
                evidence=["rollback_failed"],
                failed_family=failed_family,
                family_penalty=penalty,
            )

        if standby is None:
            return RecoveryPlan(
                action="rebid",
                reason="No standby bid is available, so the market must reopen with failure evidence.",
                evidence=["no_standby"],
                failed_family=failed_family,
                family_penalty=penalty,
            )

        if standby.strategy_family == failure.strategy_family:
            evidence.append("same_strategy_family")
        if standby.rejection_reason:
            evidence.append("standby_rejected")
        if failure.rollback_result == "rollback_succeeded":
            evidence.append("rollback_succeeded")

        attempted_scope = {path.replace("\\", "/") for path in failure.attempted_file_scope}
        standby_scope = {path.replace("\\", "/") for path in standby.touched_files}
        validator_scope = self._validator_referenced_files(failure)
        if attempted_scope and standby_scope and standby_scope == attempted_scope:
            evidence.append("same_file_scope")
        if attempted_scope and standby_scope and len(standby_scope) < len(attempted_scope):
            evidence.append("smaller_scope")
        if validator_scope and attempted_scope and validator_scope.isdisjoint(attempted_scope):
            evidence.append("validator_scope_outside_attempted")
        if validator_scope and standby_scope and validator_scope.isdisjoint(standby_scope):
            evidence.append("validator_scope_outside_standby")
        if standby.risk <= (current.risk if current else standby.risk):
            evidence.append("lower_or_equal_risk")
        if failure.failure_type in standby.promotion_hints:
            evidence.append("promotion_hint_match")
        if "api_guard_failed" in failure.validator_deltas and standby_scope & attempted_scope:
            evidence.append("api_guard_overlap")
        if "file_churn_exceeded" in failure.validator_deltas and len(standby_scope) >= len(attempted_scope):
            evidence.append("file_churn_not_reduced")

        disqualifiers = {
            "same_strategy_family",
            "standby_rejected",
            "api_guard_overlap",
            "file_churn_not_reduced",
            "validator_scope_outside_attempted",
            "validator_scope_outside_standby",
        }
        if not any(item in disqualifiers for item in evidence):
            if (
                "promotion_hint_match" in evidence
                or "smaller_scope" in evidence
                or "lower_or_equal_risk" in evidence
                or failure.failure_type == "execution_stall"
            ):
                return RecoveryPlan(
                    action="promote_standby",
                    reason="The standby bid offers a materially different, safer retry path after rollback.",
                    evidence=evidence,
                    failed_family=failed_family,
                    family_penalty=penalty,
                )

        return RecoveryPlan(
            action="rebid",
            reason="Failure evidence disqualified immediate standby promotion, so the market should reopen with tighter context.",
            evidence=evidence,
            failed_family=failed_family,
            family_penalty=penalty,
        )
