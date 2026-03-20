from __future__ import annotations

import json
import time
from pathlib import Path

from arbiter.agents.backend import BedrockModelRouter, DefaultStrategyBackend, load_candidate_files
from arbiter.civic.runtime import CivicRuntime
from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ActivePhase,
    ArbiterState,
    ExecutionStep,
    FailureContext,
    MissionControlState,
    MissionEvent,
    MissionOutcome,
    MissionSpec,
    RunState,
    TaskRequirementLevel,
    TaskStatus,
    ValidationReport,
    utc_now,
)
from arbiter.market.clustering import cluster_and_select
from arbiter.market.scoring import hard_filter_reason, score_bid
from arbiter.mission.decomposer import GoalDecomposer
from arbiter.mission.recovery import RecoveryEngine
from arbiter.mission.state import initialize_state
from arbiter.repo.collector import RepoStateCollector
from arbiter.repo.worktree import WorktreeManager
from arbiter.runtime.checkpoints import MissionCheckpointManager, RepoCheckpointManager
from arbiter.runtime.config import load_runtime_config
from arbiter.runtime.events import EventLogger
from arbiter.runtime.paths import build_mission_paths, generate_mission_id, sanitize_branch_fragment
from arbiter.runtime.replay import ReplayManager
from arbiter.runtime.store import MissionStore
from arbiter.sim.factory import SimulationFactory
from arbiter.tools.local import LocalToolset
from arbiter.validators.engine import ValidationEngine


class MissionPaused(RuntimeError):
    pass


class MissionCancelled(RuntimeError):
    pass


class MissionRuntime:
    def __init__(self, spec: MissionSpec, paths, strategy_backend=None) -> None:
        self.spec = spec
        self.paths = paths
        self.config = load_runtime_config()
        self.store = MissionStore(paths.db_path)
        self.events = EventLogger(paths.events_path)
        self.replay = ReplayManager(self.store, paths.replay_dir, mode=self.config.replay_mode)
        self.router = BedrockModelRouter(self.config, self.replay)
        self.strategy_backend = strategy_backend or DefaultStrategyBackend(self.router)
        self.collector = RepoStateCollector(spec.repo_path)
        self.decomposer = GoalDecomposer()
        self.simulation = SimulationFactory(self.config.max_parallel_bidders)
        self.recovery = RecoveryEngine()
        self.civic = CivicRuntime(self.config)
        self.branch_name = f"codex/arbiter-{sanitize_branch_fragment(spec.mission_id)}"
        self.worktree = WorktreeManager(spec.repo_path, paths.worktree_dir, self.branch_name)
        self.toolset = LocalToolset(paths.worktree_dir)
        self.state = initialize_state(spec)
        self.state.summary.mission_id = spec.mission_id
        self.state.summary.repo_path = spec.repo_path
        self.state.summary.objective = spec.objective
        self.failed_families: dict[str, set[str]] = {}
        self.accepted_checkpoint: AcceptedCheckpoint | None = None
        self.mission_checkpoints = MissionCheckpointManager(self.store)
        self.repo_checkpoints = RepoCheckpointManager(self.store)

    def emit(self, event_type: str, message: str, **payload) -> None:
        event = MissionEvent(event_type=event_type, mission_id=self.spec.mission_id, message=message, payload=payload)
        self.events.emit(event)
        self.store.append_event(event_type=event.event_type, payload=event.model_dump(mode="json"), created_at=event.created_at.isoformat())

    def persist(self, status: str) -> None:
        self.state.summary.branch_name = self.branch_name
        self.state.summary.runtime_seconds = self.state.runtime_seconds
        self.state.summary.token_usage = self.state.token_usage
        self.state.summary.cost_usage = self.state.cost_usage
        self.state.summary.decision_history = self.state.decision_history
        if self.accepted_checkpoint:
            self.state.summary.head_commit = self.accepted_checkpoint.commit_sha
        self.store.upsert_mission(
            mission_id=self.spec.mission_id,
            status=status,
            repo_path=self.spec.repo_path,
            branch_name=self.branch_name,
            outcome=self.state.outcome.value if self.state.outcome else None,
            spec=self.spec,
            summary=self.state.summary,
        )
        self._save_control_state()
        self.mission_checkpoints.save(status, self.state)

    def _save_control_state(self) -> None:
        self.store.upsert_control_state(
            mission_id=self.spec.mission_id,
            run_state=self.state.control.run_state.value,
            requested_action=self.state.control.requested_action,
            reason=self.state.control.reason,
            updated_at=self.state.control.updated_at.isoformat(),
        )

    def _hydrate_control_state(self) -> None:
        row = self.store.fetch_control_state(self.spec.mission_id)
        if row is None:
            return
        self.state.control = MissionControlState(
            run_state=RunState(row["run_state"]),
            requested_action=row["requested_action"],
            reason=row["reason"],
        )

    def _set_control_state(self, run_state: RunState, requested_action: str | None = None, reason: str | None = None) -> None:
        self.state.control = MissionControlState(
            run_state=run_state,
            requested_action=requested_action,
            reason=reason,
            updated_at=utc_now(),
        )
        self._save_control_state()

    def _prepare_run(self) -> str:
        self.worktree.ensure()
        if self.accepted_checkpoint is None:
            initial_sha = self.toolset.run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
            self.accepted_checkpoint = AcceptedCheckpoint(
                checkpoint_id=f"{self.spec.mission_id}-accepted-0",
                label="initial",
                commit_sha=initial_sha,
                summary="Initial worktree head.",
            )
            self.repo_checkpoints.save(self.accepted_checkpoint)
        previous = self.state.control.run_state
        self._hydrate_control_state()
        has_events = bool(self.store.fetch_all("events"))
        if previous == RunState.PAUSED or self.state.control.run_state == RunState.PAUSED:
            self._set_control_state(RunState.RUNNING)
            self.emit("mission.resumed", "Mission resumed.", mission_id=self.spec.mission_id)
        elif self.state.control.run_state in {RunState.IDLE, RunState.FINALIZED} or not has_events:
            self._set_control_state(RunState.RUNNING)
            self.emit("mission.started", "Mission runtime created.", repo_path=self.spec.repo_path, branch_name=self.branch_name)
        return self.state.active_phase.value if self.state.active_phase != ActivePhase.IDLE else ActivePhase.COLLECT.value

    def _cooperate(self) -> None:
        self._hydrate_control_state()
        action = self.state.control.requested_action
        if action == "cancel":
            self._set_control_state(RunState.CANCELLING, requested_action=None, reason=self.state.control.reason)
            raise MissionCancelled(self.state.control.reason or "user_cancelled")
        if action == "pause":
            self._set_control_state(RunState.PAUSED, requested_action=None, reason=self.state.control.reason)
            self.emit("mission.paused", "Mission paused.", reason=self.state.control.reason)
            self.persist("paused")
            raise MissionPaused(self.state.control.reason or "user_paused")
        if self.state.control.run_state == RunState.RUNNING:
            return
        if self.state.control.run_state == RunState.PAUSED:
            raise MissionPaused(self.state.control.reason or "user_paused")

    def run(self) -> ArbiterState:
        started = time.perf_counter()
        status = self._prepare_run()
        try:
            while status != "done":
                self._cooperate()
                self.state.active_phase = ActivePhase(status)
                self.persist(f"phase:{status}")
                result = getattr(self, f"node_{status}")()
                status = result["status"]
            self.state.runtime_seconds += time.perf_counter() - started
            return self.state
        except MissionPaused:
            self.state.runtime_seconds += time.perf_counter() - started
            return self.state
        except MissionCancelled as exc:
            self.state.runtime_seconds += time.perf_counter() - started
            self.state.outcome = MissionOutcome.FAILED_SAFE_STOP
            self.state.summary.audit_summary["cancel_reason"] = str(exc)
            self.state.active_phase = ActivePhase.FINALIZE
            self._set_control_state(RunState.FINALIZED, reason=str(exc))
            self.emit("mission.cancelled", "Mission cancelled.", reason=str(exc))
            self.emit("mission.finalized", "Mission finalized.", outcome=self.state.outcome.value)
            self.persist("cancelled")
            return self.state

    def node_collect(self) -> dict:
        self.state.repo_snapshot = self.collector.collect(run_commands=True)
        self.emit("repo.scan.completed", "Repository scan completed.", runtime=self.state.repo_snapshot.capabilities.runtime)
        return {"status": ActivePhase.DECOMPOSE.value}

    def node_decompose(self) -> dict:
        assert self.state.repo_snapshot is not None
        self.state.tasks = self.decomposer.decompose(self.spec.objective, self.state.repo_snapshot)
        for task in self.state.tasks:
            self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
            self.emit("task.created", f"Task {task.task_id} created.", task_id=task.task_id, task_type=task.task_type.value)
        return {"status": ActivePhase.SELECT_TASK.value}

    def node_select_task(self) -> dict:
        for task in self.state.tasks:
            if task.status == TaskStatus.PENDING and all(self._task(dep).status == TaskStatus.COMPLETED for dep in task.dependencies):
                task.status = TaskStatus.READY
                self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
                self.emit("task.ready", f"Task {task.task_id} is ready.", task_id=task.task_id)
        ready = [task for task in self.state.tasks if task.status == TaskStatus.READY]
        if not ready:
            return {"status": ActivePhase.FINALIZE.value}
        self.state.active_task_id = ready[0].task_id
        return {"status": ActivePhase.MARKET.value}

    def node_market(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        self.state.active_bid_round += 1
        bids = self.simulation.generate(task, self.state.repo_snapshot)
        available_tools = set(self.spec.allowed_tool_classes)
        failed = self.failed_families.setdefault(task.task_id, set())
        filtered = []
        for bid in bids:
            rejection = hard_filter_reason(bid, task, self.spec, available_tools, failed)
            if rejection:
                bid.rejection_reason = rejection
                self.store.save_record("bids", "bid_id", bid.bid_id, bid, task_id=bid.task_id, selected=0, standby=0)
                self.emit("bid.rejected", f"Bid {bid.bid_id} rejected.", bid_id=bid.bid_id, reason=rejection, task_id=task.task_id)
                continue
            bid.score = score_bid(bid)
            filtered.append(bid)
        contenders = cluster_and_select(filtered, per_family=2)
        self.state.active_bids = contenders
        for bid in contenders:
            self.store.save_record("bids", "bid_id", bid.bid_id, bid, task_id=task.task_id, selected=0, standby=0)
            self.emit(
                "bid.submitted",
                f"Bid {bid.bid_id} submitted.",
                bid_id=bid.bid_id,
                task_id=task.task_id,
                role=bid.role,
                score=bid.score,
                strategy_family=bid.strategy_family,
            )
        if not contenders:
            self.state.no_valid_contenders = True
            return {"status": ActivePhase.FINALIZE.value}
        self.state.current_bid = contenders[0]
        self.state.standby_bid = contenders[1] if len(contenders) > 1 and contenders[1].can_be_standby else None
        self.state.winner_bid_id = contenders[0].bid_id
        self.state.standby_bid_id = self.state.standby_bid.bid_id if self.state.standby_bid else None
        self.store.save_record("bids", "bid_id", contenders[0].bid_id, contenders[0], task_id=task.task_id, selected=1, standby=0)
        self.emit("bid.won", f"Bid {contenders[0].bid_id} won.", task_id=task.task_id, bid_id=contenders[0].bid_id, role=contenders[0].role, score=contenders[0].score)
        if self.state.standby_bid:
            self.store.save_record("bids", "bid_id", self.state.standby_bid.bid_id, self.state.standby_bid, task_id=task.task_id, selected=0, standby=1)
            self.emit("standby.selected", f"Standby selected for {task.task_id}.", bid_id=self.state.standby_bid.bid_id, role=self.state.standby_bid.role, task_id=task.task_id)
        return {"status": ActivePhase.EXECUTE.value}

    def node_execute(self) -> dict:
        task = self._active_task()
        bid = self.state.current_bid
        assert bid is not None
        task.status = TaskStatus.RUNNING
        self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
        self.emit("task.running", f"Task {task.task_id} is running.", task_id=task.task_id)
        if task.task_type.value in {"localize", "perf_diagnosis", "validate"}:
            self.state.decision_history.append(f"{task.task_id}: evidence-only step completed.")
            self.emit("tool.executed", "Evidence-only task executed.", task_id=task.task_id)
            return {"status": ActivePhase.VALIDATE.value}
        candidate_files = load_candidate_files(self.paths.worktree_dir, bid.touched_files or task.candidate_files)
        proposal, invocation = self.strategy_backend.generate_edit_proposal(
            task=task,
            bid=bid,
            mission_objective=self.spec.objective,
            candidate_files=candidate_files,
            failure_context=self.state.failure_context.details if self.state.failure_context else None,
        )
        self._merge_usage(invocation.token_usage, invocation.cost_usage)
        if not proposal.files:
            self.state.failure_context = FailureContext(
                task_id=task.task_id,
                failure_type="execution_stall",
                details="No file updates were proposed.",
                diff_summary="No diff generated.",
                validator_deltas=[],
                recommended_recovery_scope="rebid",
            )
            self.emit("task.failed", "Task failed to generate an edit.", task_id=task.task_id)
            return {"status": ActivePhase.RECOVER.value}
        self._cooperate()
        touched = self.toolset.apply_file_updates({item.path: item.content for item in proposal.files})
        self.state.latest_diff_summary = self.toolset.diff()
        step = ExecutionStep(
            step_id=f"{task.task_id}-step-{len(self.store.fetch_all('execution_steps')) + 1}",
            task_id=task.task_id,
            bid_id=bid.bid_id,
            action_type="edit",
            description=proposal.summary,
            tool_name="edit",
            input_payload={"files": bid.touched_files},
            output_payload={"touched": touched, "notes": proposal.notes},
        )
        self.store.save_record("execution_steps", "step_id", step.step_id, step, task_id=task.task_id, bid_id=bid.bid_id)
        self.emit("tool.executed", "Material edit applied.", task_id=task.task_id, touched_files=touched)
        return {"status": ActivePhase.VALIDATE.value}

    def node_validate(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        if task.task_type.value in {"localize", "perf_diagnosis"}:
            report = ValidationReport(
                task_id=task.task_id,
                passed=bool(task.candidate_files),
                notes=[] if task.candidate_files else ["No candidate files identified during evidence gathering."],
            )
        else:
            validator = ValidationEngine(self.toolset, self.spec, self.state.repo_snapshot)
            report = validator.validate(task)
        self.state.validation_report = report
        self.store.save_record("validation_reports", "task_id", task.task_id, report)
        if report.passed:
            task.status = TaskStatus.COMPLETED
            self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
            changed = self.toolset.changed_files()
            if changed:
                commit_sha = self.toolset.commit(f"Arbiter mission {self.spec.mission_id}: {task.title}")
                checkpoint = AcceptedCheckpoint(
                    checkpoint_id=f"{self.spec.mission_id}-accepted-{task.task_id}",
                    label=task.task_id,
                    commit_sha=commit_sha,
                    summary=task.title,
                )
                self.accepted_checkpoint = checkpoint
                self.repo_checkpoints.save(checkpoint)
                self.emit("checkpoint.accepted", "Accepted checkpoint committed.", task_id=task.task_id, commit_sha=commit_sha)
            self.state.decision_history.append(f"{task.task_id}: completed")
            self.emit("task.completed", f"Task {task.task_id} completed.", task_id=task.task_id)
            self.emit("validation.passed", "Validation passed.", task_id=task.task_id)
            return {"status": ActivePhase.SELECT_TASK.value}
        details = "; ".join(report.notes) or "Validation failed."
        self.state.failure_context = FailureContext(
            task_id=task.task_id,
            failure_type="validation_failure",
            details=details,
            diff_summary=self.toolset.diff(),
            validator_deltas=[result.stderr or result.stdout for result in report.command_results if result.exit_code != 0][:5],
            recommended_recovery_scope="standby_or_rebid",
        )
        self.state.latest_diff_summary = self.state.failure_context.diff_summary
        self.store.save_record("failure_contexts", "task_id", task.task_id, self.state.failure_context)
        self.emit("task.failed", "Task failed validation.", task_id=task.task_id)
        self.emit("validation.failed", "Validation failed.", task_id=task.task_id, details=details)
        return {"status": ActivePhase.RECOVER.value}

    def node_recover(self) -> dict:
        task = self._active_task()
        assert self.state.failure_context is not None
        self.state.recovery_round += 1
        if self.accepted_checkpoint:
            self.toolset.revert_to_commit(self.accepted_checkpoint.commit_sha)
            self.emit("checkpoint.reverted", "Worktree reverted to accepted checkpoint.", commit_sha=self.accepted_checkpoint.commit_sha)
        if self.recovery.should_promote_standby(self.state.standby_bid, self.state.failure_context):
            self.state.current_bid = self.state.standby_bid
            self.state.standby_bid = None
            self.state.winner_bid_id = self.state.current_bid.bid_id
            self.state.standby_bid_id = None
            self.emit("standby.promoted", "Standby promoted after failure.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id)
            return {"status": ActivePhase.EXECUTE.value}
        if self.state.current_bid:
            self.failed_families.setdefault(task.task_id, set()).add(self.state.current_bid.strategy_family)
            self.state.summary.failed_attempt_history.append(self.state.current_bid.strategy_summary)
        if self.state.recovery_round > self.spec.stop_policy.max_recovery_rounds:
            self.state.outcome = MissionOutcome.FAILED_EXECUTION
            return {"status": ActivePhase.FINALIZE.value}
        task.status = TaskStatus.READY
        self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
        self.emit("recovery.round_opened", "Rebidding with prior evidence.", task_id=task.task_id, round=self.state.recovery_round)
        return {"status": ActivePhase.MARKET.value}

    def node_finalize(self) -> dict:
        required_tasks = [task for task in self.state.tasks if task.requirement_level == TaskRequirementLevel.REQUIRED]
        optional_tasks = [task for task in self.state.tasks if task.requirement_level == TaskRequirementLevel.OPTIONAL]
        if self.state.outcome is None:
            if all(task.status == TaskStatus.COMPLETED for task in required_tasks):
                if any(task.status in {TaskStatus.FAILED, TaskStatus.SKIPPED} for task in optional_tasks):
                    self.state.outcome = MissionOutcome.PARTIAL_SUCCESS
                else:
                    self.state.outcome = MissionOutcome.SUCCESS
            elif self.state.no_valid_contenders or self.state.recovery_round > self.spec.stop_policy.max_recovery_rounds:
                self.state.outcome = MissionOutcome.FAILED_EXECUTION
            else:
                self.state.outcome = MissionOutcome.FAILED_SAFE_STOP
        self.state.summary.outcome = self.state.outcome
        self.state.active_phase = ActivePhase.FINALIZE
        self._set_control_state(RunState.FINALIZED)
        self.emit("mission.finalized", "Mission finalized.", outcome=self.state.outcome.value)
        return {"status": "done"}

    def _active_task(self):
        return self._task(self.state.active_task_id)

    def _task(self, task_id: str):
        for task in self.state.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def _merge_usage(self, token_usage: dict, cost_usage: dict) -> None:
        for key, value in token_usage.items():
            self.state.token_usage[key] = self.state.token_usage.get(key, 0) + int(value)
        for key, value in cost_usage.items():
            self.state.cost_usage[key] = self.state.cost_usage.get(key, 0.0) + float(value)


def build_mission_spec(
    repo: str,
    objective: str,
    constraints: list[str] | None = None,
    preferences: list[str] | None = None,
    max_runtime: int | None = None,
    benchmark_requirement: str | None = None,
    protected_paths: list[str] | None = None,
    public_api_surface: list[str] | None = None,
    mission_id: str | None = None,
) -> MissionSpec:
    config = load_runtime_config()
    return MissionSpec(
        mission_id=mission_id or generate_mission_id(),
        repo_path=str(Path(repo).resolve()),
        objective=objective,
        constraints=constraints or [],
        preferences=preferences or [],
        max_runtime_minutes=max_runtime or config.max_runtime_minutes,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths or [],
        public_api_surface=public_api_surface or [],
    )


def start_mission(
    repo: str,
    objective: str,
    constraints: list[str] | None = None,
    preferences: list[str] | None = None,
    max_runtime: int | None = None,
    benchmark_requirement: str | None = None,
    protected_paths: list[str] | None = None,
    public_api_surface: list[str] | None = None,
    strategy_backend=None,
    mission_id: str | None = None,
) -> ArbiterState:
    spec = build_mission_spec(
        repo=repo,
        objective=objective,
        constraints=constraints,
        preferences=preferences,
        max_runtime=max_runtime,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths,
        public_api_surface=public_api_surface,
        mission_id=mission_id,
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    Path(paths.metadata_path).write_text(json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8")
    runtime = MissionRuntime(spec, paths, strategy_backend=strategy_backend)
    try:
        state = runtime.run()
        final_status = "paused" if state.control.run_state == RunState.PAUSED else "finished"
        runtime.persist(final_status)
        return state
    finally:
        runtime.store.close()


def resume_mission(mission_id: str, repo: str, strategy_backend=None) -> ArbiterState:
    paths = build_mission_paths(repo, mission_id)
    store = MissionStore(paths.db_path)
    row = store.fetch_mission()
    if row is None:
        store.close()
        raise ValueError(f"Mission {mission_id} not found.")
    spec = MissionSpec.model_validate_json(row["spec_json"])
    checkpoint = store.fetch_latest_checkpoint()
    store.close()
    runtime = MissionRuntime(spec, paths, strategy_backend=strategy_backend)
    if checkpoint:
        runtime.state = ArbiterState.model_validate_json(checkpoint["state_json"])
    try:
        state = runtime.run()
        final_status = "paused" if state.control.run_state == RunState.PAUSED else "finished"
        runtime.persist(final_status)
        return state
    finally:
        runtime.store.close()


def mission_status(mission_id: str, repo: str) -> dict:
    paths = build_mission_paths(repo, mission_id)
    store = MissionStore(paths.db_path)
    row = store.fetch_mission()
    if row is None:
        store.close()
        raise ValueError(f"Mission {mission_id} not found.")
    summary = json.loads(row["summary_json"])
    control = store.fetch_control_state(mission_id)
    events = store.fetch_all("events")
    payload = {
        "mission_id": mission_id,
        "status": row["status"],
        "outcome": row["outcome"],
        "branch_name": row["branch_name"],
        "decision_history": summary.get("decision_history", []),
        "failed_attempt_history": summary.get("failed_attempt_history", []),
        "event_count": len(events),
        "run_state": control["run_state"] if control else RunState.IDLE.value,
    }
    store.close()
    return payload
