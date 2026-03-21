from __future__ import annotations

from collections.abc import Iterable

from arbiter.core.contracts import CommandResult, MissionSpec, RepoSnapshot, TaskNode, ValidationReport
from arbiter.tools.local import LocalToolset


class ValidationEngine:
    def __init__(self, toolset: LocalToolset, spec: MissionSpec, snapshot: RepoSnapshot) -> None:
        self.toolset = toolset
        self.spec = spec
        self.snapshot = snapshot

    def validate(self, task: TaskNode) -> ValidationReport:
        results: list[CommandResult] = []
        notes: list[str] = []
        validator_deltas: list[str] = []
        commands: list[list[str]] = []
        commands.extend(self.snapshot.capabilities.test_commands)
        commands.extend(self.snapshot.capabilities.lint_commands)
        commands.extend(self.snapshot.capabilities.static_commands)
        baseline_results = self._baseline_results()
        baseline_by_command = {tuple(result.command): result for result in baseline_results}
        benchmark_delta = None

        if task.task_type.value.startswith("perf"):
            if not self.snapshot.capabilities.benchmark_commands and not self.spec.benchmark_requirement:
                return ValidationReport(
                    task_id=task.task_id,
                    passed=False,
                    baseline_command_results=baseline_results,
                    notes=["Performance claims require a benchmark command or explicit benchmark requirement."],
                    policy_conformance=False,
                )
            if self.snapshot.capabilities.benchmark_commands:
                result, benchmark_delta = self.toolset.benchmark(self.snapshot.capabilities.benchmark_commands[0])
                results.append(result)
                if benchmark_delta is None:
                    notes.append("Benchmark output did not expose a parseable metric.")
                    validator_deltas.append("benchmark_metric_unavailable")

        for command in commands:
            kind = self._validator_kind(command)
            if kind == "tests":
                results.append(self.toolset.run_tests(command))
            elif kind == "lint":
                results.append(self.toolset.run_lint(command))
            else:
                results.append(self.toolset.static_analysis(command))
        changed_files = self.toolset.changed_files()
        file_churn = len(changed_files)
        api_guard_passed = self._check_api_guard(changed_files)
        new_validator_failures = False
        persistent_baseline_failures = False
        test_failures = False
        if not api_guard_passed:
            notes.append("Public API surface or protected paths changed.")
            validator_deltas.append("api_guard_failed")
        if task.task_type.value in {"bugfix", "test"} and self.spec.risk_policy.require_tests_for_bugfix and not self.snapshot.capabilities.test_commands:
            notes.append("No test command available for bugfix validation.")
            validator_deltas.append("missing_test_command")

        for result in results:
            if result.exit_code != 0:
                kind = self._validator_kind(result.command)
                baseline_result = baseline_by_command.get(tuple(result.command))
                if kind == "tests":
                    test_failures = True
                if baseline_result and baseline_result.exit_code != 0:
                    persistent_baseline_failures = True
                    notes.append(
                        f"Baseline {kind} failure persists for {' '.join(result.command)}."
                    )
                    validator_deltas.append(f"baseline_{kind}_failure_persisted")
                else:
                    new_validator_failures = True
                    validator_deltas.append(result.stderr or result.stdout or f"{kind}_validator_failed")
        passed = (
            not new_validator_failures
            and api_guard_passed
            and file_churn <= self.spec.stop_policy.max_file_churn
            and not any(note.startswith("No test command") for note in notes)
        )
        if task.task_type.value in {"bugfix", "test"} and test_failures:
            passed = False
            notes.append("Required test validation still fails for a bugfix/test task.")
            validator_deltas.append("required_tests_failed")
        if task.task_type.value.startswith("perf") and benchmark_delta is None:
            passed = False
        if file_churn > self.spec.stop_policy.max_file_churn:
            notes.append(f"File churn {file_churn} exceeded max {self.spec.stop_policy.max_file_churn}.")
            validator_deltas.append("file_churn_exceeded")
        if persistent_baseline_failures and not new_validator_failures:
            notes.append("Validation passed on a no-regression basis despite pre-existing baseline failures.")
        return ValidationReport(
            task_id=task.task_id,
            passed=passed,
            command_results=results,
            baseline_command_results=baseline_results,
            file_churn=file_churn,
            changed_files=changed_files,
            api_guard_passed=api_guard_passed,
            benchmark_delta=benchmark_delta,
            notes=notes,
            policy_conformance=api_guard_passed and file_churn <= self.spec.stop_policy.max_file_churn,
            validator_deltas=validator_deltas[:10],
        )

    def _baseline_results(self) -> list[CommandResult]:
        results: list[CommandResult] = []
        results.extend(self._iter_results(self.snapshot.initial_test_results))
        results.extend(self._iter_results(self.snapshot.initial_lint_results))
        results.extend(self._iter_results(self.snapshot.initial_static_results))
        return results

    @staticmethod
    def _iter_results(results: Iterable[CommandResult] | None) -> list[CommandResult]:
        return list(results or [])

    @staticmethod
    def _validator_kind(command: list[str]) -> str:
        joined = " ".join(command).lower()
        if "pytest" in joined or " test" in joined:
            return "tests"
        if "ruff" in joined or "lint" in joined or "eslint" in joined:
            return "lint"
        if "mypy" in joined or "typecheck" in joined or "build" in joined:
            return "static"
        if "bench" in joined or "benchmark" in joined or "perf" in joined:
            return "benchmark"
        return "validator"

    def _check_api_guard(self, changed_files: list[str]) -> bool:
        protected = set(self.spec.protected_paths + self.spec.public_api_surface)
        if not protected:
            return True
        return not any(changed in protected for changed in changed_files)
