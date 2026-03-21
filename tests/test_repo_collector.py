from __future__ import annotations

import sys
import subprocess
from pathlib import Path

from arbiter.repo.collector import RepoStateCollector


def test_detects_python_repo(python_bug_repo) -> None:
    snapshot = RepoStateCollector(str(python_bug_repo)).collect(run_commands=False)
    assert snapshot.capabilities.runtime == "python"
    assert snapshot.capabilities.test_commands


def test_detects_supported_single_package_ts_repo(ts_repo) -> None:
    snapshot = RepoStateCollector(str(ts_repo)).collect(run_commands=False)
    assert snapshot.capabilities.runtime == "tsjs"
    assert snapshot.capabilities.is_single_package_tsjs is True
    expected_pm = "npm.cmd" if sys.platform.startswith("win") else "npm"
    assert snapshot.capabilities.test_commands == [[expected_pm, "run", "test"]]


def test_skips_heavy_ignored_directories_in_scan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    ignored = repo / "node_modules" / "huge_pkg"
    ignored.mkdir(parents=True)
    (ignored / "test_hidden.py").write_text("raise SystemExit\n", encoding="utf-8")

    snapshot = RepoStateCollector(str(repo)).collect(run_commands=False)

    assert snapshot.capabilities.runtime == "python"
    assert "app.py" in snapshot.complexity_hotspots
    assert not any("node_modules" in path for path in snapshot.complexity_hotspots)


def test_python_static_analysis_is_not_enabled_without_explicit_mypy_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    snapshot = RepoStateCollector(str(repo)).collect(run_commands=False)

    assert snapshot.capabilities.runtime == "python"
    assert snapshot.capabilities.static_commands == []


def test_python_static_analysis_uses_mypy_when_explicitly_configured(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='demo'\n\n[tool.mypy]\npython_version='3.13'\n",
        encoding="utf-8",
    )

    snapshot = RepoStateCollector(str(repo)).collect(run_commands=False)

    assert snapshot.capabilities.runtime == "python"
    assert snapshot.capabilities.static_commands == [[sys.executable, "-m", "mypy", "."]]


def test_collects_github_remote_and_objective_hints(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:openai/arbiter.git"], cwd=str(repo), check=True, capture_output=True, text=True)

    snapshot = RepoStateCollector(str(repo)).collect(
        run_commands=False,
        objective="Investigate PR #42 and issue #7 from https://github.com/openai/arbiter/pull/42",
    )

    assert snapshot.remote_provider == "github"
    assert snapshot.remote_slug == "openai/arbiter"
    assert snapshot.remotes["origin"] == "git@github.com:openai/arbiter.git"
    assert snapshot.objective_hints["pr_numbers"] == [42]
    assert snapshot.objective_hints["issue_numbers"] == [7]
