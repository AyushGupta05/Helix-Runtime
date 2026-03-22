from pathlib import Path

from arbiter.repo.worktree import WorktreeManager


def test_hydrate_dependency_dirs_reuses_existing_source_dependencies(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    (repo / "frontend" / "node_modules").mkdir(parents=True)
    (repo / ".venv").mkdir(parents=True)
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend" / "node_modules" / ".bin").mkdir(parents=True)
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")

    manager = WorktreeManager(str(repo), str(worktree), "codex/test-branch")
    linked: list[tuple[Path, Path]] = []

    def _record_link(source: Path, target: Path) -> None:
        linked.append((source, target))
        target.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(manager, "_link_dependency_dir", _record_link)

    manager._hydrate_dependency_dirs(worktree)

    assert linked == [
        (repo / "frontend" / "node_modules", worktree / "frontend" / "node_modules"),
        (repo / ".venv", worktree / ".venv"),
    ]


def test_hydrate_dependency_dirs_skips_empty_source_dependencies(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    (repo / "frontend" / "node_modules").mkdir(parents=True)
    (repo / ".venv").mkdir(parents=True)

    manager = WorktreeManager(str(repo), str(worktree), "codex/test-branch")
    linked: list[tuple[Path, Path]] = []

    def _record_link(source: Path, target: Path) -> None:
        linked.append((source, target))
        target.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(manager, "_link_dependency_dir", _record_link)

    manager._hydrate_dependency_dirs(worktree)

    assert linked == []
