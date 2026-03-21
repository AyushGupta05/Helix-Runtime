from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import uuid4

from arbiter.core.contracts import (
    Bid,
    BidGenerationMode,
    PolicyDecision,
    RepoSnapshot,
    RolloutLevel,
    SimulationSummary,
    TaskNode,
    utc_now,
)
from arbiter.market.archetypes import ARCHETYPES, ArchetypeDefinition
from arbiter.runtime.model_payloads import extract_strategy_payload

logger = logging.getLogger(__name__)

VARIANTS = [
    {
        "name": "base",
        "utility_delta": 0.00,
        "risk_delta": 0.00,
        "confidence_delta": 0.00,
        "rollout_level": RolloutLevel.PAPER,
        "scope_limit": 2,
        "runtime_multiplier": 1.0,
        "directive": "Keep the plan balanced and implementation-ready.",
    },
    {
        "name": "narrow",
        "utility_delta": -0.05,
        "risk_delta": -0.08,
        "confidence_delta": 0.08,
        "rollout_level": RolloutLevel.PARTIAL,
        "scope_limit": 1,
        "runtime_multiplier": 0.6,
        "directive": "Produce the smallest viable plan with minimal blast radius.",
    },
    {
        "name": "broad",
        "utility_delta": 0.08,
        "risk_delta": 0.10,
        "confidence_delta": -0.04,
        "rollout_level": RolloutLevel.SANDBOX,
        "scope_limit": None,
        "runtime_multiplier": 1.5,
        "directive": "Produce the most comprehensive version of the strategy.",
    },
]

ROLE_FAMILIES = {
    "Speed": (
        "speed-localized",
        "Advance the mission via the smallest high-confidence move that produces a validated result.",
    ),
    "Safe": (
        "checkpoint-first",
        "Advance the mission through minimal-scope moves with rollback safety and guarded validation at every step.",
    ),
    "Quality": (
        "quality-coverage",
        "Advance the mission by improving implementation quality and strengthening coverage across the objective.",
    ),
    "Test": (
        "coverage-first",
        "Advance the mission by leading with evidence and test reinforcement before committing to wider changes.",
    ),
    "Performance": (
        "measure-then-optimize",
        "Advance the mission by treating benchmark evidence as the primary signal for optimization decisions.",
    ),
}

_ARCHETYPE_SYSTEM_PROMPT = (
    "You are a competing strategy in an autonomous market-driven coding runtime.\n"
    "You are the {role} archetype. Your strategic identity: {family_summary}\n\n"
    "You are competing against other strategies to propose the best next move\n"
    "for the overall mission. The mission is governed by continuous strategic\n"
    "competition — there is no fixed plan. You must argue why your proposed\n"
    "move is the right one for the mission right now.\n\n"
    "Return only valid JSON with fields:\n"
    '  "mission_rationale" (why this move is the best next step for the mission),\n'
    '  "strategy_summary" (short description of your approach),\n'
    '  "exact_action" (specific tactical steps),\n'
    '  "proposed_task_title" (short title for the work you propose),\n'
    '  "proposed_task_type" (one of: localize, bugfix, test, refactor, perf_diagnosis, perf_optimize, validate),\n'
    '  "utility" (0.0-1.0 float, expected benefit to mission),\n'
    '  "risk" (0.0-1.0 float),\n'
    '  "confidence" (0.0-1.0 float),\n'
    '  "estimated_runtime_seconds" (int),\n'
    '  "touched_files" (list of file paths to modify).\n\n'
    "Stay in character as the {role} archetype. "
    "You must stay within the current move type chosen by Arbiter; do not switch to a different task type. "
    "Your risk tolerance is {risk_bias:.2f}, diff preference is {diff_bias:.2f}, "
    "validator emphasis is {validator_bias:.2f}."
)


def _clamp(value: float, minimum: float = 0.05, maximum: float = 0.95) -> float:
    return max(minimum, min(maximum, value))


def _stable_seed(*parts: object) -> int:
    digest = hashlib.sha256("::".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * probability)))
    return float(ordered[position])


def _text_value(value: object, fallback: str) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or fallback
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(parts) if parts else fallback
    if value is None:
        return fallback
    return str(value)


@dataclass
class BidGenerationBatch:
    bids: list[Bid] = field(default_factory=list)
    generation_mode: BidGenerationMode = BidGenerationMode.PROVIDER_MODEL
    degraded_reason: str | None = None
    provider_errors: list[str] = field(default_factory=list)
    provider_invocation_ids: list[str] = field(default_factory=list)


class SimulationFactory:
    def __init__(
        self,
        max_workers: int = 8,
        backend=None,
        bidder_models: list[str] | None = None,
        provider_pool: list[str] | None = None,
        on_invocation=None,
    ) -> None:
        self.max_workers = max_workers
        self.backend = backend
        self.bidder_models = bidder_models or []
        self.provider_pool = provider_pool or []
        self.on_invocation = on_invocation
        self.market_token_usage: dict[str, int] = {}
        self.market_cost_usage: dict[str, float] = {}
        self._current_mission_context: dict | None = None

    def _backend_mode(self) -> BidGenerationMode:
        if self.backend and hasattr(self.backend, "market_generation_mode"):
            return self.backend.market_generation_mode()
        return BidGenerationMode.DETERMINISTIC_FALLBACK

    def generate(
        self,
        task: TaskNode,
        snapshot: RepoSnapshot,
        *,
        allow_fallback: bool = False,
        mission_context: dict | None = None,
    ) -> BidGenerationBatch:
        self.market_token_usage.clear()
        self.market_cost_usage.clear()
        self._current_mission_context = mission_context
        backend_mode = self._backend_mode()

        if backend_mode == BidGenerationMode.MOCK:
            return BidGenerationBatch(
                bids=self._build_non_provider_role_variants(
                    task,
                    snapshot,
                    generation_mode=BidGenerationMode.MOCK,
                    reason="Mock strategy mode is active; no provider calls were made.",
                    provider="scripted",
                    lane="scripted",
                    model_id="scripted",
                    mission_context=mission_context,
                ),
                generation_mode=BidGenerationMode.MOCK,
                degraded_reason="Mock strategy mode is active.",
            )

        provider_capable = bool(
            self.backend
            and getattr(self.backend, "supports_provider_bid_generation", lambda: False)()
            and hasattr(self.backend, "router")
            and self.provider_pool
        )

        if not provider_capable:
            reason = "No provider-backed strategy lanes are configured for the market."
            if allow_fallback:
                return BidGenerationBatch(
                    bids=self._build_non_provider_role_variants(
                        task,
                        snapshot,
                        generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                        reason=reason,
                        mission_context=mission_context,
                    ),
                    generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                    degraded_reason=reason,
                )
            return BidGenerationBatch(
                bids=[],
                generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                degraded_reason=reason,
            )

        provider_bids, provider_errors, failed_specs, provider_invocation_ids = self._generate_provider_market(
            task,
            snapshot,
        )
        if provider_errors and failed_specs and allow_fallback:
            provider_bids.extend(
                self._build_non_provider_role_variants(
                    task,
                    snapshot,
                    generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                    reason="Provider strategy generation failed for part of the market.",
                    failed_specs=failed_specs,
                    mission_context=mission_context,
                )
            )
            return BidGenerationBatch(
                bids=provider_bids,
                generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                degraded_reason="Provider failures forced deterministic fallback for part of the strategy market.",
                provider_errors=provider_errors,
                provider_invocation_ids=provider_invocation_ids,
            )
        if not provider_bids:
            reason = "Provider strategy generation produced no provider-backed strategies."
            if provider_errors:
                reason = f"{reason} Errors: {'; '.join(provider_errors)}"
            if allow_fallback:
                return BidGenerationBatch(
                    bids=self._build_non_provider_role_variants(
                        task,
                        snapshot,
                        generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                        reason=reason,
                        mission_context=mission_context,
                    ),
                    generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                    degraded_reason=reason,
                    provider_errors=provider_errors,
                    provider_invocation_ids=provider_invocation_ids,
                )
            return BidGenerationBatch(
                bids=[],
                generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                degraded_reason=reason,
                provider_errors=provider_errors,
                provider_invocation_ids=provider_invocation_ids,
            )
        return BidGenerationBatch(
            bids=provider_bids,
            generation_mode=backend_mode,
            degraded_reason="; ".join(provider_errors) if provider_errors else None,
            provider_errors=provider_errors,
            provider_invocation_ids=provider_invocation_ids,
        )

    def _generate_provider_market(
        self,
        task: TaskNode,
        snapshot: RepoSnapshot,
    ) -> tuple[list[Bid], list[str], list[tuple[ArchetypeDefinition, dict[str, object]]], list[str]]:
        config = getattr(getattr(self.backend, "router", None), "config", None)
        specs = []
        for provider in self.provider_pool:
            allowed_lanes = set(config.market_lanes_for(provider)) if config and hasattr(config, "market_lanes_for") else None
            for archetype in ARCHETYPES:
                if allowed_lanes is not None and archetype.default_lane not in allowed_lanes:
                    continue
                for variant in VARIANTS:
                    specs.append((provider, archetype, variant))
        if not specs:
            return [], [], [], []
        bids: list[Bid] = []
        provider_errors: list[str] = []
        failed_specs: list[tuple[ArchetypeDefinition, dict[str, object]]] = []
        provider_invocation_ids: list[str] = []
        worker_count = max(1, min(self.max_workers, len(specs)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(self._build_provider_bid, provider, archetype, variant, task, snapshot): (provider, archetype, variant)
                for provider, archetype, variant in specs
            }
            for future, spec in future_map.items():
                provider, archetype, variant = spec
                try:
                    bid = future.result()
                except Exception as exc:
                    message = f"{provider}/{archetype.role}/{variant['name']}: {exc}"
                    logger.warning("Provider-backed bid generation failed for %s", message)
                    provider_errors.append(message)
                    failed_specs.append((archetype, variant))
                    continue
                if bid.invocation_id:
                    provider_invocation_ids.append(bid.invocation_id)
                self._merge_market_usage(bid.token_usage, bid.cost_usage)
                bids.append(bid)
        return bids, provider_errors, failed_specs, provider_invocation_ids

    def _build_provider_bid(
        self,
        provider: str,
        role: ArchetypeDefinition,
        variant: dict[str, object],
        task: TaskNode,
        snapshot: RepoSnapshot,
    ) -> Bid:
        family_name, family_summary = ROLE_FAMILIES[role.role]
        candidate_files = task.candidate_files or snapshot.complexity_hotspots[:3]
        lane = role.default_lane
        lane_key = f"{lane}.{provider}"
        lane_config = self.backend.router.config.model_lanes.get(lane_key) or self.backend.router.config.model_lanes[lane]
        invocation_id = uuid4().hex
        started_at = utc_now().isoformat()
        system_prompt = _ARCHETYPE_SYSTEM_PROMPT.format(
            role=role.role,
            family_summary=family_summary,
            risk_bias=role.risk_bias,
            diff_bias=role.diff_bias,
            validator_bias=role.validator_bias,
        )
        mc = self._current_mission_context or {}
        mission_section = ""
        if mc:
            completed = mc.get("completed_moves", [])
            failed = mc.get("failed_moves", [])
            landscape = mc.get("mission_landscape", [])
            mission_section = (
                f"Mission objective: {mc.get('objective', task.title)}\n"
                f"Strategy round: {mc.get('strategy_round', 1)}\n"
                f"Completed moves: {', '.join(completed) if completed else 'none yet'}\n"
                f"Failed moves: {', '.join(failed) if failed else 'none'}\n"
                f"Mission landscape: {'; '.join(landscape[:4]) if landscape else 'not yet mapped'}\n"
                f"Constraints: {', '.join(mc.get('constraints', [])) or 'none'}\n"
            )
        user_prompt = (
            f"{mission_section}"
            f"Current move type: {task.task_type.value}\n"
            f"Risk level: {task.risk_level}\n"
            f"Variant: {variant['name']}\n"
            f"Variant directive: {variant['directive']}\n"
            f"Candidate files: {', '.join(candidate_files) or 'none identified'}\n"
            f"Validators required: {', '.join(task.validator_requirements or ['tests'])}\n"
            f"You must propose a {task.task_type.value} move and keep the work bounded to that task type.\n"
            f"Propose your next best move for this mission as the {role.role} archetype.\n"
            "Explain why this move is the right strategic choice now.\n"
            "Return JSON only."
        )
        if self.on_invocation:
            self.on_invocation(
                {
                    "invocation_id": invocation_id,
                    "provider": provider,
                    "lane": lane_key,
                    "model_id": lane_config.model_id,
                    "invocation_kind": "bid_generation",
                    "generation_mode": self._backend_mode(),
                    "status": "started",
                    "task_id": task.task_id,
                    "started_at": started_at,
                    "prompt_preview": user_prompt[:1200],
                }
            )
        try:
            result = self.backend.router.invoke(
                lane=lane_key,
                prompt={"system": system_prompt, "user": user_prompt},
            )
        except Exception as exc:
            if self.on_invocation:
                self.on_invocation(
                    {
                        "invocation_id": invocation_id,
                        "provider": provider,
                        "lane": lane_key,
                        "model_id": lane_config.model_id,
                        "invocation_kind": "bid_generation",
                        "generation_mode": self._backend_mode(),
                        "status": "failed",
                        "task_id": task.task_id,
                        "started_at": started_at,
                        "completed_at": utc_now().isoformat(),
                        "prompt_preview": user_prompt[:1200],
                        "error": str(exc),
                    }
                )
            raise
        result.invocation_id = invocation_id
        payload = self._parse_json_payload(result.content)
        touched_files = payload.get("touched_files")
        if not isinstance(touched_files, list) or not touched_files:
            touched_files = candidate_files
        scope_limit = variant["scope_limit"]
        scoped_files = touched_files if scope_limit is None else touched_files[: int(scope_limit)]
        required_validators = list(dict.fromkeys(task.validator_requirements or ["tests"]))
        mission_rationale = str(
            payload.get("mission_rationale")
            or payload.get("rationale")
            or f"As the {role.role} archetype, this move advances the mission by applying {family_summary.lower()}"
        )
        provider_cost_signal = min(0.18, float((result.cost_usage or {}).get("usd", 0.0)) * 12.0)
        default_exact_action = (
            f"Inspect {', '.join(scoped_files) or 'the highest-signal files'} and execute the "
            f"{variant['name']} {task.task_type.value.replace('_', ' ')} plan."
        )
        bid = Bid(
            bid_id=uuid4().hex,
            task_id=task.task_id,
            role=role.role,
            provider=result.provider or provider,
            lane=result.lane or lane_key,
            model_id=result.model_id or lane_config.model_id,
            invocation_id=invocation_id,
            variant_id=f"{role.role.lower()}-{variant['name']}-{provider}",
            strategy_family=family_name,
            strategy_summary=_text_value(
                payload.get("strategy_summary"),
                f"{family_summary} Variant: {variant['name']}.",
            ),
            exact_action=_text_value(payload.get("exact_action"), default_exact_action),
            mission_rationale=mission_rationale,
            proposed_task_title=_text_value(payload.get("proposed_task_title"), task.title),
            proposed_task_type=_text_value(payload.get("proposed_task_type"), task.task_type.value),
            expected_benefit=_clamp(float(payload.get("utility", 0.62 + role.validator_bias * 0.08)) + float(variant["utility_delta"])),
            utility=_clamp(float(payload.get("utility", 0.62 + role.validator_bias * 0.08)) + float(variant["utility_delta"])),
            confidence=_clamp(float(payload.get("confidence", 0.55 + role.rollback_bias * 0.05)) + float(variant["confidence_delta"])),
            risk=_clamp(float(payload.get("risk", task.risk_level + 0.2 - role.risk_bias * 0.15)) + float(variant["risk_delta"])),
            cost=_clamp(0.22 + role.diff_bias * 0.2 + (0.08 if variant["name"] == "broad" else 0.0) + provider_cost_signal),
            estimated_runtime_seconds=max(
                15.0,
                float(payload.get("estimated_runtime_seconds", 55)) * float(variant["runtime_multiplier"]),
            ),
            touched_files=scoped_files,
            validator_plan=required_validators,
            rollback_plan="Revert to the latest accepted checkpoint, retain failure evidence, and reopen bidding with tighter scope.",
            dependency_impact="localized" if len(scoped_files) <= 2 else "shared",
            rollout_level=variant["rollout_level"],
            mutation_parent_id=None,
            mutation_kind=str(variant["name"]),
            policy_feasibility=PolicyDecision(allowed=True),
            civic_permission_footprint=list(task.allowed_tools),
            promotion_hints=["validation_failure", "policy_block", "regression"],
            token_usage=result.token_usage,
            cost_usage=result.cost_usage,
            usage_unavailable_reason=result.usage_unavailable_reason,
            prompt_preview=result.prompt_preview,
            response_preview=result.response_preview,
            generation_mode=result.generation_mode,
        )
        if self.on_invocation:
            self.on_invocation(
                {
                    "invocation_id": invocation_id,
                    "provider": bid.provider,
                    "lane": bid.lane,
                    "model_id": bid.model_id,
                    "invocation_kind": "bid_generation",
                    "generation_mode": bid.generation_mode,
                    "status": "completed",
                    "task_id": task.task_id,
                    "bid_id": bid.bid_id,
                    "started_at": result.started_at or started_at,
                    "completed_at": result.completed_at,
                    "prompt_preview": result.prompt_preview,
                    "response_preview": result.response_preview,
                    "raw_usage": result.raw_usage,
                    "token_usage": result.token_usage,
                    "cost_usage": result.cost_usage,
                    "usage_unavailable_reason": result.usage_unavailable_reason,
                }
            )
        return bid

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, object]:
        try:
            parsed = extract_strategy_payload(content)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _build_non_provider_role_variants(
        self,
        task: TaskNode,
        snapshot: RepoSnapshot,
        *,
        generation_mode: BidGenerationMode,
        reason: str,
        provider: str = "system",
        lane: str | None = None,
        model_id: str | None = None,
        failed_specs: list[tuple[ArchetypeDefinition, dict[str, object]]] | None = None,
        mission_context: dict | None = None,
    ) -> list[Bid]:
        bids: list[Bid] = []
        specs = failed_specs or [(role, variant) for role in ARCHETYPES for variant in VARIANTS]
        mc = mission_context or self._current_mission_context or {}
        strategy_round = mc.get("strategy_round", 1)
        completed = mc.get("completed_moves", [])
        for role, variant in specs:
            family_name, family_summary = ROLE_FAMILIES[role.role]
            candidate_files = task.candidate_files or snapshot.complexity_hotspots[:3]
            scope_limit = variant["scope_limit"]
            scoped_files = candidate_files if scope_limit is None else candidate_files[: int(scope_limit)]
            risk = _clamp(task.risk_level + float(variant["risk_delta"]) + (0.2 - role.risk_bias * 0.15))
            rationale = (
                f"Round {strategy_round}: {family_summary.rstrip('.')} — "
                f"{'building on ' + str(len(completed)) + ' completed moves' if completed else 'opening the mission'}."
            )
            bids.append(
                Bid(
                    bid_id=uuid4().hex,
                    task_id=task.task_id,
                    role=role.role,
                    provider=provider,
                    lane=lane or role.default_lane,
                    model_id=model_id,
                    invocation_id=None,
                    variant_id=f"{role.role.lower()}-{variant['name']}",
                    strategy_family=family_name,
                    strategy_summary=f"{family_summary} Variant: {variant['name']}.",
                    exact_action=f"Inspect {', '.join(scoped_files) or 'the highest-signal files'} and execute a {variant['name']} {task.task_type.value.replace('_', ' ')} strategy.",
                    mission_rationale=rationale,
                    proposed_task_title=task.title,
                    proposed_task_type=task.task_type.value,
                    expected_benefit=_clamp(0.62 + role.validator_bias * 0.08 + float(variant["utility_delta"])),
                    utility=_clamp(0.62 + role.validator_bias * 0.08 + float(variant["utility_delta"])),
                    confidence=_clamp(0.55 + role.rollback_bias * 0.05 + float(variant["confidence_delta"])),
                    risk=risk,
                    cost=_clamp(0.22 + role.diff_bias * 0.2 + (0.08 if variant["name"] == "broad" else 0.0)),
                    estimated_runtime_seconds=55 * float(variant["runtime_multiplier"]),
                    touched_files=scoped_files,
                    validator_plan=list(dict.fromkeys(task.validator_requirements or ["tests"])),
                    rollback_plan="Revert to the latest accepted checkpoint, retain failure evidence, and reopen strategy market with tighter scope.",
                    dependency_impact="localized" if len(scoped_files) <= 2 else "shared",
                    rollout_level=variant["rollout_level"],
                    mutation_parent_id=None,
                    mutation_kind=str(variant["name"]),
                    policy_feasibility=PolicyDecision(allowed=True, risk_score=risk),
                    civic_permission_footprint=list(task.allowed_tools),
                    promotion_hints=["validation_failure", "policy_block", "regression"],
                    token_usage=None,
                    cost_usage=None,
                    usage_unavailable_reason=reason,
                    generation_mode=generation_mode,
                )
            )
        return bids

    def _merge_market_usage(
        self,
        token_usage: dict[str, int] | None,
        cost_usage: dict[str, float] | None,
    ) -> None:
        for key, value in (token_usage or {}).items():
            self.market_token_usage[key] = self.market_token_usage.get(key, 0) + int(value)
        for key, value in (cost_usage or {}).items():
            self.market_cost_usage[key] = self.market_cost_usage.get(key, 0.0) + float(value)

    @staticmethod
    def _runtime_pressure(task: TaskNode, bid: Bid) -> float:
        runtime_budget = {
            "small": 90.0,
            "medium": 180.0,
            "large": 300.0,
        }[task.runtime_class]
        return max(0.0, min(1.0, bid.estimated_runtime_seconds / runtime_budget))

    def _search_priors(
        self,
        task: TaskNode,
        bid: Bid,
        rollout_evidence: list[str],
        failure_count: int,
    ) -> dict[str, float]:
        evidence = set(rollout_evidence)
        required_validators = set(task.validator_requirements or bid.validator_plan or ["tests"])
        validator_alignment = len(required_validators & set(bid.validator_plan)) / max(1, len(required_validators))
        scope_pressure = min(1.0, len(bid.touched_files or task.candidate_files) / max(1, task.search_depth * 2))
        provider_reliability = {
            BidGenerationMode.PROVIDER_MODEL: 0.88,
            BidGenerationMode.REPLAY: 0.8,
            BidGenerationMode.MOCK: 0.6,
            BidGenerationMode.DETERMINISTIC_FALLBACK: 0.5,
        }.get(bid.generation_mode, 0.55)
        if "sandbox:pass" in evidence:
            provider_reliability += 0.05
        if "sandbox:fail" in evidence:
            provider_reliability -= 0.08
        provider_reliability = _clamp(provider_reliability, 0.2, 0.98)
        runtime_pressure = self._runtime_pressure(task, bid)
        success_alpha = (
            1.5
            + bid.confidence * 6.0
            + validator_alignment * 3.0
            + (1.6 if "sandbox:pass" in evidence else 0.0)
            + (0.6 if "partial" in evidence else 0.0)
        )
        success_beta = (
            1.2
            + bid.risk * 5.0
            + scope_pressure * 2.5
            + failure_count * 0.6
            + (1.8 if "sandbox:fail" in evidence else 0.0)
        )
        rollback_alpha = 1.0 + bid.risk * 4.5 + scope_pressure * 1.8 + failure_count * 0.5
        rollback_beta = (
            2.5
            + bid.confidence * 3.0
            + validator_alignment * 1.5
            + (1.3 if "sandbox:pass" in evidence else 0.0)
        )
        drift_alpha = 1.0 + scope_pressure * 4.0 + (1.2 if bid.dependency_impact == "shared" else 0.2)
        drift_beta = 2.3 + bid.confidence * 2.6 + (1.0 if len(bid.touched_files) <= 2 else 0.0)
        base_reward = (
            0.55 * bid.utility
            + 0.22 * validator_alignment
            + 0.1 * provider_reliability
            - 0.18 * bid.cost
            - 0.16 * runtime_pressure
        )
        return {
            "validator_alignment": validator_alignment,
            "scope_pressure": scope_pressure,
            "provider_reliability": provider_reliability,
            "runtime_pressure": runtime_pressure,
            "success_alpha": success_alpha,
            "success_beta": success_beta,
            "rollback_alpha": rollback_alpha,
            "rollback_beta": rollback_beta,
            "drift_alpha": drift_alpha,
            "drift_beta": drift_beta,
            "base_reward": base_reward,
        }

    def evaluate_search(
        self,
        task: TaskNode,
        bid: Bid,
        *,
        rollout_evidence: list[str],
        failure_count: int = 0,
    ) -> dict[str, float | int]:
        priors = self._search_priors(task, bid, rollout_evidence, failure_count)
        sample_count = max(task.monte_carlo_samples, 16 + task.search_depth * 8 + failure_count * 4)
        rng = random.Random(
            _stable_seed(
                task.task_id,
                bid.bid_id,
                bid.invocation_id or bid.variant_id,
                failure_count,
                ",".join(sorted(rollout_evidence)),
            )
        )
        outcomes: list[float] = []
        successes = 0
        rollbacks = 0
        drifts = 0
        evidence = set(rollout_evidence)
        for _ in range(sample_count):
            p_success = min(0.995, rng.betavariate(priors["success_alpha"], priors["success_beta"]) * priors["provider_reliability"])
            p_rollback = min(0.995, rng.betavariate(priors["rollback_alpha"], priors["rollback_beta"]))
            p_drift = min(0.995, rng.betavariate(priors["drift_alpha"], priors["drift_beta"]))
            success = 1 if rng.random() < p_success else 0
            rollback = 1 if rng.random() < p_rollback else 0
            drift = 1 if rng.random() < p_drift else 0
            score = (
                priors["base_reward"]
                + 0.82 * success
                - 0.7 * rollback
                - 0.42 * drift
                - 0.35 * bid.risk * rollback
                - 0.08 * priors["scope_pressure"] * drift
            )
            if success and not rollback:
                score += 0.12
            if "sandbox:pass" in evidence:
                score += 0.05
            if "sandbox:fail" in evidence:
                score -= 0.08
            outcomes.append(max(0.0, min(1.0, score)))
            successes += success
            rollbacks += rollback
            drifts += drift
        mean_score = float(statistics.fmean(outcomes)) if outcomes else 0.0
        stddev = float(statistics.pstdev(outcomes)) if len(outcomes) > 1 else 0.0
        p10 = _quantile(outcomes, 0.1)
        p50 = _quantile(outcomes, 0.5)
        p90 = _quantile(outcomes, 0.9)
        success_rate = successes / sample_count if sample_count else 0.0
        rollback_rate = rollbacks / sample_count if sample_count else 0.0
        drift_rate = drifts / sample_count if sample_count else 0.0
        search_score = max(
            0.0,
            min(
                1.0,
                mean_score
                - 0.35 * stddev
                + 0.1 * p10
                + 0.05 * success_rate
                - 0.05 * rollback_rate,
            ),
        )
        search_reward = max(0.0, min(1.0, mean_score + 0.05 * (p90 - p10)))
        return {
            "sample_count": sample_count,
            "mean_score": round(mean_score, 4),
            "stddev": round(stddev, 4),
            "p10": round(p10, 4),
            "p50": round(p50, 4),
            "p90": round(p90, 4),
            "success_rate": round(success_rate, 4),
            "rollback_rate": round(rollback_rate, 4),
            "drift_rate": round(drift_rate, 4),
            "validator_alignment": round(priors["validator_alignment"], 4),
            "provider_reliability": round(priors["provider_reliability"], 4),
            "runtime_pressure": round(priors["runtime_pressure"], 4),
            "search_score": round(search_score, 4),
            "search_reward": round(search_reward, 4),
        }

    def rollout_plan(
        self,
        task: TaskNode,
        bids: list[Bid],
        failure_count: int = 0,
    ) -> dict[str, list[str] | int]:
        ordered = sorted(bids, key=lambda item: (item.score or item.utility), reverse=True)
        base_budget = max(6, task.search_depth * 3 + math.ceil(task.monte_carlo_samples / 8))
        if task.risk_level >= 0.6:
            base_budget += 4
        if failure_count:
            base_budget += min(6, failure_count * 2)
        if len({round(bid.confidence, 1) for bid in bids}) > 3:
            base_budget += 2
        partial_count = min(len(ordered), max(4, task.search_depth + 2))
        frontier_gap = 1.0
        if len(ordered) > 1:
            leader_score = ordered[0].score or ordered[0].utility
            challenger_score = ordered[1].score or ordered[1].utility
            frontier_gap = abs(float(leader_score) - float(challenger_score))
        sandbox_count = 0
        if task.task_type.value not in {"localize", "perf_diagnosis", "validate"} and ordered:
            sandbox_count = 1
            if failure_count and len(ordered) > 1:
                sandbox_count += 1
            elif task.risk_level >= 0.65 and frontier_gap <= 0.05 and len(ordered) > 1:
                sandbox_count += 1
            sandbox_count = min(len(ordered), sandbox_count)
        return {
            "budget": base_budget,
            "paper": [bid.bid_id for bid in ordered],
            "partial": [bid.bid_id for bid in ordered[:partial_count]],
            "sandbox": [bid.bid_id for bid in ordered[:sandbox_count]],
        }

    def summarize(self, task: TaskNode, bids: list[Bid], plan: dict[str, list[str] | int]) -> SimulationSummary:
        provider_backed = sum(1 for bid in bids if bid.generation_mode == BidGenerationMode.PROVIDER_MODEL)
        fallback = sum(1 for bid in bids if bid.generation_mode == BidGenerationMode.DETERMINISTIC_FALLBACK)
        ordered = sorted(
            (bid.search_score if bid.search_score is not None else bid.score or 0.0) for bid in bids
        )
        frontier_gap = (ordered[-1] - ordered[-2]) if len(ordered) > 1 else (ordered[-1] if ordered else 0.0)
        monte_carlo_samples = max(
            [int(bid.search_diagnostics.get("sample_count", 0)) for bid in bids if bid.search_diagnostics] or [0]
        )
        validator_stability = max(
            [float(bid.search_diagnostics.get("success_rate", bid.confidence)) for bid in bids] or [0.0]
        )
        rollback_safety = max(
            [1.0 - float(bid.search_diagnostics.get("rollback_rate", bid.risk)) for bid in bids] or [0.0]
        )
        return SimulationSummary(
            task_id=task.task_id,
            search_mode="bounded_monte_carlo",
            total_bids=len(bids),
            valid_bids=len([bid for bid in bids if bid.policy_feasibility.allowed and not bid.rejection_reason]),
            paper_rollouts=len(plan["paper"]),
            partial_rollouts=len(plan["partial"]),
            sandbox_rollouts=len(plan["sandbox"]),
            budget_used=int(plan["budget"]),
            frontier_size=len(bids),
            monte_carlo_samples=monte_carlo_samples,
            frontier_gap=round(frontier_gap, 4),
            risk_forecast=max((bid.risk for bid in bids), default=0.0),
            validator_stability=round(validator_stability, 4),
            rollback_safety=round(rollback_safety, 4),
            policy_confidence=max((1.0 - len(bid.policy_feasibility.reasons) * 0.2 for bid in bids), default=0.0),
            summary=(
                f"Evaluated {len(bids)} bids ({provider_backed} provider-backed, {fallback} fallback) "
                f"with bounded Monte Carlo search over {monte_carlo_samples or task.monte_carlo_samples} samples per bid "
                f"and rollout budget {int(plan['budget'])}; frontier gap {frontier_gap:.3f}."
            ),
        )
