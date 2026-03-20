from __future__ import annotations

from arbiter.core.contracts import MissionSpec, RepoSnapshot, TaskNode, ValidationReport
from arbiter.tools.local import LocalToolset


class ValidationEngine:
    def __init__(self, toolset: LocalToolset, spec: MissionSpec, snapshot: RepoSnapshot) -> None:
        self.toolset = toolset
        self.spec = spec
        self.snapshot = snapshot

    def validate(self, task: TaskNode) -> ValidationReport:
        results = []
        notes: list[str] = []
        validator_deltas: list[str] = []
        commands = []
        commands.extend(self.snapshot.capabilities.test_commands)
        commands.extend(self.snapshot.capabilities.lint_commands)
        commands.extend(self.snapshot.capabilities.static_commands)
        benchmark_delta = None

        if task.task_type.value.startswith("perf"):
            if not self.snapshot.capabilities.benchmark_commands and not self.spec.benchmark_requirement:
                return ValidationReport(
                    task_id=task.task_id,
                    passed=False,
                    notes=["Performance claims require a benchmark command or explicit benchmark requirement."],
                    policy_conformance=False,
                )
            if self.snapshot.capabilities.benchmark_commands:
                result, benchmark_delta = self.toolset.benchmark(self.snapshot.capabilities.benchmark_commands[0])
                results.append(result)
                if benchmark_delta is None:
                    notes.append("Benchmark output did not expose a parseable metric.")

        for command in commands:
            if "pytest" in " ".join(command) or " test" in " ".join(command):
                results.append(self.toolset.run_tests(command))
            elif "ruff" in " ".join(command) or "lint" in " ".join(command) or "eslint" in " ".join(command):
                results.append(self.toolset.run_lint(command))
            else:
                results.append(self.toolset.static_analysis(command))
        changed_files = self.toolset.changed_files()
        file_churn = len(changed_files)
        api_guard_passed = self._check_api_guard(changed_files)
        if not api_guard_passed:
            notes.append("Public API surface or protected paths changed.")
            validator_deltas.append("api_guard_failed")
        if task.task_type.value in {"bugfix", "test"} and self.spec.risk_policy.require_tests_for_bugfix and not self.snapshot.capabilities.test_commands:
            notes.append("No test command available for bugfix validation.")
            validator_deltas.append("missing_test_command")

        passed = (
            all(result.exit_code == 0 for result in results)
            and api_guard_passed
            and file_churn <= self.spec.stop_policy.max_file_churn
            and not any(note.startswith("No test command") for note in notes)
        )
        for result in results:
            if result.exit_code != 0:
                validator_deltas.append(result.stderr or result.stdout or "validator_failed")
        if file_churn > self.spec.stop_policy.max_file_churn:
            notes.append(f"File churn {file_churn} exceeded max {self.spec.stop_policy.max_file_churn}.")
            validator_deltas.append("file_churn_exceeded")
        return ValidationReport(
            task_id=task.task_id,
            passed=passed,
            command_results=results,
            file_churn=file_churn,
            changed_files=changed_files,
            api_guard_passed=api_guard_passed,
            benchmark_delta=benchmark_delta,
            notes=notes,
            policy_conformance=api_guard_passed and file_churn <= self.spec.stop_policy.max_file_churn,
            validator_deltas=validator_deltas[:10],
        )

    def _check_api_guard(self, changed_files: list[str]) -> bool:
        protected = set(self.spec.protected_paths + self.spec.public_api_surface)
        if not protected:
            return True
        return not any(changed in protected for changed in changed_files)
