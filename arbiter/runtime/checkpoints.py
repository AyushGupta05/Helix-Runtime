from __future__ import annotations

from uuid import uuid4

from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ArbiterState,
    MissionStateCheckpoint,
    RepoStateCheckpoint,
)
from arbiter.runtime.store import MissionStore


class MissionCheckpointManager:
    def __init__(self, mission_id: str, store: MissionStore) -> None:
        self.mission_id = mission_id
        self.store = store

    def save(self, label: str, state: ArbiterState) -> MissionStateCheckpoint:
        checkpoint = MissionStateCheckpoint(
            checkpoint_id=f"{self.mission_id}-mission-{uuid4().hex[:8]}",
            mission_id=self.mission_id,
            label=label,
            active_phase=state.active_phase,
            active_task_id=state.active_task_id,
            active_bid_round=state.active_bid_round,
            recovery_round=state.recovery_round,
            winner_bid_id=state.winner_bid_id,
            standby_bid_id=state.standby_bid_id,
            accepted_checkpoint_id=state.accepted_checkpoint.checkpoint_id if state.accepted_checkpoint else None,
            run_state=state.control.run_state,
            policy_state=state.governance.policy_state,
            state=state.model_dump(mode="json"),
        )
        self.store.save_mission_state_checkpoint(checkpoint)
        return checkpoint


class RepoCheckpointManager:
    def __init__(self, mission_id: str, branch_name: str, store: MissionStore) -> None:
        self.mission_id = mission_id
        self.branch_name = branch_name
        self.store = store

    def save(
        self,
        checkpoint: AcceptedCheckpoint,
        *,
        accepted: bool = True,
        checkpoint_kind: str = "accepted",
        label: str | None = None,
        worktree_state: dict | None = None,
    ) -> RepoStateCheckpoint:
        record = RepoStateCheckpoint(
            checkpoint_id=f"{self.mission_id}-repo-{uuid4().hex[:8]}",
            mission_id=self.mission_id,
            label=label or checkpoint.label,
            checkpoint_kind=checkpoint_kind,
            branch_name=self.branch_name,
            commit_sha=checkpoint.commit_sha,
            accepted=accepted,
            diff_summary=checkpoint.diff_summary,
            diff_patch=checkpoint.diff_patch,
            affected_files=list(checkpoint.affected_files),
            worktree_state=worktree_state or {},
            rollback_pointer=checkpoint.rollback_pointer,
        )
        self.store.save_repo_state_checkpoint(record)
        return record
