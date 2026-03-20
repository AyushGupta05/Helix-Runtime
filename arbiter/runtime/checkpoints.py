from __future__ import annotations

from arbiter.core.contracts import AcceptedCheckpoint, ArbiterState
from arbiter.runtime.store import MissionStore


class MissionCheckpointManager:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def save(self, label: str, state: ArbiterState) -> None:
        self.store.add_checkpoint(label=label, state=state, created_at=state.mission.created_at.isoformat())


class RepoCheckpointManager:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def save(self, checkpoint: AcceptedCheckpoint, accepted: bool = True) -> None:
        self.store.add_repo_checkpoint(
            checkpoint_id=checkpoint.checkpoint_id,
            accepted=accepted,
            payload=checkpoint,
        )

