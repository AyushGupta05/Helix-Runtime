from __future__ import annotations

from pathlib import Path

from arbiter.runtime.paths import resolve_repo_path
from arbiter.runtime.store import MissionStore
from arbiter.server.schemas import MissionView


def materialize_mission_view(repo_path: str, mission_id: str) -> MissionView:
    repo = resolve_repo_path(repo_path)
    db_path = repo / ".arbiter" / "missions" / mission_id / "state.db"
    if not db_path.exists():
        raise ValueError(f"Mission {mission_id} not found.")
    store = MissionStore(str(Path(db_path)), read_only=True)
    try:
        return MissionView.model_validate(store.get_mission_view(mission_id))
    finally:
        store.close()
