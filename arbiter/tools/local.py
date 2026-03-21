from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from arbiter.core.contracts import CommandResult
from arbiter.repo.collector import IGNORED_DIRECTORIES, _run


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

    @staticmethod
    def _replace_nth(text: str, target: str, replacement: str, occurrence: int) -> str:
        if occurrence < 1:
            raise ValueError("Edit operations use 1-based occurrence indexes.")
        start = 0
        for _ in range(occurrence):
            index = text.find(target, start)
            if index == -1:
                raise ValueError("Edit operation target was not found in file content.")
            start = index + len(target)
        return text[:index] + replacement + text[index + len(target) :]

    def apply_edit_operations(self, operations: list[dict[str, Any]]) -> list[str]:
        touched: list[str] = []
        for operation in operations:
            op_type = str(operation.get("type") or "").strip()
            relative_path = str(operation.get("path") or "").strip()
            content = str(operation.get("content") or "")
            target = operation.get("target")
            occurrence = int(operation.get("occurrence") or 1)
            if not op_type or not relative_path:
                raise ValueError("Edit operations must include type and path.")
            path = self.worktree / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""

            if op_type == "create_file":
                updated = content
            elif op_type == "replace":
                if not isinstance(target, str) or not target:
                    raise ValueError("Replace operations require a non-empty target string.")
                updated = self._replace_nth(existing, target, content, occurrence)
            elif op_type == "insert_after":
                if not isinstance(target, str) or not target:
                    raise ValueError("insert_after operations require a non-empty target string.")
                updated = self._replace_nth(existing, target, f"{target}{content}", occurrence)
            elif op_type == "insert_before":
                if not isinstance(target, str) or not target:
                    raise ValueError("insert_before operations require a non-empty target string.")
                updated = self._replace_nth(existing, target, f"{content}{target}", occurrence)
            elif op_type == "append":
                updated = existing + content
            elif op_type == "prepend":
                updated = content + existing
            else:
                raise ValueError(f"Unsupported edit operation type: {op_type}")

            path.write_text(updated, encoding="utf-8")
            if relative_path not in touched:
                touched.append(relative_path)
        return touched

    def _run_tool(self, command: list[str], timeout: int = 300) -> CommandResult:
        return _run(command, cwd=str(self.worktree), timeout=timeout)

    def _run_tool_with_env(self, command: list[str], env_overrides: dict[str, str], timeout: int = 300) -> CommandResult:
        env = os.environ.copy()
        env.update(env_overrides)
        completed = subprocess.run(
            command,
            cwd=str(self.worktree),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout[-8000:],
            stderr=completed.stderr[-8000:],
            duration_seconds=0.0,
        )

    def _include_path(self, relative_path: str) -> bool:
        path = Path(relative_path)
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
            return False
        if path.suffix.lower() in {".pyc", ".pyo"}:
            return False
        return True

    def _changed_paths(self) -> list[str]:
        result = self._run_tool(["git", "status", "--porcelain"])
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if not line:
                continue
            relative_path = line[3:]
            if self._include_path(relative_path):
                paths.append(relative_path)
        return paths

    def run_tests(self, command: list[str]) -> CommandResult:
        joined = " ".join(command).lower()
        if "pytest" in joined:
            return self._run_tool_with_env(command, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
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
        return self._changed_paths()

    def diff(self) -> str:
        return self._run_tool(["git", "diff", "--stat"]).stdout

    def diff_patch(self) -> str:
        return self._run_tool(["git", "diff", "--", "."]).stdout

    def commit_diff(self, commit_sha: str) -> str:
        return self._run_tool(
            ["git", "show", "--format=medium", "--stat", "--patch", "--no-ext-diff", commit_sha]
        ).stdout

    def commit_diff_stat(self, commit_sha: str) -> str:
        return self._run_tool(["git", "show", "--stat", "--format=oneline", "--no-patch", commit_sha]).stdout

    def worktree_state(self) -> dict[str, object]:
        changed = self.changed_files()
        diff_stat = self.diff()
        diff_patch = self.diff_patch()
        return {
            "worktree_path": str(self.worktree),
            "changed_files": changed,
            "diff_stat": diff_stat,
            "diff_patch": diff_patch,
            "has_changes": bool(changed or diff_stat.strip() or diff_patch.strip()),
            "reason": "No repo changes yet." if not changed and not diff_stat.strip() and not diff_patch.strip() else "Isolated worktree contains pending or accepted changes.",
        }

    def revert_to_checkpoint(self, commit_sha: str) -> None:
        subprocess.run(["git", "reset", "--hard", commit_sha], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fd"], cwd=str(self.worktree), check=True, capture_output=True, text=True)

    def commit(self, message: str) -> str:
        changed = self._changed_paths()
        if not changed:
            return self.git_head()
        subprocess.run(["git", "add", "--", *changed], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(self.worktree), check=True, capture_output=True, text=True)
        return self.git_head()

    # Compatibility wrappers for older call sites while the runner is being upgraded.
    def search(self, pattern: str) -> list[str]:
        return self.search_code(pattern)

    def run_command(self, command: list[str], timeout: int = 300) -> CommandResult:
        return self._run_tool(command, timeout=timeout)

    def benchmark_metric(self, command: list[str]) -> tuple[CommandResult, float | None]:
        return self.benchmark(command)
