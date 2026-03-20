from __future__ import annotations

import json
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

    def search(self, pattern: str) -> list[str]:
        matches: list[str] = []
        regex = re.compile(pattern)
        for path in self.worktree.rglob("*"):
            if path.is_dir() or path.parts[-1] in {".git"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if regex.search(text):
                matches.append(str(path.relative_to(self.worktree)))
        return matches[:50]

    def apply_file_updates(self, updates: dict[str, str]) -> list[str]:
        touched: list[str] = []
        for relative_path, content in updates.items():
            path = self.worktree / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            touched.append(relative_path)
        return touched

    def run_command(self, command: list[str], timeout: int = 300) -> CommandResult:
        return _run(command, cwd=str(self.worktree), timeout=timeout)

    def changed_files(self) -> list[str]:
        result = self.run_command(["git", "status", "--porcelain"])
        return [line[3:] for line in result.stdout.splitlines() if line]

    def diff(self) -> str:
        return self.run_command(["git", "diff", "--stat"]).stdout

    def revert_to_commit(self, commit_sha: str) -> None:
        subprocess.run(["git", "reset", "--hard", commit_sha], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fd"], cwd=str(self.worktree), check=True, capture_output=True, text=True)

    def commit(self, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        head = self.run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
        return head

    def benchmark_metric(self, command: list[str]) -> tuple[CommandResult, float | None]:
        result = self.run_command(command)
        text = result.stdout + "\n" + result.stderr
        match = re.search(r"(?:BENCHMARK_METRIC|TIME_MS|SCORE)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text)
        return result, float(match.group(1)) if match else None

