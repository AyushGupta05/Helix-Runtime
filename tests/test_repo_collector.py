from __future__ import annotations

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
    assert snapshot.capabilities.test_commands == [["npm", "run", "test"]]


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
