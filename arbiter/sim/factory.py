from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from arbiter.core.contracts import Bid, RepoSnapshot, RolloutLevel, TaskNode
from arbiter.market.archetypes import ARCHETYPES, ArchetypeDefinition


VARIANTS = [
    ("base", 0.0, 0.0, 0.0),
    ("narrow", -0.05, -0.1, 0.08),
    ("broad", 0.08, 0.08, -0.04),
]


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
        candidate_files = task.candidate_files or snapshot.complexity_hotspots[:2]
        task_word = task.task_type.value.replace("_", " ")
        for variant_name, utility_delta, risk_delta, confidence_delta in VARIANTS:
            bid = Bid(
                bid_id=uuid4().hex,
                task_id=task.task_id,
                role=role.role,
                variant_id=f"{role.role.lower()}-{variant_name}",
                strategy_family=f"{task.task_type.value}-{variant_name}",
                strategy_summary=f"{role.role} contender pursuing a {variant_name} {task_word} strategy",
                exact_action=f"Inspect {', '.join(candidate_files[:2]) or 'relevant files'} and execute a {variant_name} change set.",
                expected_benefit=0.65 + utility_delta,
                utility=max(0.1, min(0.95, 0.65 + role.validator_bias * 0.1 + utility_delta)),
                confidence=max(0.1, min(0.95, 0.58 + confidence_delta + role.rollback_bias * 0.05)),
                risk=max(0.05, min(0.95, 0.35 + risk_delta + (0.2 - role.risk_bias * 0.15))),
                cost=max(0.05, min(0.95, 0.25 + role.diff_bias * 0.2 + (0.05 if variant_name == "broad" else 0))),
                estimated_runtime_seconds=45 if variant_name != "broad" else 75,
                touched_files=candidate_files[: 1 if variant_name == "narrow" else 2],
                validator_plan=list(dict.fromkeys(task.validator_requirements + ["tests"])),
                rollback_plan="Reset to the accepted checkpoint and reopen bidding if validations regress.",
                dependency_impact="shared" if variant_name == "broad" else "localized",
                rollout_level=RolloutLevel.SANDBOX if variant_name == "broad" else RolloutLevel.CHEAP_PARTIAL,
                promotion_hints=["validation_failure", "regression"],
            )
            bids.append(bid)
        return bids

