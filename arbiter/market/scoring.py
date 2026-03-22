from __future__ import annotations

import re

from arbiter.core.contracts import Bid, FailureContext, MissionSpec, TaskNode, TaskType


_FAILURE_FILE_PATTERN = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:py|js|jsx|ts|tsx))")


def _normalize_paths(paths: list[str] | None) -> list[str]:
    ordered: list[str] = []
    for path in paths or []:
        normalized = str(path).replace("\\", "/").strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _extract_failure_paths(failure_context: FailureContext | None) -> list[str]:
    if failure_context is None:
        return []
    candidates = _normalize_paths(failure_context.attempted_file_scope)
    for delta in failure_context.validator_deltas:
        for match in _FAILURE_FILE_PATTERN.findall(str(delta)):
            normalized = match.replace("\\", "/").strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates


def effective_file_scope_limit(bid: Bid, task: TaskNode, spec: MissionSpec) -> int:
    limit = int(spec.stop_policy.max_file_scope)
    bid_files = _normalize_paths(bid.touched_files)
    if len(bid_files) <= limit:
        return limit
    task_focus = set(_normalize_paths(task.candidate_files))
    if not task_focus:
        return limit
    overlap = [path for path in bid_files if path in task_focus]
    extras = [path for path in bid_files if path not in task_focus]
    required_validators = set(task.validator_requirements or [])
    planned_validators = set(bid.validator_plan or [])
    has_validator_coverage = not required_validators or required_validators.issubset(planned_validators)
    if (
        task.required
        and task.task_type == TaskType.BUGFIX
        and has_validator_coverage
        and len(overlap) >= min(6, len(task_focus))
        and len(extras) <= 3
    ):
        return limit + len(extras)
    return limit


def hard_filter_reason(bid: Bid, task: TaskNode, spec: MissionSpec, available_tools: set[str], failed_families: set[str]) -> str | None:
    runtime_cap_seconds = (
        spec.stop_policy.max_runtime_minutes * 60
        if spec.stop_policy.max_runtime_minutes is not None
        else None
    )
    if any(path in spec.protected_paths for path in bid.touched_files):
        return "touches_protected_path"
    if bid.strategy_family in failed_families:
        return "repeats_failed_family"
    if bid.proposed_task_type and bid.proposed_task_type != task.task_type.value:
        return "proposed_task_type_mismatch"
    if not set(task.allowed_tools).issubset(available_tools):
        return "requires_unavailable_tool"
    if task.validator_requirements and not set(task.validator_requirements).issubset(set(bid.validator_plan)):
        return "missing_required_validator"
    if runtime_cap_seconds is not None and bid.estimated_runtime_seconds > runtime_cap_seconds:
        return "exceeds_runtime_budget"
    if len(_normalize_paths(bid.touched_files)) > effective_file_scope_limit(bid, task, spec):
        return "file_scope_exceeded"
    return None


def score_bid(bid: Bid, *, task: TaskNode | None = None, failure_context: FailureContext | None = None) -> float:
    base = 0.40 * bid.utility + 0.25 * bid.confidence - 0.20 * bid.risk - 0.15 * bid.cost
    search = 0.10 * (bid.search_reward or bid.search_score or 0.0) if (bid.search_reward is not None or bid.search_score is not None) else 0.0
    policy = -0.30 if not bid.policy_feasibility.allowed else 0.0
    capability = 0.06 * float(bid.capability_reliance_score or 0.0)
    friction = -0.08 * float(bid.policy_friction_score or 0.0)
    revocation = -0.05 * float(bid.revocation_risk_score or 0.0)
    # Reward strategies that articulate a mission-level rationale
    rationale_bonus = 0.04 if bid.mission_rationale and len(bid.mission_rationale) > 20 else 0.0
    rollout_bonus = 0.0
    rollout_summary = str(bid.search_summary or "").lower()
    if "sandbox:error" in rollout_summary:
        rollout_bonus -= 0.18
    elif "sandbox:fail" in rollout_summary:
        rollout_bonus -= 0.10
    elif "sandbox:pass" in rollout_summary:
        rollout_bonus += 0.05
    if task is not None and task.required and task.task_type in {TaskType.BUGFIX, TaskType.TEST}:
        if "sandbox:no_patch" in rollout_summary:
            rollout_bonus -= 0.08
        if "partial" in rollout_summary and ("sandbox:error" in rollout_summary or "sandbox:fail" in rollout_summary):
            rollout_bonus -= 0.05
    touched = set(_normalize_paths(bid.touched_files))
    task_focus_bonus = 0.0
    task_focus = set()
    if task is not None and touched:
        task_focus = set(_normalize_paths(task.candidate_files))
        if task_focus:
            overlap = len(touched & task_focus)
            task_focus_bonus = min(0.08, overlap * 0.02)

    recovery_bonus = 0.0
    if failure_context is not None and touched:
        attempted = set(_normalize_paths(failure_context.attempted_file_scope))
        failure_paths = set(_extract_failure_paths(failure_context))
        validator_focus = failure_paths - attempted
        complementary_focus = set(_normalize_paths(task.candidate_files if task is not None else [])) - attempted
        touches_attempted = bool(touched & attempted)
        touches_validator_focus = bool(touched & validator_focus)
        touches_complementary_focus = bool(touched & complementary_focus)

        if validator_focus:
            recovery_bonus += 0.12 if touches_validator_focus else -0.08
        if attempted and complementary_focus:
            if touches_attempted and touches_complementary_focus:
                recovery_bonus += 0.12
            elif touches_attempted and not touches_complementary_focus:
                recovery_bonus -= 0.05
            elif touches_complementary_focus:
                recovery_bonus += 0.03
        if task_focus and len(task_focus) >= 5:
            overlap = len(touched & task_focus)
            if overlap >= 4:
                recovery_bonus += min(0.16, (overlap - 3) * 0.05)
            elif overlap <= 3:
                recovery_bonus -= 0.05

    return round(base + search + policy + capability + friction + revocation + rationale_bonus + rollout_bonus + task_focus_bonus + recovery_bonus, 4)
