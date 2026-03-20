from __future__ import annotations

from arbiter.core.contracts import AcceptedCheckpoint, ArbiterState
from arbiter.runtime.store import MissionStore


class MissionCheckpointManager:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def save(self, label: str, state: ArbiterState) -> None:
        del label, state


class RepoCheckpointManager:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def save(self, checkpoint: AcceptedCheckpoint, accepted: bool = True) -> None:
        del checkpoint, accepted
