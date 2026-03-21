from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from arbiter.core.contracts import MissionPaths


def generate_mission_id() -> str:
    return uuid4().hex[:12]


def sanitize_branch_fragment(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return slug[:40] or "mission"


def build_managed_branch_name(repo_path: str | Path, objective: str, mission_id: str) -> str:
    repo_name = Path(repo_path).name if not isinstance(repo_path, Path) else repo_path.name
    repo_fragment = sanitize_branch_fragment(repo_name).lower()
    objective_words = objective.split()
    objective_fragment = sanitize_branch_fragment(" ".join(objective_words[:6])).lower()[:32]
    mission_fragment = sanitize_branch_fragment(mission_id).lower()[:8]
    parts = [part for part in (repo_fragment, objective_fragment, mission_fragment) if part]
    return f"codex/{'-'.join(parts) or 'mission'}"


def resolve_repo_path(repo_path: str) -> Path:
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists():
        raise ValueError(f"Repository path does not exist: {repo}")
    if not repo.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo}")
    if not (repo / ".git").exists():
        raise ValueError(f"Repository path is not a git repository: {repo}")
    return repo


def build_mission_paths(repo_path: str, mission_id: str) -> MissionPaths:
    repo = resolve_repo_path(repo_path)
    legacy_root = repo / ".arbiter" / mission_id
    missions_root = repo / ".arbiter" / "missions"
    root = missions_root / mission_id
    reports = root / "reports"
    replay = root / "replay"
    worktree = repo / ".arbiter" / "worktrees" / mission_id / "primary"
    scratch = repo / ".arbiter" / "worktrees" / mission_id / "scratch"
    for path in (missions_root, root, reports, replay, worktree.parent, scratch):
        path.mkdir(parents=True, exist_ok=True)
    return MissionPaths(
        repo_path=str(repo),
        root_dir=str(root),
        db_path=str(root / "state.db"),
        events_path=str(root / "events.jsonl"),
        metadata_path=str(root / "metadata.json"),
        reports_dir=str(reports),
        replay_dir=str(replay),
        worktree_dir=str(worktree),
        scratch_worktrees_dir=str(scratch),
        legacy_root_dir=str(legacy_root) if legacy_root.exists() else None,
    )
