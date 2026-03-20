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


def build_mission_paths(repo_path: str, mission_id: str) -> MissionPaths:
    repo = Path(repo_path).resolve()
    root = repo / ".arbiter" / mission_id
    reports = root / "reports"
    replay = root / "replay"
    worktree = repo / ".arbiter" / "worktrees" / mission_id
    for path in (root, reports, replay, worktree.parent):
        path.mkdir(parents=True, exist_ok=True)
    return MissionPaths(
        repo_path=str(repo),
        root_dir=str(root),
        db_path=str(root / "mission.db"),
        events_path=str(root / "events.jsonl"),
        metadata_path=str(root / "metadata.json"),
        reports_dir=str(reports),
        replay_dir=str(replay),
        worktree_dir=str(worktree),
    )

