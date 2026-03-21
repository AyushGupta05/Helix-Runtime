from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from arbiter.core.contracts import (
    BidGenerationMode,
    MissionSpec,
    RepoSnapshot,
    SuccessCriteria,
    TaskNode,
    TaskRequirementLevel,
    TaskStatus,
    TaskType,
    utc_now,
)


@dataclass
class DecompositionCandidate:
    source: str
    tasks: list[TaskNode] = field(default_factory=list)
    score: float = 0.0
    summary: str = ""
    provider: str | None = None
    lane: str | None = None
    model_id: str | None = None
    invocation_id: str | None = None
    generation_mode: BidGenerationMode = BidGenerationMode.DETERMINISTIC_FALLBACK


class GoalDecomposer:
    def __init__(self) -> None:
        self.last_plan_source: str = "heuristic"
        self.last_plan_reason: str = "heuristic_only"
        self.last_candidate_scores: list[dict[str, Any]] = []

    def decompose(
        self,
        objective: str,
        snapshot: RepoSnapshot,
        *,
        spec: MissionSpec | None = None,
        strategy_backend=None,
        on_invocation=None,
    ) -> list[TaskNode]:
        heuristic = DecompositionCandidate(
            source="heuristic",
            tasks=self._heuristic_decompose(objective, snapshot),
            summary="Deterministic fallback task graph.",
        )
        heuristic.score = self._score_candidate(heuristic.tasks, objective)
        candidates = [heuristic]
        provider_candidates = self._provider_candidates(
            objective,
            snapshot,
            spec=spec,
            strategy_backend=strategy_backend,
            on_invocation=on_invocation,
        )
        candidates.extend(provider_candidates)
        best = max(candidates, key=lambda item: item.score)
        self.last_plan_source = best.source
        self.last_plan_reason = (
            "provider_selected" if best.source != "heuristic" else "provider_unavailable_or_outscored"
        )
        self.last_candidate_scores = [
            {
                "source": item.source,
                "provider": item.provider,
                "lane": item.lane,
                "score": item.score,
                "summary": item.summary,
                "generation_mode": item.generation_mode.value,
            }
            for item in sorted(candidates, key=lambda item: item.score, reverse=True)
        ]
        return best.tasks

    def _provider_candidates(
        self,
        objective: str,
        snapshot: RepoSnapshot,
        *,
        spec: MissionSpec | None,
        strategy_backend,
        on_invocation,
    ) -> list[DecompositionCandidate]:
        if not (
            strategy_backend
            and hasattr(strategy_backend, "router")
            and hasattr(strategy_backend.router, "config")
            and getattr(strategy_backend.router.config, "enabled_providers", None)
        ):
            return []
        providers = list(strategy_backend.router.config.enabled_providers)
        system_prompt = (
            "You are Arbiter's mission planner. Return only valid JSON with fields: summary and tasks. "
            "Each task must contain title, task_type, requirement_level, dependencies, candidate_files, "
            "validator_requirements, strategy_families, acceptance_criteria, risk_level, runtime_class, "
            "search_depth, and monte_carlo_samples. Keep the plan bounded: Arbiter will choose the task graph, "
            "models will later execute only bounded work units."
        )
        snapshot_summary = "\n".join(snapshot.tree_summary[:20])
        user_prompt = (
            f"Objective: {objective}\n"
            f"Runtime: {snapshot.capabilities.runtime}\n"
            f"Failure signals: {snapshot.failure_signals[:8]}\n"
            f"Changed files: {snapshot.changed_files[:8]}\n"
            f"Complexity hotspots: {snapshot.complexity_hotspots[:8]}\n"
            f"Risky paths: {snapshot.capabilities.risky_paths[:8]}\n"
            f"Protected paths: {(spec.protected_paths if spec else [])[:8]}\n"
            f"Public API surface: {(spec.public_api_surface if spec else [])[:8]}\n"
            f"Constraints: {(spec.constraints if spec else [])[:8]}\n"
            f"Preferences: {(spec.preferences if spec else [])[:8]}\n"
            f"Repository summary:\n{snapshot_summary}\n"
            "Return JSON only."
        )

        def run_provider(provider: str) -> DecompositionCandidate | None:
            lane = f"triage.{provider}"
            lane_config = strategy_backend.router.config.model_lanes.get(lane) or strategy_backend.router.config.model_lanes["triage"]
            invocation_id = uuid4().hex
            started_at = utc_now().isoformat()
            if on_invocation:
                on_invocation(
                    {
                        "invocation_id": invocation_id,
                        "provider": provider,
                        "lane": lane,
                        "model_id": lane_config.model_id,
                        "invocation_kind": "mission_planning",
                        "generation_mode": strategy_backend.market_generation_mode(),
                        "status": "started",
                        "started_at": started_at,
                        "prompt_preview": user_prompt[:1200],
                    }
                )
            try:
                result = strategy_backend.router.invoke(
                    lane=lane,
                    prompt={"system": system_prompt, "user": user_prompt},
                )
                tasks, summary = self._parse_provider_plan(
                    result.content,
                    snapshot=snapshot,
                    objective=objective,
                )
                if not tasks:
                    return None
                if on_invocation:
                    on_invocation(
                        {
                            "invocation_id": invocation_id,
                            "provider": result.provider or provider,
                            "lane": result.lane or lane,
                            "model_id": result.model_id or lane_config.model_id,
                            "invocation_kind": "mission_planning",
                            "generation_mode": result.generation_mode,
                            "status": "completed",
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
                candidate = DecompositionCandidate(
                    source="provider_plan",
                    tasks=tasks,
                    summary=summary,
                    provider=result.provider or provider,
                    lane=result.lane or lane,
                    model_id=result.model_id or lane_config.model_id,
                    invocation_id=invocation_id,
                    generation_mode=result.generation_mode,
                )
                candidate.score = self._score_candidate(candidate.tasks, objective) + 0.15
                return candidate
            except Exception as exc:
                if on_invocation:
                    on_invocation(
                        {
                            "invocation_id": invocation_id,
                            "provider": provider,
                            "lane": lane,
                            "model_id": lane_config.model_id,
                            "invocation_kind": "mission_planning",
                            "generation_mode": strategy_backend.market_generation_mode(),
                            "status": "failed",
                            "started_at": started_at,
                            "completed_at": utc_now().isoformat(),
                            "prompt_preview": user_prompt[:1200],
                            "error": str(exc),
                        }
                    )
                return None

        worker_count = max(1, min(len(providers), 4))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(run_provider, provider) for provider in providers]
        return [candidate for candidate in (future.result() for future in futures) if candidate is not None]

    def _heuristic_decompose(self, objective: str, snapshot: RepoSnapshot) -> list[TaskNode]:
        objective_lower = objective.lower()
        tasks: list[TaskNode] = []
        is_bugfix = any(word in objective_lower for word in ("bug", "fail", "test", "error", "fix"))
        wants_refactor = any(
            word in objective_lower for word in ("refactor", "cleanup", "maintain", "structure", "architecture")
        )
        wants_perf = any(word in objective_lower for word in ("perf", "performance", "slow", "latency", "speed"))

        candidate_files = self._candidate_files(snapshot)

        if is_bugfix or wants_perf:
            tasks.append(
                self._build_task(
                    task_id="T1_localize",
                    title="Localize the likely root cause",
                    task_type=TaskType.LOCALIZE,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=[],
                    candidate_files=candidate_files,
                    risk_level=0.22,
                    runtime_class="small",
                    search_depth=2,
                    monte_carlo_samples=20,
                )
            )

        if is_bugfix:
            tasks.append(
                self._build_task(
                    task_id="T2_bugfix",
                    title="Implement the safest validated bug fix",
                    task_type=TaskType.BUGFIX,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T1_localize"] if tasks else [],
                    candidate_files=candidate_files,
                    risk_level=0.4,
                    runtime_class="medium",
                    search_depth=3,
                    monte_carlo_samples=32,
                )
            )
            tasks.append(
                self._build_task(
                    task_id="T3_regression_tests",
                    title="Add or update regression coverage",
                    task_type=TaskType.TEST,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T2_bugfix"],
                    candidate_files=[path for path in candidate_files if "test" in path or "spec" in path] or candidate_files,
                    risk_level=0.26,
                    runtime_class="small",
                    search_depth=2,
                    monte_carlo_samples=24,
                )
            )

        if wants_refactor:
            tasks.append(
                self._build_task(
                    task_id="T4_refactor",
                    title="Refactor maintainability hotspots without changing declared API",
                    task_type=TaskType.REFACTOR,
                    requirement_level=TaskRequirementLevel.OPTIONAL if is_bugfix else TaskRequirementLevel.REQUIRED,
                    dependencies=["T3_regression_tests"] if is_bugfix else [],
                    candidate_files=candidate_files,
                    risk_level=0.48,
                    runtime_class="medium",
                    search_depth=3,
                    monte_carlo_samples=28,
                )
            )

        if wants_perf:
            tasks.append(
                self._build_task(
                    task_id="T5_perf_diagnosis",
                    title="Diagnose the performance bottleneck",
                    task_type=TaskType.PERF_DIAGNOSIS,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T1_localize"] if tasks else [],
                    candidate_files=candidate_files,
                    risk_level=0.3,
                    runtime_class="small",
                    search_depth=3,
                    monte_carlo_samples=28,
                )
            )
            tasks.append(
                self._build_task(
                    task_id="T6_perf_optimize",
                    title="Implement a benchmark-valid optimization",
                    task_type=TaskType.PERF_OPTIMIZE,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T5_perf_diagnosis"],
                    candidate_files=candidate_files,
                    risk_level=0.52,
                    runtime_class="large",
                    search_depth=4,
                    monte_carlo_samples=40,
                )
            )

        tasks.append(
            self._build_task(
                task_id="T7_validate",
                title="Run final required validators",
                task_type=TaskType.VALIDATE,
                requirement_level=TaskRequirementLevel.REQUIRED,
                dependencies=[
                    task.task_id
                    for task in tasks
                    if task.requirement_level == TaskRequirementLevel.REQUIRED and task.task_id != "T7_validate"
                ],
                candidate_files=candidate_files,
                risk_level=0.2,
                runtime_class="small",
                search_depth=1,
                monte_carlo_samples=16,
            )
        )
        return tasks

    def _parse_provider_plan(
        self,
        text: str,
        *,
        snapshot: RepoSnapshot,
        objective: str,
    ) -> tuple[list[TaskNode], str]:
        data = self._parse_json_payload(text)
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            return [], ""
        normalized: list[TaskNode] = []
        raw_to_id: dict[str, str] = {}
        for index, raw_task in enumerate(raw_tasks, start=1):
            if not isinstance(raw_task, dict):
                continue
            task_type = self._parse_task_type(raw_task.get("task_type"))
            if task_type is None:
                continue
            raw_identifier = str(raw_task.get("id") or raw_task.get("title") or task_type.value)
            task_id = f"T{index}_{self._slug(raw_identifier or task_type.value)}"
            raw_to_id[raw_identifier] = task_id
            dependencies = []
            for dependency in raw_task.get("dependencies", []):
                dependency_id = raw_to_id.get(str(dependency))
                if dependency_id and dependency_id != task_id:
                    dependencies.append(dependency_id)
            candidate_files = self._normalize_candidate_paths(snapshot, raw_task.get("candidate_files")) or self._candidate_files(snapshot)
            normalized.append(
                self._build_task(
                    task_id=task_id,
                    title=str(raw_task.get("title") or task_type.value.replace("_", " ").title()),
                    task_type=task_type,
                    requirement_level=self._parse_requirement_level(raw_task.get("requirement_level")),
                    dependencies=list(dict.fromkeys(dependencies)),
                    candidate_files=candidate_files,
                    risk_level=self._clamp_float(raw_task.get("risk_level"), default=0.35),
                    runtime_class=self._parse_runtime_class(raw_task.get("runtime_class")),
                    search_depth=self._clamp_int(raw_task.get("search_depth"), default=2, minimum=1, maximum=5),
                    monte_carlo_samples=self._clamp_int(
                        raw_task.get("monte_carlo_samples"),
                        default=24,
                        minimum=12,
                        maximum=96,
                    ),
                    validator_requirements=self._string_list(raw_task.get("validator_requirements")),
                    strategy_families=self._string_list(raw_task.get("strategy_families")),
                    acceptance_criteria=self._string_list(raw_task.get("acceptance_criteria")),
                )
            )
        if not normalized:
            return [], ""
        if not any(task.task_type == TaskType.VALIDATE for task in normalized):
            normalized.append(
                self._build_task(
                    task_id=f"T{len(normalized) + 1}_validate",
                    title="Run final required validators",
                    task_type=TaskType.VALIDATE,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=[task.task_id for task in normalized if task.required],
                    candidate_files=self._candidate_files(snapshot),
                    risk_level=0.2,
                    runtime_class="small",
                    search_depth=1,
                    monte_carlo_samples=16,
                )
            )
        summary = str(data.get("summary") or f"Provider-generated task graph for {objective}.")
        return normalized, summary

    def _build_task(
        self,
        *,
        task_id: str,
        title: str,
        task_type: TaskType,
        requirement_level: TaskRequirementLevel,
        dependencies: list[str],
        candidate_files: list[str],
        risk_level: float,
        runtime_class: str,
        search_depth: int,
        monte_carlo_samples: int,
        validator_requirements: list[str] | None = None,
        strategy_families: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> TaskNode:
        defaults = self._task_defaults(task_type)
        validator_requirements = list(dict.fromkeys(validator_requirements or defaults["validator_requirements"]))
        acceptance_criteria = list(dict.fromkeys(acceptance_criteria or defaults["acceptance_criteria"]))
        return TaskNode(
            task_id=task_id,
            title=title,
            task_type=task_type,
            requirement_level=requirement_level,
            dependencies=list(dict.fromkeys(dependencies)),
            success_criteria=SuccessCriteria(
                description=defaults["success_description"],
                required_validators=validator_requirements,
                required_signals=list(defaults["required_signals"]),
                acceptance_checks=acceptance_criteria,
            ),
            allowed_tools=list(defaults["allowed_tools"]),
            rollback_conditions=list(defaults["rollback_conditions"]),
            validator_requirements=validator_requirements,
            risk_level=max(0.05, min(0.95, risk_level)),
            runtime_class=runtime_class,
            search_depth=search_depth,
            monte_carlo_samples=monte_carlo_samples,
            candidate_files=list(dict.fromkeys(candidate_files))[:8],
            policy_constraints=list(defaults["policy_constraints"]),
            strategy_families=list(dict.fromkeys(strategy_families or defaults["strategy_families"])),
            acceptance_criteria=acceptance_criteria,
            status=TaskStatus.READY if not dependencies else TaskStatus.PENDING,
        )

    def _task_defaults(self, task_type: TaskType) -> dict[str, Any]:
        defaults = {
            TaskType.LOCALIZE: {
                "success_description": "Likely failure area is isolated from evidence.",
                "required_signals": ["candidate_files"],
                "allowed_tools": ["read_file", "search_code"],
                "rollback_conditions": [],
                "validator_requirements": [],
                "policy_constraints": ["repo_only"],
                "strategy_families": ["Speed", "Safe", "Quality", "Test"],
                "acceptance_criteria": ["candidate files identified", "protected boundaries recognized"],
            },
            TaskType.BUGFIX: {
                "success_description": "Fix lands without new failures.",
                "required_signals": [],
                "allowed_tools": ["read_file", "search_code", "edit_file", "run_tests", "revert_to_checkpoint"],
                "rollback_conditions": ["regression", "scope_drift"],
                "validator_requirements": ["tests"],
                "policy_constraints": ["protected_paths_respected", "public_api_guard"],
                "strategy_families": ["Safe", "Quality", "Test", "Speed"],
                "acceptance_criteria": ["tests pass", "no public api drift"],
            },
            TaskType.TEST: {
                "success_description": "Regression coverage exists for the fixed behavior.",
                "required_signals": [],
                "allowed_tools": ["read_file", "search_code", "edit_file", "run_tests"],
                "rollback_conditions": ["test_breakage"],
                "validator_requirements": ["tests"],
                "policy_constraints": ["file_scope_bounded"],
                "strategy_families": ["Test", "Quality", "Safe"],
                "acceptance_criteria": ["regression test present"],
            },
            TaskType.REFACTOR: {
                "success_description": "Structure improves without declared public API changes.",
                "required_signals": [],
                "allowed_tools": ["read_file", "search_code", "edit_file", "run_tests", "run_lint", "revert_to_checkpoint"],
                "rollback_conditions": ["public_api_change", "regression"],
                "validator_requirements": ["tests", "lint"],
                "policy_constraints": ["public_api_guard"],
                "strategy_families": ["Quality", "Safe"],
                "acceptance_criteria": ["tests pass", "lint pass", "public api stable"],
            },
            TaskType.PERF_DIAGNOSIS: {
                "success_description": "A benchmark-backed optimization target is identified.",
                "required_signals": [],
                "allowed_tools": ["read_file", "search_code", "benchmark"],
                "rollback_conditions": [],
                "validator_requirements": ["benchmark"],
                "policy_constraints": ["benchmark_required"],
                "strategy_families": ["Performance", "Safe"],
                "acceptance_criteria": ["benchmark command available"],
            },
            TaskType.PERF_OPTIMIZE: {
                "success_description": "Measured performance improves without breaking validators.",
                "required_signals": [],
                "allowed_tools": ["read_file", "search_code", "edit_file", "benchmark", "run_tests", "revert_to_checkpoint"],
                "rollback_conditions": ["benchmark_regression", "regression"],
                "validator_requirements": ["benchmark", "tests"],
                "policy_constraints": ["benchmark_required", "rollback_ready"],
                "strategy_families": ["Performance", "Safe", "Quality"],
                "acceptance_criteria": ["benchmark improves", "tests pass"],
            },
            TaskType.VALIDATE: {
                "success_description": "All required validators pass for the accepted diff.",
                "required_signals": [],
                "allowed_tools": ["run_tests", "run_lint", "static_analysis", "benchmark"],
                "rollback_conditions": ["validation_failure", "policy_block"],
                "validator_requirements": ["tests", "lint", "static"],
                "policy_constraints": ["guardrails_green"],
                "strategy_families": ["Safe", "Test", "Quality"],
                "acceptance_criteria": ["all validators pass", "guardrails clear"],
            },
        }
        return defaults[task_type]

    def _score_candidate(self, tasks: list[TaskNode], objective: str) -> float:
        if not tasks:
            return -1.0
        objective_lower = objective.lower()
        has_bugfix = any(task.task_type == TaskType.BUGFIX for task in tasks)
        has_tests = any(task.task_type == TaskType.TEST for task in tasks)
        has_refactor = any(task.task_type == TaskType.REFACTOR for task in tasks)
        has_perf = any(task.task_type in {TaskType.PERF_DIAGNOSIS, TaskType.PERF_OPTIMIZE} for task in tasks)
        has_validate = any(task.task_type == TaskType.VALIDATE for task in tasks)
        objective_alignment = 0.0
        if any(word in objective_lower for word in ("bug", "fail", "test", "error", "fix")):
            objective_alignment += 0.9 if has_bugfix else -0.45
            objective_alignment += 0.35 if has_tests else -0.2
        if any(word in objective_lower for word in ("refactor", "cleanup", "maintain", "structure", "architecture")):
            objective_alignment += 0.45 if has_refactor else -0.15
        if any(word in objective_lower for word in ("perf", "performance", "slow", "latency", "speed")):
            objective_alignment += 0.75 if has_perf else -0.35
        objective_alignment += 0.25 if has_validate else -0.25
        file_grounding = sum(1 for task in tasks if task.candidate_files) / len(tasks)
        search_strength = sum(task.search_depth + task.monte_carlo_samples / 24 for task in tasks) / len(tasks)
        validator_density = sum(len(task.validator_requirements) for task in tasks) / len(tasks)
        dependency_health = (
            sum(1 for index, task in enumerate(tasks) if set(task.dependencies).issubset({item.task_id for item in tasks[:index]}))
            / len(tasks)
        )
        avg_risk = sum(task.risk_level for task in tasks) / len(tasks)
        return round(
            objective_alignment
            + 0.45 * file_grounding
            + 0.18 * search_strength
            + 0.12 * validator_density
            + 0.25 * dependency_health
            - 0.25 * avg_risk,
            4,
        )

    def _candidate_files(self, snapshot: RepoSnapshot) -> list[str]:
        evidence_files = self._failure_evidence_files(snapshot)
        candidate_files = evidence_files + snapshot.changed_files[:3] + snapshot.complexity_hotspots[:3]
        candidate_files = list(dict.fromkeys(candidate_files + snapshot.capabilities.risky_paths[:2]))
        return candidate_files

    @staticmethod
    def _failure_evidence_files(snapshot: RepoSnapshot) -> list[str]:
        candidates: list[str] = []
        file_pattern = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:py|js|jsx|ts|tsx))")
        repo = Path(snapshot.repo_path)
        baseline_results = [
            *snapshot.initial_test_results,
            *snapshot.initial_lint_results,
            *snapshot.initial_static_results,
        ]
        for result in baseline_results:
            text = f"{result.stdout}\n{result.stderr}"
            for match in file_pattern.findall(text):
                normalized = GoalDecomposer._normalize_candidate_path(repo, match)
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        for failure_signal in snapshot.failure_signals:
            for match in file_pattern.findall(failure_signal):
                normalized = GoalDecomposer._normalize_candidate_path(repo, match)
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        return candidates[:8]

    def _normalize_candidate_paths(self, snapshot: RepoSnapshot, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        repo = Path(snapshot.repo_path)
        normalized: list[str] = []
        for raw_path in values:
            if not isinstance(raw_path, str):
                continue
            candidate = self._normalize_candidate_path(repo, raw_path)
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @staticmethod
    def _normalize_candidate_path(repo: Path, raw_path: str) -> str | None:
        candidate = Path(raw_path.replace("\\", "/"))
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(repo.resolve()).as_posix()
            except ValueError:
                return None
        normalized = candidate.as_posix().lstrip("./")
        repo_candidate = (repo / normalized).resolve()
        if repo_candidate.exists() and repo_candidate.is_file():
            return repo_candidate.relative_to(repo.resolve()).as_posix()
        return None

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            chunks = cleaned.split("```")
            cleaned = chunks[-2].replace("json", "", 1).strip() if len(chunks) >= 3 else cleaned
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug or "task"

    @staticmethod
    def _parse_task_type(value: Any) -> TaskType | None:
        try:
            return TaskType(str(value))
        except Exception:
            return None

    @staticmethod
    def _parse_requirement_level(value: Any) -> TaskRequirementLevel:
        try:
            return TaskRequirementLevel(str(value))
        except Exception:
            return TaskRequirementLevel.REQUIRED

    @staticmethod
    def _parse_runtime_class(value: Any) -> str:
        runtime_class = str(value or "small")
        return runtime_class if runtime_class in {"small", "medium", "large"} else "small"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _clamp_float(value: Any, *, default: float) -> float:
        try:
            return max(0.05, min(0.95, float(value)))
        except Exception:
            return default

    @staticmethod
    def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(minimum, min(maximum, parsed))
