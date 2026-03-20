from __future__ import annotations

import re
import subprocess
from pathlib import Path

from arbiter.core.contracts import CommandResult
from arbiter.repo.collector import _run


class LocalToolset:
    def __init__(self, worktree_path: str) -> None:
        self.worktree = Path(worktree_path).resolve()

    def read_file(self, relative_path: str) -> str:
        return (self.worktree / relative_path).read_text(encoding="utf-8", errors="ignore")

    def search_code(self, pattern: str) -> list[str]:
        matches: list[str] = []
        regex = re.compile(pattern)
        for path in self.worktree.rglob("*"):
            if path.is_dir() or ".git" in path.parts or ".arbiter" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if regex.search(text):
                matches.append(str(path.relative_to(self.worktree)))
        return matches[:50]

    def edit_file(self, relative_path: str, content: str) -> str:
        path = self.worktree / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return relative_path

    def apply_file_updates(self, updates: dict[str, str]) -> list[str]:
        return [self.edit_file(relative_path, content) for relative_path, content in updates.items()]

    def _run_tool(self, command: list[str], timeout: int = 300) -> CommandResult:
        return _run(command, cwd=str(self.worktree), timeout=timeout)

    def run_tests(self, command: list[str]) -> CommandResult:
        return self._run_tool(command)

    def run_lint(self, command: list[str]) -> CommandResult:
        return self._run_tool(command)

    def static_analysis(self, command: list[str]) -> CommandResult:
        return self._run_tool(command)

    def benchmark(self, command: list[str]) -> tuple[CommandResult, float | None]:
        result = self._run_tool(command)
        text = result.stdout + "\n" + result.stderr
        match = re.search(r"(?:BENCHMARK_METRIC|TIME_MS|SCORE)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text)
        return result, float(match.group(1)) if match else None

    def git_head(self) -> str:
        return self._run_tool(["git", "rev-parse", "HEAD"]).stdout.strip()

    def changed_files(self) -> list[str]:
        result = self._run_tool(["git", "status", "--porcelain"])
        return [line[3:] for line in result.stdout.splitlines() if line]

    def diff(self) -> str:
        return self._run_tool(["git", "diff", "--stat"]).stdout

    def revert_to_checkpoint(self, commit_sha: str) -> None:
        subprocess.run(["git", "reset", "--hard", commit_sha], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fd"], cwd=str(self.worktree), check=True, capture_output=True, text=True)

    def commit(self, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        return self.git_head()

    # Compatibility wrappers for older call sites while the runner is being upgraded.
    def search(self, pattern: str) -> list[str]:
        return self.search_code(pattern)

    def run_command(self, command: list[str], timeout: int = 300) -> CommandResult:
        return self._run_tool(command, timeout=timeout)

    def benchmark_metric(self, command: list[str]) -> tuple[CommandResult, float | None]:
        return self.benchmark(command)
