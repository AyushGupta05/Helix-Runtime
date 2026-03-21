from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Iterable

from arbiter.core.contracts import CapabilitySet, CommandResult, RepoSnapshot

IGNORED_DIRECTORIES = {
    ".git",
    ".arbiter",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".pnpm-store",
    ".yarn",
    "coverage",
}

IGNORED_FILE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
_WINDOWS_COMMAND_ALIASES = {
    "npm": "npm.cmd",
    "npx": "npx.cmd",
    "pnpm": "pnpm.cmd",
    "yarn": "yarn.cmd",
}


def _platform_command(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    executable = command[0].strip().lower()
    if executable in _WINDOWS_COMMAND_ALIASES:
        return [_WINDOWS_COMMAND_ALIASES[executable], *command[1:]]
    return command


def _run(command: list[str], cwd: str, timeout: int = 120) -> CommandResult:
    normalized = _platform_command(command)
    try:
        completed = subprocess.run(
            normalized,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
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


def _prune_directories(directories: list[str]) -> None:
    directories[:] = [name for name in directories if name not in IGNORED_DIRECTORIES]


def _repo_walk(repo: Path) -> Iterable[tuple[Path, list[str], list[str]]]:
    for root, dirs, files in os.walk(repo):
        _prune_directories(dirs)
        yield Path(root), dirs, files


def _iter_source_files(repo: Path) -> Iterable[Path]:
    for root, _, files in _repo_walk(repo):
        for file_name in files:
            path = root / file_name
            if path.suffix.lower() in IGNORED_FILE_SUFFIXES:
                yield path


def _find_matching_file(repo: Path, pattern: str) -> bool:
    for root, _, files in _repo_walk(repo):
        for file_name in files:
            if Path(file_name).match(pattern):
                return True
    return False


_GITHUB_REMOTE_PATTERNS = (
    re.compile(r"^git@github\.com:(?P<slug>[^/]+/[^/]+?)(?:\.git)?$"),
    re.compile(r"^https://github\.com/(?P<slug>[^/]+/[^/]+?)(?:\.git)?$"),
)


def _parse_github_slug(remote_url: str | None) -> str | None:
    if not remote_url:
        return None
    normalized = remote_url.strip()
    for pattern in _GITHUB_REMOTE_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return match.group("slug")
    return None


class RepoStateCollector:
    def __init__(self, repo_path: str) -> None:
        self.repo = Path(repo_path).resolve()

    def collect(self, run_commands: bool = True, objective: str | None = None) -> RepoSnapshot:
        capabilities = self._detect_capabilities()
        branch = self._git(["branch", "--show-current"]).stdout.strip() or None
        head = self._git(["rev-parse", "HEAD"]).stdout.strip() or None
        tracking_branch = self._git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]).stdout.strip() or None
        remotes = self._git_remotes()
        default_remote = "origin" if "origin" in remotes else next(iter(remotes), None)
        remote_slug = _parse_github_slug(remotes.get(default_remote or "", ""))
        remote_provider = "github" if remote_slug else None
        status = self._git(["status", "--porcelain"]).stdout.splitlines()
        changed_files = [line[3:] for line in status if line and not line.startswith("??")]
        untracked = [line[3:] for line in status if line.startswith("??")]
        snapshot = RepoSnapshot(
            repo_path=str(self.repo),
            branch=branch,
            head_commit=head,
            tracking_branch=tracking_branch,
            dirty=bool(status),
            remotes=remotes,
            default_remote=default_remote,
            remote_provider=remote_provider,
            remote_slug=remote_slug,
            objective_hints=self._objective_hints(objective),
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

    def _git_remotes(self) -> dict[str, str]:
        remotes: dict[str, str] = {}
        output = self._git(["remote", "-v"]).stdout.splitlines()
        for line in output:
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in remotes:
                remotes[parts[0]] = parts[1]
        return remotes

    def _objective_hints(self, objective: str | None) -> dict[str, object]:
        text = objective or ""
        pr_numbers = {
            int(match)
            for match in re.findall(r"(?:pull request|pr|pull)[^\d#]*(?:#)?(\d+)", text, flags=re.IGNORECASE)
        }
        pr_numbers.update(int(match) for match in re.findall(r"github\.com/[^/]+/[^/]+/pull/(\d+)", text, flags=re.IGNORECASE))
        issue_numbers = {
            int(match)
            for match in re.findall(r"(?:issue|bug|ticket)[^\d#]*(?:#)?(\d+)", text, flags=re.IGNORECASE)
        }
        issue_numbers.update(int(match) for match in re.findall(r"github\.com/[^/]+/[^/]+/issues/(\d+)", text, flags=re.IGNORECASE))
        discussion_numbers = {
            int(match)
            for match in re.findall(r"(?:discussion)[^\d#]*(?:#)?(\d+)", text, flags=re.IGNORECASE)
        }
        discussion_numbers.update(int(match) for match in re.findall(r"github\.com/[^/]+/[^/]+/discussions/(\d+)", text, flags=re.IGNORECASE))
        return {
            "has_github_reference": bool(pr_numbers or issue_numbers or discussion_numbers),
            "pr_numbers": sorted(pr_numbers),
            "issue_numbers": sorted(issue_numbers),
            "discussion_numbers": sorted(discussion_numbers),
        }

    def _dependency_files(self) -> list[str]:
        candidates = ["pyproject.toml", "requirements.txt", "setup.py", "package.json", "package-lock.json", "pnpm-lock.yaml"]
        return [name for name in candidates if (self.repo / name).exists()]

    def _risky_paths(self) -> list[str]:
        markers = ("migrations", "alembic", "schema", "config", "settings", "api", "public")
        risky: list[str] = []
        for path in _iter_source_files(self.repo):
            relative = str(path.relative_to(self.repo))
            if any(marker in relative.lower() for marker in markers):
                risky.append(relative)
        return risky[:10]

    def _protected_interfaces(self) -> list[str]:
        protected: list[str] = []
        for path in _iter_source_files(self.repo):
            relative = str(path.relative_to(self.repo))
            lowered = relative.lower()
            if any(marker in lowered for marker in ("api", "public", "sdk", "client")):
                protected.append(relative)
        return protected[:10]

    def _tree_summary(self) -> list[str]:
        paths = []
        for path in sorted(self.repo.iterdir()):
            if path.name in IGNORED_DIRECTORIES:
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
        if (self.repo / "pyproject.toml").exists() or (self.repo / "tests").exists() or _find_matching_file(self.repo, "test_*.py"):
            return self._detect_python()
        if (self.repo / "package.json").exists():
            return self._detect_tsjs()
        return CapabilitySet(runtime="unsupported", unsupported_reason="No supported project metadata found.")

    def _detect_python(self) -> CapabilitySet:
        test_commands: list[list[str]] = []
        lint_commands: list[list[str]] = []
        static_commands: list[list[str]] = []
        benchmark_commands: list[list[str]] = []
        python = sys.executable
        if (self.repo / "tests").exists() or any(self.repo.glob("test_*.py")):
            test_commands.append([python, "-m", "pytest"])
        if (self.repo / "ruff.toml").exists() or (self.repo / "pyproject.toml").exists():
            lint_commands.append([python, "-m", "ruff", "check", "."])
        if (self.repo / "mypy.ini").exists() or (self.repo / ".mypy.ini").exists() or self._pyproject_has_tool("mypy"):
            static_commands.append([python, "-m", "mypy", "."])
        if (self.repo / "benchmarks").exists():
            benchmark_commands.append([python, "-m", "pytest", "benchmarks"])
        return CapabilitySet(
            runtime="python",
            test_commands=test_commands,
            lint_commands=lint_commands,
            static_commands=static_commands,
            benchmark_commands=benchmark_commands,
            risky_paths=self._risky_paths(),
            protected_interfaces=self._protected_interfaces(),
        )

    def _pyproject_has_tool(self, tool_name: str) -> bool:
        path = self.repo / "pyproject.toml"
        if not path.exists():
            return False
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return False
        tool = data.get("tool")
        return isinstance(tool, dict) and tool_name in tool

    def _detect_tsjs(self) -> CapabilitySet:
        package_path = self.repo / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if package.get("workspaces") or (self.repo / "pnpm-workspace.yaml").exists():
            return CapabilitySet(
                runtime="unsupported",
                unsupported_reason="TS/JS monorepos are out of scope for V1.",
            )
        scripts = package.get("scripts", {})
        pm = _platform_command(["npm"])[0]
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
            risky_paths=self._risky_paths(),
            protected_interfaces=self._protected_interfaces(),
        )
