from __future__ import annotations

from pathlib import Path

import pytest

import arbiter.tools.local as local_tools
from arbiter.tools.local import LocalToolset
from arbiter.core.contracts import CommandResult


def test_apply_edit_operations_supports_compact_replacements(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    target = worktree / "calc.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    toolset = LocalToolset(str(worktree))
    touched = toolset.apply_edit_operations(
        [
            {
                "type": "replace",
                "path": "calc.py",
                "target": "return a - b",
                "content": "return a + b",
            }
        ]
    )

    assert touched == ["calc.py"]
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_apply_edit_operations_supports_insertions(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    target = worktree / "calc.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    toolset = LocalToolset(str(worktree))
    toolset.apply_edit_operations(
        [
            {
                "type": "insert_after",
                "path": "calc.py",
                "target": "def add(a, b):\n",
                "content": "    if a is None or b is None:\n        raise TypeError('missing operand')\n",
            }
        ]
    )

    assert "raise TypeError('missing operand')" in target.read_text(encoding="utf-8")


def test_apply_edit_operations_raises_when_target_is_missing(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    (worktree / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    toolset = LocalToolset(str(worktree))

    with pytest.raises(ValueError, match="target was not found"):
        toolset.apply_edit_operations(
            [
                {
                    "type": "replace",
                    "path": "calc.py",
                    "target": "return a - b",
                    "content": "return a + b",
                }
            ]
        )


def test_apply_edit_operations_tolerates_whitespace_drift_in_target(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    target = worktree / "calc.py"
    target.write_text(
        "def add(a, b):\n    total = a + b\n    return total\n",
        encoding="utf-8",
    )

    toolset = LocalToolset(str(worktree))
    touched = toolset.apply_edit_operations(
        [
            {
                "type": "replace",
                "path": "calc.py",
                "target": "total = a + b\nreturn total",
                "content": "total = a + b\n    return max(total, 0)",
            }
        ]
    )

    assert touched == ["calc.py"]
    assert target.read_text(encoding="utf-8") == (
        "def add(a, b):\n    total = a + b\n    return max(total, 0)\n"
    )


def test_apply_edit_operations_is_atomic_when_a_later_operation_fails(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    target = worktree / "calc.py"
    original = "def add(a, b):\n    return a - b\n"
    target.write_text(original, encoding="utf-8")

    toolset = LocalToolset(str(worktree))

    with pytest.raises(ValueError, match="target was not found"):
        toolset.apply_edit_operations(
            [
                {
                    "type": "replace",
                    "path": "calc.py",
                    "target": "return a - b",
                    "content": "return a + b",
                },
                {
                    "type": "insert_after",
                    "path": "calc.py",
                    "target": "return does not exist",
                    "content": "\n# unreachable\n",
                },
            ]
        )

    assert target.read_text(encoding="utf-8") == original


def test_apply_structured_edits_is_atomic_across_file_updates_and_operations(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    calc = worktree / "calc.py"
    test_file = worktree / "tests" / "test_calc.py"
    calc.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_add():\n    assert True\n", encoding="utf-8")

    toolset = LocalToolset(str(worktree))

    with pytest.raises(ValueError, match="target was not found"):
        toolset.apply_structured_edits(
            {
                "tests/test_calc.py": "def test_add():\n    assert add(2, 3) == 5\n",
            },
            [
                {
                    "type": "replace",
                    "path": "calc.py",
                    "target": "return does not exist",
                    "content": "return a + b",
                }
            ],
        )

    assert calc.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert test_file.read_text(encoding="utf-8") == "def test_add():\n    assert True\n"


def test_run_tests_returns_failure_when_command_is_missing(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()

    toolset = LocalToolset(str(worktree))
    result = toolset.run_tests(["definitely-not-a-real-command-helix", "--version"])

    assert result.exit_code == 1
    assert "not found" in result.stderr.lower() or "cannot find" in result.stderr.lower()


def test_run_tests_bootstraps_missing_frontend_dependencies_once(tmp_path: Path, monkeypatch) -> None:
    worktree = tmp_path / "repo"
    frontend = worktree / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        '{"name":"frontend","scripts":{"test":"vitest run","build":"vite build"}}',
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")

    calls: list[list[str]] = []
    package_manager = "npm.cmd" if local_tools.os.name == "nt" else "npm"

    def _fake_run(command: list[str], cwd: str, timeout: int = 120) -> CommandResult:
        del cwd, timeout
        calls.append(command)
        if command[:4] == [package_manager, "--prefix", "frontend", "ci"]:
            (frontend / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
            (frontend / "node_modules" / ".bin" / "vitest").write_text("", encoding="utf-8")
            return CommandResult(command=command, exit_code=0, stdout="installed", stderr="", duration_seconds=0.0)
        return CommandResult(command=command, exit_code=0, stdout="ok", stderr="", duration_seconds=0.0)

    monkeypatch.setattr(local_tools, "_run", _fake_run)

    toolset = LocalToolset(str(worktree))
    first = toolset.run_tests([package_manager, "--prefix", "frontend", "run", "test"])
    second = toolset.static_analysis([package_manager, "--prefix", "frontend", "run", "build"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert calls == [
        [package_manager, "--prefix", "frontend", "ci", "--no-audit", "--no-fund"],
        [package_manager, "--prefix", "frontend", "run", "test"],
        [package_manager, "--prefix", "frontend", "run", "build"],
    ]
