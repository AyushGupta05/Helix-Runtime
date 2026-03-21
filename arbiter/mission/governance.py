from __future__ import annotations

from dataclasses import dataclass

from arbiter.core.contracts import (
    ActionIntent,
    ArbiterState,
    Bid,
    MissionOutcome,
    MissionSpec,
    PolicyDecision,
    PolicyState,
    RepoSnapshot,
    RunState,
    TaskNode,
    TaskStatus,
    ValidationReport,
)


@dataclass
class StopDecision:
    should_stop: bool
    reason: str | None = None
    outcome: MissionOutcome | None = None


class GovernanceEngine:
    TASK_PRIORITY = {
        "localize": 6.0,
        "bugfix": 5.0,
        "test": 4.5,
        "perf_diagnosis": 4.0,
        "perf_optimize": 3.5,
        "refactor": 2.5,
        "validate": 1.0,
    }

    def evaluate_repo(self, snapshot: RepoSnapshot, spec: MissionSpec) -> PolicyDecision:
        if snapshot.capabilities.runtime == "unsupported":
            return PolicyDecision(
                allowed=False,
                state=PolicyState.BLOCKED,
                reasons=[snapshot.capabilities.unsupported_reason or "unsupported_repo"],
            )
        risk = 0.2 + (0.1 if snapshot.dirty else 0.0) + min(len(snapshot.failure_signals) * 0.05, 0.2)
        return PolicyDecision(allowed=True, risk_score=min(risk, 1.0))

    def evaluate_task(self, task: TaskNode, snapshot: RepoSnapshot, spec: MissionSpec) -> PolicyDecision:
        repo_decision = self.evaluate_repo(snapshot, spec)
        if not repo_decision.allowed:
            return repo_decision
        reasons: list[str] = []
        validators = list(dict.fromkeys(task.validator_requirements))
        risk = max(task.risk_level, repo_decision.risk_score)
        if task.task_type.value.startswith("perf") and spec.risk_policy.require_benchmark_for_perf_claim:
            if not snapshot.capabilities.benchmark_commands and not spec.benchmark_requirement:
                reasons.append("benchmark_required")
        state = PolicyState.RESTRICTED if reasons else PolicyState.CLEAR
        return PolicyDecision(
            allowed=not reasons,
            state=state if not reasons else PolicyState.BLOCKED,
            reasons=reasons,
            risk_score=min(risk, 1.0),
            validators_required=validators,
        )

    def task_priority(self, task: TaskNode, all_tasks: list[TaskNode]) -> float:
        dependents = sum(1 for item in all_tasks if task.task_id in item.dependencies)
        required_bonus = 4.0 if task.required else 0.0
        type_bonus = self.TASK_PRIORITY.get(task.task_type.value, 0.0)
        validator_bonus = min(1.5, len(task.validator_requirements) * 0.3)
        dependency_bonus = dependents * 0.55
        risk_penalty = task.risk_level * 0.75
        return round(required_bonus + type_bonus + validator_bonus + dependency_bonus - risk_penalty, 4)

    def evaluate_bid(self, task: TaskNode, bid: Bid, spec: MissionSpec, failed_families: set[str]) -> PolicyDecision:
        reasons: list[str] = []
        runtime_cap_seconds = (
            spec.stop_policy.max_runtime_minutes * 60
            if spec.stop_policy.max_runtime_minutes is not None
            else None
        )
        if bid.strategy_family in failed_families:
            reasons.append("failed_family_banned")
        if len(bid.touched_files) > spec.stop_policy.max_file_scope:
            reasons.append("file_scope_exceeded")
        if any(path in spec.protected_paths for path in bid.touched_files):
            reasons.append("touches_protected_path")
        if task.validator_requirements and not set(task.validator_requirements).issubset(set(bid.validator_plan)):
            reasons.append("missing_required_validator")
        if runtime_cap_seconds is not None and bid.estimated_runtime_seconds > runtime_cap_seconds:
            reasons.append("runtime_budget_exceeded")
        if not set(task.allowed_tools).issubset(set(spec.allowed_tool_classes)):
            reasons.append("requires_disallowed_tool")
        risk = min(max(task.risk_level, bid.risk), 1.0)
        state = PolicyState.CLEAR if not reasons else PolicyState.BLOCKED
        return PolicyDecision(allowed=not reasons, state=state, reasons=reasons, risk_score=risk, validators_required=list(task.validator_requirements))

    def authorize_action(self, task: TaskNode, bid: Bid, intent: ActionIntent, spec: MissionSpec) -> PolicyDecision:
        reasons: list[str] = []
        if intent.action_type not in spec.allowed_tool_classes:
            reasons.append("tool_not_allowed")
        if intent.action_type not in task.allowed_tools and intent.action_type not in {"run_tests", "run_lint", "static_analysis", "benchmark", "create_commit", "revert_to_checkpoint"}:
            reasons.append("task_tool_mismatch")
        touched = intent.file_scope or bid.touched_files
        if len(touched) > spec.stop_policy.max_file_scope:
            reasons.append("action_file_scope_exceeded")
        if any(path in spec.protected_paths for path in touched):
            reasons.append("protected_file_write_denied")
        if spec.public_api_surface and intent.action_type == "edit_file" and any(path in spec.public_api_surface for path in touched):
            reasons.append("public_api_boundary_denied")
        risk = min(max(bid.risk, task.risk_level), 1.0)
        return PolicyDecision(allowed=not reasons, state=PolicyState.CLEAR if not reasons else PolicyState.BLOCKED, reasons=reasons, risk_score=risk)

    def evaluate_validation(self, task: TaskNode, report: ValidationReport, spec: MissionSpec) -> PolicyDecision:
        reasons: list[str] = []
        if not report.passed:
            reasons.append("validation_failed")
        if not report.policy_conformance:
            reasons.append("policy_non_conformance")
        if not report.api_guard_passed and spec.risk_policy.block_public_api_changes:
            reasons.append("api_guard_failed")
        if report.file_churn > spec.stop_policy.max_file_churn:
            reasons.append("file_churn_exceeded")
        risk = min(task.risk_level + (0.2 if reasons else 0.0), 1.0)
        return PolicyDecision(allowed=not reasons, state=PolicyState.CLEAR if not reasons else PolicyState.BLOCKED, reasons=reasons, risk_score=risk)

    def evaluate_mission_progress(self, state: ArbiterState) -> dict:
        """Assess current mission progress for the strategy market."""
        completed = [t for t in state.tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in state.tasks if t.status == TaskStatus.FAILED]
        total_rounds = state.strategy_round
        runtime_cap_seconds = (
            state.mission.max_runtime_minutes * 60
            if state.mission.max_runtime_minutes is not None
            else None
        )
        return {
            "completed_count": len(completed),
            "failed_count": len(failed),
            "total_rounds": total_rounds,
            "risk_score": state.governance.current_risk_score,
            "runtime_seconds": state.runtime_seconds,
            "budget_remaining_pct": (
                max(0, 1.0 - state.runtime_seconds / max(1, runtime_cap_seconds))
                if runtime_cap_seconds is not None
                else None
            ),
            "recovery_rounds_used": state.recovery_round,
            "policy_collisions": state.policy_collisions,
        }

    def evaluate_stop(self, state: ArbiterState) -> StopDecision:
        completed = [task for task in state.tasks if task.status == TaskStatus.COMPLETED]
        # In the market-driven model, the strategy market decides when to stop.
        # A mission with completed work and no more strategy rounds needed is successful.
        if state.strategy_round > 1 and completed and not any(
            task.status in {TaskStatus.READY, TaskStatus.RUNNING} for task in state.tasks
        ):
            # Check if the objective appears satisfied by completed moves
            required_tasks = [task for task in state.tasks if task.required]
            if required_tasks and all(task.status == TaskStatus.COMPLETED for task in required_tasks):
                return StopDecision(True, "mission_objective_met", MissionOutcome.SUCCESS)
        if state.governance.current_risk_score > state.mission.risk_policy.max_risk_score:
            return StopDecision(True, "risk_threshold_exceeded", MissionOutcome.FAILED_SAFE_STOP)
        if (
            state.mission.max_runtime_minutes is not None
            and state.runtime_seconds >= state.mission.max_runtime_minutes * 60
        ):
            return StopDecision(True, "runtime_budget_exceeded", MissionOutcome.FAILED_SAFE_STOP)
        if state.no_valid_contenders:
            return StopDecision(True, "no_valid_strategies", MissionOutcome.FAILED_EXECUTION)
        if state.policy_collisions > state.mission.stop_policy.max_policy_collisions:
            return StopDecision(True, "repeated_policy_block", MissionOutcome.POLICY_BLOCKED)
        if state.control.run_state == RunState.CANCELLING:
            return StopDecision(True, "user_cancelled", MissionOutcome.FAILED_SAFE_STOP)
        return StopDecision(False)
