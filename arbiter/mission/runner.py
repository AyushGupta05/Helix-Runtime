from __future__ import annotations

import json
import time
from pathlib import Path

from arbiter.agents.backend import BedrockModelRouter, DefaultStrategyBackend, load_candidate_files
from arbiter.civic.runtime import CivicRuntime
from arbiter.core.contracts import AcceptedCheckpoint, ArbiterState, FailureContext, MissionEvent, MissionOutcome, MissionSpec, TaskRequirementLevel, TaskStatus
from arbiter.graph.workflow import build_workflow
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
        self.failed_families: dict[str, set[str]] = {}
        self.accepted_checkpoint: AcceptedCheckpoint | None = None
        self.mission_checkpoints = MissionCheckpointManager(self.store)
        self.repo_checkpoints = RepoCheckpointManager(self.store)

    def emit(self, event_type: str, message: str, **payload) -> None:
        event = MissionEvent(event_type=event_type, mission_id=self.spec.mission_id, message=message, payload=payload)
        self.events.emit(event)
        self.store.append_event(event_type=event.event_type, payload=event.model_dump(), created_at=event.created_at.isoformat())

    def persist(self, status: str) -> None:
        self.state.summary.branch_name = self.branch_name
        self.state.summary.runtime_seconds = self.state.runtime_seconds
        self.state.summary.token_usage = self.state.token_usage
        self.state.summary.cost_usage = self.state.cost_usage
        self.state.summary.decision_history = self.state.decision_history
        self.state.summary.failed_attempt_history = self.state.summary.failed_attempt_history
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
        self.mission_checkpoints.save(status, self.state)

    def run(self) -> ArbiterState:
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
        self.emit("mission_started", "Mission runtime created.", repo_path=self.spec.repo_path, branch_name=self.branch_name)
        self.persist("running")
        graph = build_workflow(self)
        graph.invoke({"status": "collect"})
        return self.state

    def node_collect(self) -> dict:
        self.state.repo_snapshot = self.collector.collect(run_commands=True)
        self.emit("repo_scan_completed", "Repository scan completed.", runtime=self.state.repo_snapshot.capabilities.runtime)
        self.persist("scanned")
        return {"status": "decompose"}

    def node_decompose(self) -> dict:
        assert self.state.repo_snapshot is not None
        self.state.tasks = self.decomposer.decompose(self.spec.objective, self.state.repo_snapshot)
        for task in self.state.tasks:
            self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
            self.emit("task_created", f"Task {task.task_id} created.", task_id=task.task_id, task_type=task.task_type.value)
        self.persist("decomposed")
        return {"status": "select_task"}

    def node_select_task(self) -> dict:
        for task in self.state.tasks:
            if task.status == TaskStatus.PENDING and all(self._task(dep).status == TaskStatus.COMPLETED for dep in task.dependencies):
                task.status = TaskStatus.READY
                self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
                self.emit("task_ready", f"Task {task.task_id} is ready.", task_id=task.task_id)
        ready = [task for task in self.state.tasks if task.status == TaskStatus.READY]
        if not ready:
            return {"status": "finalize"}
        self.state.active_task_id = ready[0].task_id
        return {"status": "market"}

    def node_market(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        bids = self.simulation.generate(task, self.state.repo_snapshot)
        available_tools = set(self.spec.allowed_tool_classes)
        failed = self.failed_families.setdefault(task.task_id, set())
        filtered = []
        for bid in bids:
            rejection = hard_filter_reason(bid, task, self.spec, available_tools, failed)
            if rejection:
                bid.rejection_reason = rejection
                self.store.save_record("bids", "bid_id", bid.bid_id, bid, task_id=bid.task_id, selected=0, standby=0)
                self.emit("bid_rejected", f"Bid {bid.bid_id} rejected.", bid_id=bid.bid_id, reason=rejection)
                continue
            bid.score = score_bid(bid)
            filtered.append(bid)
        contenders = cluster_and_select(filtered, per_family=2)
        if not contenders:
            self.state.no_valid_contenders = True
            return {"status": "finalize"}
        self.state.current_bid = contenders[0]
        self.state.standby_bid = contenders[1] if len(contenders) > 1 and contenders[1].can_be_standby else None
        self.store.save_record("bids", "bid_id", contenders[0].bid_id, contenders[0], task_id=task.task_id, selected=1, standby=0)
        self.emit("bid_won", f"Bid {contenders[0].bid_id} won.", task_id=task.task_id, bid_id=contenders[0].bid_id, role=contenders[0].role, score=contenders[0].score)
        if self.state.standby_bid:
            self.store.save_record("bids", "bid_id", self.state.standby_bid.bid_id, self.state.standby_bid, task_id=task.task_id, selected=0, standby=1)
            self.emit("standby_selected", f"Standby selected for {task.task_id}.", bid_id=self.state.standby_bid.bid_id, role=self.state.standby_bid.role)
        self.persist("marketed")
        return {"status": "execute"}

    def node_execute(self) -> dict:
        task = self._active_task()
        bid = self.state.current_bid
        assert bid is not None
        task.status = TaskStatus.RUNNING
        self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
        if task.task_type.value in {"localize", "perf_diagnosis", "validate"}:
            self.state.decision_history.append(f"{task.task_id}: evidence-only step completed.")
            self.emit("tool_executed", "Evidence-only task executed.", task_id=task.task_id)
            return {"status": "validate"}
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
            return {"status": "recover"}
        touched = self.toolset.apply_file_updates({item.path: item.content for item in proposal.files})
        from arbiter.core.contracts import ExecutionStep

        step = ExecutionStep(
            step_id=f"{task.task_id}-step-1",
            task_id=task.task_id,
            bid_id=bid.bid_id,
            action_type="edit",
            description=proposal.summary,
            tool_name="edit",
            input_payload={"files": bid.touched_files},
            output_payload={"touched": touched, "notes": proposal.notes},
        )
        self.store.save_record("execution_steps", "step_id", step.step_id, step, task_id=task.task_id, bid_id=bid.bid_id)
        self.emit("tool_executed", "Material edit applied.", task_id=task.task_id, touched_files=touched)
        return {"status": "validate"}

    def node_validate(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        if task.task_type.value in {"localize", "perf_diagnosis"}:
            from arbiter.core.contracts import ValidationReport

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
                self.emit("checkpoint_accepted", "Accepted checkpoint committed.", task_id=task.task_id, commit_sha=commit_sha)
            self.state.decision_history.append(f"{task.task_id}: completed")
            self.persist("validated")
            return {"status": "select_task"}
        details = "; ".join(report.notes) or "Validation failed."
        self.state.failure_context = FailureContext(
            task_id=task.task_id,
            failure_type="validation_failure",
            details=details,
            diff_summary=self.toolset.diff(),
            validator_deltas=[result.stderr or result.stdout for result in report.command_results if result.exit_code != 0][:5],
            recommended_recovery_scope="standby_or_rebid",
        )
        self.store.save_record("failure_contexts", "task_id", task.task_id, self.state.failure_context)
        self.emit("validation_failed", "Validation failed.", task_id=task.task_id, details=details)
        return {"status": "recover"}

    def node_recover(self) -> dict:
        task = self._active_task()
        assert self.state.failure_context is not None
        self.state.recovery_round += 1
        if self.accepted_checkpoint:
            self.toolset.revert_to_commit(self.accepted_checkpoint.commit_sha)
            self.emit("checkpoint_reverted", "Worktree reverted to accepted checkpoint.", commit_sha=self.accepted_checkpoint.commit_sha)
        if self.recovery.should_promote_standby(self.state.standby_bid, self.state.failure_context):
            self.state.current_bid = self.state.standby_bid
            self.state.standby_bid = None
            self.emit("standby_promoted", "Standby promoted after failure.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id)
            return {"status": "execute"}
        if self.state.current_bid:
            self.failed_families.setdefault(task.task_id, set()).add(self.state.current_bid.strategy_family)
            self.state.summary.failed_attempt_history.append(self.state.current_bid.strategy_summary)
        if self.state.recovery_round > self.spec.stop_policy.max_recovery_rounds:
            self.state.outcome = MissionOutcome.FAILED_EXECUTION
            return {"status": "finalize"}
        self.emit("recovery_round_opened", "Rebidding with prior evidence.", task_id=task.task_id, round=self.state.recovery_round)
        task.status = TaskStatus.READY
        self.store.save_record("tasks", "task_id", task.task_id, task, status=task.status.value)
        return {"status": "market"}

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
        self.emit("mission_finalized", "Mission finalized.", outcome=self.state.outcome.value)
        self.persist("finalized")
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
) -> ArbiterState:
    mission_id = generate_mission_id()
    config = load_runtime_config()
    spec = MissionSpec(
        mission_id=mission_id,
        repo_path=str(Path(repo).resolve()),
        objective=objective,
        constraints=constraints or [],
        preferences=preferences or [],
        max_runtime_minutes=max_runtime or config.max_runtime_minutes,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths or [],
        public_api_surface=public_api_surface or [],
    )
    paths = build_mission_paths(spec.repo_path, mission_id)
    Path(paths.metadata_path).write_text(json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8")
    runtime = MissionRuntime(spec, paths, strategy_backend=strategy_backend)
    started = time.perf_counter()
    state = runtime.run()
    state.runtime_seconds = time.perf_counter() - started
    runtime.persist("finished")
    runtime.store.close()
    return state


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
    state = runtime.run()
    runtime.store.close()
    return state


def mission_status(mission_id: str, repo: str) -> dict:
    paths = build_mission_paths(repo, mission_id)
    store = MissionStore(paths.db_path)
    row = store.fetch_mission()
    if row is None:
        store.close()
        raise ValueError(f"Mission {mission_id} not found.")
    summary = json.loads(row["summary_json"])
    events = store.fetch_all("events")
    payload = {
        "mission_id": mission_id,
        "status": row["status"],
        "outcome": row["outcome"],
        "branch_name": row["branch_name"],
        "decision_history": summary.get("decision_history", []),
        "failed_attempt_history": summary.get("failed_attempt_history", []),
        "event_count": len(events),
    }
    store.close()
    return payload
