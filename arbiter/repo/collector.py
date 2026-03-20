from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from arbiter.core.contracts import CapabilitySet, CommandResult, RepoSnapshot


def _run(command: list[str], cwd: str, timeout: int = 120) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout[-8000:],
        stderr=completed.stderr[-8000:],
        duration_seconds=0.0,
    )


def _iter_source_files(repo: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {".git", ".arbiter", ".venv", "node_modules", "__pycache__"}]
        for file_name in files:
            path = Path(root) / file_name
            if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                yield path


class RepoStateCollector:
    def __init__(self, repo_path: str) -> None:
        self.repo = Path(repo_path).resolve()

    def collect(self, run_commands: bool = True) -> RepoSnapshot:
        capabilities = self._detect_capabilities()
        branch = self._git(["branch", "--show-current"]).stdout.strip() or None
        head = self._git(["rev-parse", "HEAD"]).stdout.strip() or None
        status = self._git(["status", "--porcelain"]).stdout.splitlines()
        changed_files = [line[3:] for line in status if line and not line.startswith("??")]
        untracked = [line[3:] for line in status if line.startswith("??")]
        snapshot = RepoSnapshot(
            repo_path=str(self.repo),
            branch=branch,
            head_commit=head,
            dirty=bool(status),
            changed_files=changed_files,
            untracked_files=untracked,
            tree_summary=self._tree_summary(),
            dependency_files=self._dependency_files(),
            complexity_hotspots=self._complexity_hotspots(),
            failure_signals=self._failure_signals(),
            capabilities=capabilities,
            initial_test_results=self._run_commands(capabilities.test_commands, run_commands),
            initial_lint_results=self._run_commands(capabilities.lint_commands, run_commands),
            initial_static_results=self._run_commands(capabilities.static_commands, run_commands),
        )
        return snapshot

    def _git(self, args: list[str]) -> CommandResult:
        return _run(["git", *args], cwd=str(self.repo))

    def _dependency_files(self) -> list[str]:
        candidates = ["pyproject.toml", "requirements.txt", "setup.py", "package.json", "package-lock.json", "pnpm-lock.yaml"]
        return [name for name in candidates if (self.repo / name).exists()]

    def _tree_summary(self) -> list[str]:
        paths = []
        for path in sorted(self.repo.iterdir()):
            if path.name in {".git", ".arbiter", ".venv", "node_modules"}:
                continue
            paths.append(path.name)
        return paths[:50]

    def _complexity_hotspots(self) -> list[str]:
        scored: list[tuple[int, str]] = []
        for path in _iter_source_files(self.repo):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
            except OSError:
                continue
            scored.append((lines, str(path.relative_to(self.repo))))
        scored.sort(reverse=True)
        return [name for _, name in scored[:10]]

    def _failure_signals(self) -> list[str]:
        signals: list[str] = []
        for report in ("pytest.log", "test.log", "error.log", "stacktrace.log"):
            path = self.repo / report
            if path.exists():
                signals.append(path.read_text(encoding="utf-8", errors="ignore")[:4000])
        return signals

    def _run_commands(self, commands: list[list[str]], enabled: bool) -> list[CommandResult]:
        if not enabled:
            return []
        results: list[CommandResult] = []
        for command in commands:
            try:
                results.append(_run(command, cwd=str(self.repo)))
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append(
                    CommandResult(
                        command=command,
                        exit_code=1,
                        stdout="",
                        stderr=str(exc),
                        duration_seconds=0.0,
                    )
                )
        return results

    def _detect_capabilities(self) -> CapabilitySet:
        if (self.repo / "pyproject.toml").exists() or any(self.repo.glob("**/conftest.py")):
            return self._detect_python()
        if (self.repo / "package.json").exists():
            return self._detect_tsjs()
        return CapabilitySet(runtime="unsupported", unsupported_reason="No supported project metadata found.")

    def _detect_python(self) -> CapabilitySet:
        test_commands: list[list[str]] = []
        lint_commands: list[list[str]] = []
        static_commands: list[list[str]] = []
        benchmark_commands: list[list[str]] = []
        if (self.repo / "tests").exists() or any(self.repo.glob("test_*.py")):
            test_commands.append(["python", "-m", "pytest"])
        if (self.repo / "ruff.toml").exists() or (self.repo / "pyproject.toml").exists():
            lint_commands.append(["python", "-m", "ruff", "check", "."])
        if (self.repo / "mypy.ini").exists() or (self.repo / "pyproject.toml").exists():
            static_commands.append(["python", "-m", "mypy", "."])
        if (self.repo / "benchmarks").exists():
            benchmark_commands.append(["python", "-m", "pytest", "benchmarks"])
        return CapabilitySet(
            runtime="python",
            test_commands=test_commands,
            lint_commands=lint_commands,
            static_commands=static_commands,
            benchmark_commands=benchmark_commands,
        )

    def _detect_tsjs(self) -> CapabilitySet:
        package_path = self.repo / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if package.get("workspaces") or (self.repo / "pnpm-workspace.yaml").exists():
            return CapabilitySet(
                runtime="unsupported",
                unsupported_reason="TS/JS monorepos are out of scope for V1.",
            )
        scripts = package.get("scripts", {})
        pm = "npm"
        test_commands = [[pm, "run", name] for name in ("test", "test:ci", "unit") if name in scripts][:1]
        lint_commands = [[pm, "run", name] for name in ("lint", "eslint") if name in scripts][:1]
        static_commands = [[pm, "run", name] for name in ("typecheck", "build") if name in scripts][:1]
        benchmark_commands = [[pm, "run", name] for name in ("bench", "benchmark", "perf") if name in scripts][:1]
        return CapabilitySet(
            runtime="tsjs",
            test_commands=test_commands,
            lint_commands=lint_commands,
            static_commands=static_commands,
            benchmark_commands=benchmark_commands,
            is_single_package_tsjs=True,
        )

