from __future__ import annotations

import re
from pathlib import Path

from arbiter.core.contracts import RepoSnapshot, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskStatus, TaskType


class GoalDecomposer:
    def decompose(self, objective: str, snapshot: RepoSnapshot) -> list[TaskNode]:
        objective_lower = objective.lower()
        tasks: list[TaskNode] = []
        is_bugfix = any(word in objective_lower for word in ("bug", "fail", "test", "error", "fix"))
        wants_refactor = any(word in objective_lower for word in ("refactor", "cleanup", "maintain", "structure", "architecture"))
        wants_perf = any(word in objective_lower for word in ("perf", "performance", "slow", "latency", "speed"))

        candidate_files = self._candidate_files(snapshot)

        if is_bugfix or wants_perf:
            tasks.append(
                TaskNode(
                    task_id="T1_localize",
                    title="Localize the likely root cause",
                    task_type=TaskType.LOCALIZE,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    success_criteria=SuccessCriteria(
                        description="Likely failure area is isolated from evidence.",
                        required_signals=["candidate_files"],
                        acceptance_checks=["root_cause_evidence"],
                    ),
                    allowed_tools=["read_file", "search_code"],
                    rollback_conditions=[],
                    validator_requirements=[],
                    candidate_files=candidate_files,
                    policy_constraints=["repo_only"],
                    strategy_families=["Speed", "Safe", "Quality", "Test"],
                    acceptance_criteria=["candidate files identified", "protected boundaries recognized"],
                    status=TaskStatus.READY,
                )
            )

        if is_bugfix:
            tasks.append(
                TaskNode(
                    task_id="T2_bugfix",
                    title="Implement the safest validated bug fix",
                    task_type=TaskType.BUGFIX,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T1_localize"] if tasks else [],
                    success_criteria=SuccessCriteria(
                        description="Fix lands without new failures.",
                        required_validators=["tests"],
                        acceptance_checks=["tests_pass", "policy_conformant"],
                    ),
                    allowed_tools=["read_file", "search_code", "edit_file", "run_tests", "revert_to_checkpoint"],
                    rollback_conditions=["regression", "scope_drift"],
                    validator_requirements=["tests"],
                    candidate_files=candidate_files,
                    policy_constraints=["protected_paths_respected", "public_api_guard"],
                    strategy_families=["Safe", "Quality", "Test", "Speed"],
                    acceptance_criteria=["tests pass", "no public api drift"],
                )
            )
            tasks.append(
                TaskNode(
                    task_id="T3_regression_tests",
                    title="Add or update regression coverage",
                    task_type=TaskType.TEST,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T2_bugfix"],
                    success_criteria=SuccessCriteria(
                        description="Regression coverage exists for the fixed behavior.",
                        required_validators=["tests"],
                        acceptance_checks=["coverage_added"],
                    ),
                    allowed_tools=["read_file", "search_code", "edit_file", "run_tests"],
                    rollback_conditions=["test_breakage"],
                    validator_requirements=["tests"],
                    candidate_files=[path for path in candidate_files if "test" in path or "spec" in path] or candidate_files,
                    policy_constraints=["file_scope_bounded"],
                    strategy_families=["Test", "Quality", "Safe"],
                    acceptance_criteria=["regression test present"],
                )
            )

        if wants_refactor:
            tasks.append(
                TaskNode(
                    task_id="T4_refactor",
                    title="Refactor maintainability hotspots without changing declared API",
                    task_type=TaskType.REFACTOR,
                    requirement_level=TaskRequirementLevel.OPTIONAL if is_bugfix else TaskRequirementLevel.REQUIRED,
                    dependencies=["T3_regression_tests"] if is_bugfix else [],
                    success_criteria=SuccessCriteria(
                        description="Structure improves without declared public API changes.",
                        required_validators=["tests", "lint"],
                        acceptance_checks=["tests_pass", "lint_pass"],
                    ),
                    allowed_tools=["read_file", "search_code", "edit_file", "run_tests", "run_lint", "revert_to_checkpoint"],
                    rollback_conditions=["public_api_change", "regression"],
                    validator_requirements=["tests", "lint"],
                    candidate_files=candidate_files,
                    policy_constraints=["public_api_guard"],
                    strategy_families=["Quality", "Safe"],
                    acceptance_criteria=["tests pass", "lint pass", "public api stable"],
                )
            )

        if wants_perf:
            tasks.append(
                TaskNode(
                    task_id="T5_perf_diagnosis",
                    title="Diagnose the performance bottleneck",
                    task_type=TaskType.PERF_DIAGNOSIS,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T1_localize"] if is_bugfix or wants_perf else [],
                    success_criteria=SuccessCriteria(
                        description="A benchmark-backed optimization target is identified.",
                        required_validators=["benchmark"],
                        acceptance_checks=["benchmark_target_identified"],
                    ),
                    allowed_tools=["read_file", "search_code", "benchmark"],
                    rollback_conditions=[],
                    validator_requirements=["benchmark"],
                    candidate_files=candidate_files,
                    policy_constraints=["benchmark_required"],
                    strategy_families=["Performance", "Safe"],
                    acceptance_criteria=["benchmark command available"],
                )
            )
            tasks.append(
                TaskNode(
                    task_id="T6_perf_optimize",
                    title="Implement a benchmark-valid optimization",
                    task_type=TaskType.PERF_OPTIMIZE,
                    requirement_level=TaskRequirementLevel.REQUIRED,
                    dependencies=["T5_perf_diagnosis"],
                    success_criteria=SuccessCriteria(
                        description="Measured performance improves without breaking validators.",
                        required_validators=["benchmark", "tests"],
                        acceptance_checks=["benchmark_improves", "tests_pass"],
                    ),
                    allowed_tools=["read_file", "search_code", "edit_file", "benchmark", "run_tests", "revert_to_checkpoint"],
                    rollback_conditions=["benchmark_regression", "regression"],
                    validator_requirements=["benchmark", "tests"],
                    candidate_files=candidate_files,
                    policy_constraints=["benchmark_required", "rollback_ready"],
                    strategy_families=["Performance", "Safe", "Quality"],
                    acceptance_criteria=["benchmark improves", "tests pass"],
                )
            )

        tasks.append(
            TaskNode(
                task_id="T7_validate",
                title="Run final required validators",
                task_type=TaskType.VALIDATE,
                requirement_level=TaskRequirementLevel.REQUIRED,
                dependencies=[task.task_id for task in tasks if task.requirement_level == TaskRequirementLevel.REQUIRED and task.task_id != "T7_validate"],
                success_criteria=SuccessCriteria(
                    description="All required validators pass for the accepted diff.",
                    required_validators=["tests", "lint", "static"],
                    acceptance_checks=["validators_green"],
                ),
                allowed_tools=["run_tests", "run_lint", "static_analysis", "benchmark"],
                rollback_conditions=["validation_failure", "policy_block"],
                validator_requirements=["tests", "lint", "static"],
                candidate_files=candidate_files,
                policy_constraints=["guardrails_green"],
                strategy_families=["Safe", "Test", "Quality"],
                acceptance_criteria=["all validators pass", "guardrails clear"],
            )
        )
        return tasks

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
