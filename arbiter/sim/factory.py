from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from arbiter.core.contracts import Bid, PolicyDecision, RepoSnapshot, RolloutLevel, SimulationSummary, TaskNode
from arbiter.market.archetypes import ARCHETYPES, ArchetypeDefinition


VARIANTS = [
    ("base", 0.00, 0.00, 0.00, RolloutLevel.PAPER),
    ("narrow", -0.05, -0.08, 0.08, RolloutLevel.PARTIAL),
    ("broad", 0.08, 0.10, -0.04, RolloutLevel.SANDBOX),
]

ROLE_FAMILIES = {
    "Speed": ("speed-localized", "Prefer the smallest high-confidence path to a validated change."),
    "Safe": ("checkpoint-first", "Bias toward minimal scope, rollback safety, and guarded validation."),
    "Quality": ("quality-coverage", "Improve implementation quality while strengthening coverage."),
    "Test": ("coverage-first", "Lead with evidence and test reinforcement before wider edits."),
    "Performance": ("measure-then-optimize", "Treat benchmark evidence as the primary optimization signal."),
}


class SimulationFactory:
    def __init__(self, max_workers: int = 8) -> None:
        self.max_workers = max_workers

    def generate(self, task: TaskNode, snapshot: RepoSnapshot) -> list[Bid]:
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._build_role_variants, role, task, snapshot) for role in ARCHETYPES]
        bids: list[Bid] = []
        for future in futures:
            bids.extend(future.result())
        return bids

    def _build_role_variants(self, role: ArchetypeDefinition, task: TaskNode, snapshot: RepoSnapshot) -> list[Bid]:
        bids: list[Bid] = []
        family_name, family_summary = ROLE_FAMILIES[role.role]
        candidate_files = task.candidate_files or snapshot.complexity_hotspots[:3]
        required_validators = list(dict.fromkeys(task.validator_requirements or ["tests"]))
        base_id = uuid4().hex
        for variant_name, utility_delta, risk_delta, confidence_delta, rollout_level in VARIANTS:
            scope = candidate_files[: 1 if variant_name == "narrow" else 2 if variant_name == "base" else 3]
            risk = max(0.05, min(0.95, task.risk_level + risk_delta + (0.2 - role.risk_bias * 0.15)))
            bid = Bid(
                bid_id=uuid4().hex,
                task_id=task.task_id,
                role=role.role,
                variant_id=f"{role.role.lower()}-{variant_name}",
                strategy_family=family_name,
                strategy_summary=f"{family_summary} Variant: {variant_name}.",
                exact_action=f"Inspect {', '.join(scope) or 'the highest-signal files'} and execute a {variant_name} {task.task_type.value.replace('_', ' ')} plan.",
                expected_benefit=max(0.1, min(0.95, 0.62 + role.validator_bias * 0.08 + utility_delta)),
                utility=max(0.1, min(0.95, 0.62 + role.validator_bias * 0.08 + utility_delta)),
                confidence=max(0.1, min(0.95, 0.55 + role.rollback_bias * 0.05 + confidence_delta)),
                risk=risk,
                cost=max(0.05, min(0.95, 0.22 + role.diff_bias * 0.2 + (0.08 if variant_name == "broad" else 0.0))),
                estimated_runtime_seconds=35 if variant_name == "narrow" else 55 if variant_name == "base" else 85,
                touched_files=scope,
                validator_plan=required_validators,
                rollback_plan="Revert to the latest accepted checkpoint, retain failure evidence, and reopen bidding with tighter scope.",
                dependency_impact="localized" if len(scope) <= 2 else "shared",
                rollout_level=rollout_level,
                mutation_parent_id=None if variant_name == "base" else base_id,
                mutation_kind=variant_name,
                policy_feasibility=PolicyDecision(allowed=True, risk_score=risk),
                civic_permission_footprint=list(task.allowed_tools),
                promotion_hints=["validation_failure", "policy_block", "regression"],
            )
            if variant_name == "base":
                base_id = bid.bid_id
            bids.append(bid)
        return bids

    def rollout_plan(self, task: TaskNode, bids: list[Bid], failure_count: int = 0) -> dict[str, list[str] | int]:
        ordered = sorted(bids, key=lambda item: (item.score or item.utility), reverse=True)
        base_budget = 6
        if task.risk_level >= 0.6:
            base_budget += 4
        if failure_count:
            base_budget += min(6, failure_count * 2)
        if len({round(bid.confidence, 1) for bid in bids}) > 3:
            base_budget += 2
        partial_count = min(len(ordered), 6 if task.risk_level >= 0.6 else 4)
        sandbox_count = 0 if task.task_type.value in {"localize", "perf_diagnosis", "validate"} else min(len(ordered), 2 if task.risk_level >= 0.5 or failure_count else 1)
        return {
            "budget": base_budget,
            "paper": [bid.bid_id for bid in ordered],
            "partial": [bid.bid_id for bid in ordered[:partial_count]],
            "sandbox": [bid.bid_id for bid in ordered[:sandbox_count]],
        }

    def summarize(self, task: TaskNode, bids: list[Bid], plan: dict[str, list[str] | int]) -> SimulationSummary:
        rewards = [bid.search_reward or bid.search_score or bid.score or 0.0 for bid in bids]
        return SimulationSummary(
            task_id=task.task_id,
            total_bids=len(bids),
            valid_bids=len([bid for bid in bids if bid.policy_feasibility.allowed and not bid.rejection_reason]),
            paper_rollouts=len(plan["paper"]),
            partial_rollouts=len(plan["partial"]),
            sandbox_rollouts=len(plan["sandbox"]),
            budget_used=int(plan["budget"]),
            risk_forecast=max((bid.risk for bid in bids), default=0.0),
            validator_stability=max((bid.confidence for bid in bids), default=0.0),
            rollback_safety=max((1.0 - bid.risk for bid in bids), default=0.0),
            policy_confidence=max((1.0 - len(bid.policy_feasibility.reasons) * 0.2 for bid in bids), default=0.0),
            summary=f"Evaluated {len(bids)} bids with bounded rollout budget {int(plan['budget'])}.",
        )
