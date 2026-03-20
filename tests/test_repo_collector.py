from __future__ import annotations

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

