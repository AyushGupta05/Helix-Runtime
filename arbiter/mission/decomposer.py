from __future__ import annotations

from arbiter.core.contracts import RepoSnapshot, SuccessCriteria, TaskNode, TaskRequirementLevel, TaskStatus, TaskType


class GoalDecomposer:
    def decompose(self, objective: str, snapshot: RepoSnapshot) -> list[TaskNode]:
        objective_lower = objective.lower()
        tasks: list[TaskNode] = []
        is_bugfix = any(word in objective_lower for word in ("bug", "fail", "test", "error", "fix"))
        wants_refactor = any(word in objective_lower for word in ("refactor", "cleanup", "maintain", "structure", "architecture"))
        wants_perf = any(word in objective_lower for word in ("perf", "performance", "slow", "latency", "speed"))

        candidate_files = snapshot.complexity_hotspots[:3] or snapshot.changed_files[:3]

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
                    ),
                    allowed_tools=["read", "search", "diff"],
                    rollback_conditions=[],
                    validator_requirements=[],
                    candidate_files=candidate_files,
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
                    ),
                    allowed_tools=["read", "search", "edit", "diff", "test", "revert"],
                    rollback_conditions=["regression", "scope_drift"],
                    validator_requirements=["tests"],
                    candidate_files=candidate_files,
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
                    ),
                    allowed_tools=["read", "search", "edit", "test"],
                    rollback_conditions=["test_breakage"],
                    validator_requirements=["tests"],
                    candidate_files=[path for path in candidate_files if "test" in path or "spec" in path] or candidate_files,
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
                    ),
                    allowed_tools=["read", "search", "edit", "diff", "test", "lint", "revert"],
                    rollback_conditions=["public_api_change", "regression"],
                    validator_requirements=["tests", "lint"],
                    candidate_files=candidate_files,
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
                    ),
                    allowed_tools=["read", "search", "benchmark", "diff"],
                    rollback_conditions=[],
                    validator_requirements=["benchmark"],
                    candidate_files=candidate_files,
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
                    ),
                    allowed_tools=["read", "search", "edit", "diff", "benchmark", "test", "revert"],
                    rollback_conditions=["benchmark_regression", "regression"],
                    validator_requirements=["benchmark", "tests"],
                    candidate_files=candidate_files,
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
                ),
                allowed_tools=["test", "lint", "static", "benchmark", "diff"],
                rollback_conditions=["validation_failure"],
                validator_requirements=["tests"],
                candidate_files=candidate_files,
            )
        )
        return tasks

