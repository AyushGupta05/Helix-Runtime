from __future__ import annotations

from arbiter.runtime.migrate import migrate_legacy_mission
from arbiter.runtime.paths import build_mission_paths
from arbiter.runtime.store import MissionStore
from arbiter.server.schemas import MissionView


def materialize_mission_view(repo_path: str, mission_id: str) -> MissionView:
    paths = build_mission_paths(repo_path, mission_id)
    migrate_legacy_mission(paths, mission_id)
    store = MissionStore(paths.db_path)
    try:
        return MissionView.model_validate(store.get_mission_view(mission_id))
    finally:
        store.close()
