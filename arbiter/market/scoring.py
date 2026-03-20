from __future__ import annotations

from arbiter.core.contracts import Bid, MissionSpec, TaskNode


def hard_filter_reason(bid: Bid, task: TaskNode, spec: MissionSpec, available_tools: set[str], failed_families: set[str]) -> str | None:
    if any(path in spec.protected_paths for path in bid.touched_files):
        return "touches_protected_path"
    if bid.strategy_family in failed_families:
        return "repeats_failed_family"
    if not set(task.allowed_tools).issubset(available_tools):
        return "requires_unavailable_tool"
    if task.validator_requirements and not set(task.validator_requirements).issubset(set(bid.validator_plan)):
        return "missing_required_validator"
    if bid.estimated_runtime_seconds > spec.stop_policy.max_runtime_minutes * 60:
        return "exceeds_runtime_budget"
    if len(bid.touched_files) > spec.stop_policy.max_file_scope:
        return "file_scope_exceeded"
    return None


def score_bid(bid: Bid) -> float:
    base = 0.40 * bid.utility + 0.25 * bid.confidence - 0.20 * bid.risk - 0.15 * bid.cost
    search = 0.10 * (bid.search_reward or bid.search_score or 0.0) if (bid.search_reward is not None or bid.search_score is not None) else 0.0
    policy = -0.30 if not bid.policy_feasibility.allowed else 0.0
    return round(base + search + policy, 4)
