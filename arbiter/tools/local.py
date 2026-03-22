from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from arbiter.core.contracts import CommandResult
from arbiter.repo.collector import IGNORED_DIRECTORIES, _platform_command, _run


class LocalToolset:
    def __init__(self, worktree_path: str) -> None:
        self.worktree = Path(worktree_path).resolve()
        self._js_bootstrap_attempts: dict[str, CommandResult] = {}

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
        return self.apply_structured_edits(updates, [])

    @staticmethod
    def _replace_nth(text: str, target: str, replacement: str, occurrence: int) -> str:
        if occurrence < 1:
            raise ValueError("Edit operations use 1-based occurrence indexes.")
        match = LocalToolset._find_target_match(text, target, occurrence)
        if match is None:
            raise ValueError("Edit operation target was not found in file content.")
        start_index, end_index = match
        return text[:start_index] + replacement + text[end_index:]

    @staticmethod
    def _find_target_match(text: str, target: str, occurrence: int) -> tuple[int, int] | None:
        start = 0
        for _ in range(occurrence):
            index = text.find(target, start)
            if index != -1:
                start = index + len(target)
                continue

            matches = list(LocalToolset._iter_whitespace_tolerant_matches(text, target))
            if len(matches) < occurrence:
                return None
            return matches[occurrence - 1]
        return (index, index + len(target))

    @staticmethod
    def _iter_whitespace_tolerant_matches(text: str, target: str):
        # Model-generated structured edits often preserve the right code shape but
        # drift slightly on indentation or newline formatting.
        normalized_target = target.strip()
        if not normalized_target:
            return
        chunks = [chunk for chunk in re.split(r"(\s+)", normalized_target) if chunk]
        pattern_parts: list[str] = []
        for chunk in chunks:
            if chunk.isspace():
                pattern_parts.append(r"\s+")
            else:
                pattern_parts.append(re.escape(chunk))
        pattern = "".join(pattern_parts)
        if not pattern:
            return
        for match in re.finditer(pattern, text, flags=re.MULTILINE):
            yield match.span()

    def _apply_operation_to_text(
        self,
        *,
        existing: str,
        op_type: str,
        content: str,
        target: Any,
        occurrence: int,
    ) -> str:
        if op_type == "create_file":
            return content
        if op_type == "replace":
            if not isinstance(target, str) or not target:
                raise ValueError("Replace operations require a non-empty target string.")
            return self._replace_nth(existing, target, content, occurrence)
        if op_type == "insert_after":
            if not isinstance(target, str) or not target:
                raise ValueError("insert_after operations require a non-empty target string.")
            return self._replace_nth(existing, target, f"{target}{content}", occurrence)
        if op_type == "insert_before":
            if not isinstance(target, str) or not target:
                raise ValueError("insert_before operations require a non-empty target string.")
            return self._replace_nth(existing, target, f"{content}{target}", occurrence)
        if op_type == "append":
            return existing + content
        if op_type == "prepend":
            return content + existing
        raise ValueError(f"Unsupported edit operation type: {op_type}")

    def apply_structured_edits(
        self,
        file_updates: dict[str, str],
        operations: list[dict[str, Any]],
    ) -> list[str]:
        staged: dict[str, str] = {}
        touched: list[str] = []

        for relative_path, content in file_updates.items():
            if not relative_path:
                raise ValueError("File updates must include a path.")
            staged[relative_path] = content
            if relative_path not in touched:
                touched.append(relative_path)

        for operation in operations:
            op_type = str(operation.get("type") or "").strip()
            relative_path = str(operation.get("path") or "").strip()
            content = str(operation.get("content") or "")
            target = operation.get("target")
            occurrence = int(operation.get("occurrence") or 1)
            if not op_type or not relative_path:
                raise ValueError("Edit operations must include type and path.")
            path = self.worktree / relative_path
            existing = staged.get(relative_path)
            if existing is None:
                existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            updated = self._apply_operation_to_text(
                existing=existing,
                op_type=op_type,
                content=content,
                target=target,
                occurrence=occurrence,
            )
            staged[relative_path] = updated
            if relative_path not in touched:
                touched.append(relative_path)

        for relative_path, content in staged.items():
            path = self.worktree / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return touched

    def apply_edit_operations(self, operations: list[dict[str, Any]]) -> list[str]:
        return self.apply_structured_edits({}, operations)

    def _run_tool(self, command: list[str], timeout: int = 300) -> CommandResult:
        return _run(command, cwd=str(self.worktree), timeout=timeout)

    def _run_tool_with_env(self, command: list[str], env_overrides: dict[str, str], timeout: int = 300) -> CommandResult:
        env = os.environ.copy()
        env.update(env_overrides)
        normalized = _platform_command(command)
        try:
            completed = subprocess.run(
                normalized,
                cwd=str(self.worktree),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=normalized,
                exit_code=124,
                stdout=(exc.stdout or "")[-8000:],
                stderr=(str(exc) or "")[-8000:],
                duration_seconds=0.0,
            )
        except OSError as exc:
            return CommandResult(
                command=normalized,
                exit_code=1,
                stdout="",
                stderr=str(exc)[-8000:],
                duration_seconds=0.0,
            )
        return CommandResult(
            command=normalized,
            exit_code=completed.returncode,
            stdout=completed.stdout[-8000:],
            stderr=completed.stderr[-8000:],
            duration_seconds=0.0,
        )

    def _project_dir_for_package_manager_command(self, command: list[str]) -> Path | None:
        if not command:
            return None
        executable = Path(command[0]).name.lower()
        if executable not in {"npm", "npm.cmd"}:
            return None
        if "--prefix" in command:
            prefix_index = command.index("--prefix")
            if prefix_index + 1 < len(command):
                candidate = (self.worktree / command[prefix_index + 1]).resolve()
                return candidate if candidate.exists() else None
        return self.worktree if (self.worktree / "package.json").exists() else None

    @staticmethod
    def _has_js_dependencies(project_dir: Path) -> bool:
        node_modules = project_dir / "node_modules"
        if not node_modules.is_dir():
            return False
        bin_dir = node_modules / ".bin"
        if bin_dir.is_dir():
            try:
                next(bin_dir.iterdir())
                return True
            except (OSError, StopIteration):
                pass
        try:
            return any(child.name != ".bin" for child in node_modules.iterdir())
        except OSError:
            return False

    def _dependency_install_command(self, command: list[str], project_dir: Path) -> list[str] | None:
        if not command:
            return None
        executable = command[0]
        try:
            relative_dir = project_dir.relative_to(self.worktree).as_posix()
        except ValueError:
            return None
        prefix_args = ["--prefix", relative_dir] if relative_dir != "." else []
        if (project_dir / "package-lock.json").exists():
            return [executable, *prefix_args, "ci", "--no-audit", "--no-fund"]
        return [executable, *prefix_args, "install", "--no-audit", "--no-fund"]

    def _ensure_js_dependencies(self, command: list[str]) -> CommandResult | None:
        project_dir = self._project_dir_for_package_manager_command(command)
        if project_dir is None or not (project_dir / "package.json").exists():
            return None
        if self._has_js_dependencies(project_dir):
            return None
        cache_key = str(project_dir)
        previous_attempt = self._js_bootstrap_attempts.get(cache_key)
        if previous_attempt is not None:
            return None if previous_attempt.exit_code == 0 else previous_attempt
        install_command = self._dependency_install_command(command, project_dir)
        if install_command is None:
            return None
        result = _run(install_command, cwd=str(self.worktree), timeout=600)
        if result.exit_code == 0 and not self._has_js_dependencies(project_dir):
            result = CommandResult(
                command=result.command,
                exit_code=1,
                stdout=result.stdout,
                stderr="Dependency bootstrap completed but the package manager binaries are still unavailable.",
                duration_seconds=result.duration_seconds,
            )
        self._js_bootstrap_attempts[cache_key] = result
        return None if result.exit_code == 0 else result

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
        bootstrap = self._ensure_js_dependencies(command)
        if bootstrap is not None:
            return bootstrap
        joined = " ".join(command).lower()
        if "pytest" in joined:
            return self._run_tool_with_env(command, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        return self._run_tool(command)

    def run_lint(self, command: list[str]) -> CommandResult:
        bootstrap = self._ensure_js_dependencies(command)
        if bootstrap is not None:
            return bootstrap
        return self._run_tool(command)

    def static_analysis(self, command: list[str]) -> CommandResult:
        bootstrap = self._ensure_js_dependencies(command)
        if bootstrap is not None:
            return bootstrap
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
