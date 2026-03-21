from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from uuid import uuid4

from arbiter.agents.backend import DefaultStrategyBackend, ProviderModelRouter, load_candidate_files
from arbiter.civic.runtime import CivicRuntime
from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ActionIntent,
    ActivePhase,
    ArbiterState,
    BidGenerationMode,
    BidStatus,
    BiddingState,
    ExecutionStep,
    FailureContext,
    MissionControlState,
    MissionEvent,
    MissionOutcome,
    MissionSpec,
    PolicyDecision,
    PolicyState,
    RunState,
    SuccessCriteria,
    TaskNode,
    TaskRequirementLevel,
    TaskStatus,
    TaskType,
    ValidationReport,
    utc_now,
)
from arbiter.graph.checkpointer import MissionSqliteCheckpointer
from arbiter.graph.workflow import build_workflow
from arbiter.market.clustering import cluster_and_select
from arbiter.market.scoring import score_bid
from arbiter.mission.decomposer import GoalDecomposer
from arbiter.mission.governance import GovernanceEngine
from arbiter.mission.recovery import RecoveryEngine
from arbiter.mission.state import initialize_state
from arbiter.repo.collector import RepoStateCollector
from arbiter.repo.worktree import WorktreeManager
from arbiter.runtime.config import load_runtime_config
from arbiter.runtime.checkpoints import MissionCheckpointManager, RepoCheckpointManager
from arbiter.runtime.events import EventLogger
from arbiter.runtime.migrate import migrate_legacy_mission
from arbiter.runtime.paths import build_mission_paths, generate_mission_id, resolve_repo_path, sanitize_branch_fragment
from arbiter.runtime.persistence import PersistenceCoordinator
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
        migrate_legacy_mission(paths, spec.mission_id)
        self.config = load_runtime_config()
        self.store = MissionStore(paths.db_path)
        self.events = EventLogger(paths.events_path)
        self.persistence = PersistenceCoordinator(spec.mission_id, self.store, self.events)
        self.replay = ReplayManager(self.store, paths.replay_dir, mode=self.config.replay_mode, mission_id=spec.mission_id)
        self.router = ProviderModelRouter(self.config, self.replay)
        self.strategy_backend = strategy_backend or DefaultStrategyBackend(self.router)
        self.collector = RepoStateCollector(spec.repo_path)
        self.decomposer = GoalDecomposer()
        self.governance = GovernanceEngine()
        self.simulation = SimulationFactory(
            max_workers=self.config.max_parallel_bidders,
            backend=self.strategy_backend,
            bidder_models=self.config.bidder_models,
            provider_pool=self.config.enabled_providers if hasattr(self.strategy_backend, "router") else [],
            on_invocation=self._record_model_invocation,
        )
        self.recovery = RecoveryEngine()
        self.civic = CivicRuntime(self.config)
        self.branch_name = f"codex/arbiter-{sanitize_branch_fragment(spec.mission_id)}"
        self.mission_checkpoints = MissionCheckpointManager(spec.mission_id, self.store)
        self.repo_checkpoints = RepoCheckpointManager(spec.mission_id, self.branch_name, self.store)
        self.worktree = WorktreeManager(spec.repo_path, paths.worktree_dir, self.branch_name)
        self.toolset = LocalToolset(paths.worktree_dir)
        self.graph_checkpointer = MissionSqliteCheckpointer(paths.db_path)
        self.workflow = build_workflow(self, checkpointer=self.graph_checkpointer)
        self.state = initialize_state(spec)
        self.state.summary.mission_id = spec.mission_id
        self.state.summary.repo_path = spec.repo_path
        self.state.summary.objective = spec.objective
        self.state.summary.branch_name = self.branch_name
        self.state.bidding_state = BiddingState(
            generation_mode=self._backend_bidding_mode(),
            require_provider_backed_bids=spec.bidding_policy.require_provider_backed_bids,
            allow_degraded_fallback=spec.bidding_policy.allow_degraded_fallback,
        )
        self.failed_families: dict[str, set[str]] = {}
        self._invocation_lock = threading.RLock()
        self._provider_invocations_seen: set[str] = {
            row["id"]
            for row in self.store.fetch_model_invocations(spec.mission_id)
            if (row["generation_mode"] or json.loads(row["payload_json"]).get("generation_mode")) == BidGenerationMode.PROVIDER_MODEL.value
        }
        self.state.bidding_state.total_provider_invocations = len(self._provider_invocations_seen)

    def emit(self, event_type: str, message: str, refresh_view: bool = False, **payload) -> None:
        event = MissionEvent(event_type=event_type, mission_id=self.spec.mission_id, message=message, payload=payload)
        self.persistence.append_event(event, refresh_view=refresh_view)

    def trace(self, trace_type: str, title: str, message: str, *, status: str = "info", task_id: str | None = None, bid_id: str | None = None, provider: str | None = None, lane: str | None = None, refresh_view: bool = False, **payload) -> None:
        self.persistence.append_trace(
            trace_type=trace_type,
            title=title,
            message=message,
            status=status,
            task_id=task_id,
            bid_id=bid_id,
            provider=provider,
            lane=lane,
            refresh_view=refresh_view,
            **payload,
        )

    def _record_model_invocation(self, payload: dict) -> None:
        with self._invocation_lock:
            invocation_id = self.persistence.save_model_invocation(payload)
            generation_mode = payload.get("generation_mode")
            if generation_mode is not None and not isinstance(generation_mode, BidGenerationMode):
                generation_mode = BidGenerationMode(generation_mode)
            if generation_mode == BidGenerationMode.PROVIDER_MODEL and invocation_id not in self._provider_invocations_seen:
                self._provider_invocations_seen.add(invocation_id)
                self.state.bidding_state.total_provider_invocations = len(self._provider_invocations_seen)
            self.trace(
                f"model.invocation.{payload['status']}",
                title=f"Model invocation {payload['status']}",
                message=f"{payload.get('provider', 'unknown')} {payload.get('invocation_kind', 'invocation')} {payload['status']}.",
                status="danger" if payload["status"] == "failed" else "info" if payload["status"] == "started" else "success",
                task_id=payload.get("task_id"),
                bid_id=payload.get("bid_id"),
                provider=payload.get("provider"),
                lane=payload.get("lane"),
                invocation_id=invocation_id,
                model_id=payload.get("model_id"),
                generation_mode=payload.get("generation_mode"),
                prompt_preview=payload.get("prompt_preview"),
                response_preview=payload.get("response_preview"),
                raw_usage=payload.get("raw_usage", {}),
                token_usage=payload.get("token_usage"),
                cost_usage=payload.get("cost_usage"),
                usage_unavailable_reason=payload.get("usage_unavailable_reason"),
                error=payload.get("error"),
            )

    def _backend_bidding_mode(self) -> BidGenerationMode:
        if hasattr(self.strategy_backend, "market_generation_mode"):
            return self.strategy_backend.market_generation_mode()
        return BidGenerationMode.DETERMINISTIC_FALLBACK

    def _update_bidding_state(self, *, generation_mode: BidGenerationMode, warning: str | None = None, architecture_violation: str | None = None) -> None:
        self.state.bidding_state.generation_mode = generation_mode
        self.state.bidding_state.degraded = generation_mode == BidGenerationMode.DETERMINISTIC_FALLBACK
        self.state.bidding_state.warning = warning
        self.state.bidding_state.architecture_violation = architecture_violation
        self.state.bidding_state.total_provider_invocations = len(self._provider_invocations_seen)

    def _set_bidding_metrics(self, bids: list, provider_invocation_ids: list[str] | None = None) -> None:
        if provider_invocation_ids is not None:
            self.state.bidding_state.round_provider_invocations = len({item for item in provider_invocation_ids if item})
        self.state.bidding_state.total_provider_invocations = len(self._provider_invocations_seen)
        self.state.bidding_state.active_provider_bids = sum(
            1 for bid in bids if bid.generation_mode == BidGenerationMode.PROVIDER_MODEL
        )
        self.state.bidding_state.active_fallback_bids = sum(
            1 for bid in bids if bid.generation_mode == BidGenerationMode.DETERMINISTIC_FALLBACK
        )

    def _checkpoint_label(self, status: str) -> str:
        phase = self.state.active_phase.value if isinstance(self.state.active_phase, ActivePhase) else str(self.state.active_phase)
        task_fragment = self.state.active_task_id or "idle"
        return f"{status}:{phase}:{task_fragment}:r{self.state.active_bid_round}:rr{self.state.recovery_round}"

    def _restore_runtime_context(self) -> None:
        if self.state.repo_snapshot is None and self.state.active_phase not in {ActivePhase.IDLE, ActivePhase.COLLECT}:
            self.state.repo_snapshot = self.collector.collect(run_commands=True)
        # Map legacy phases to strategize
        if self.state.active_phase in {ActivePhase.DECOMPOSE, ActivePhase.SELECT_TASK, ActivePhase.MARKET}:
            self.state.active_phase = ActivePhase.STRATEGIZE
        if self.state.current_bid is None and self.state.winner_bid_id:
            self.state.current_bid = next((bid for bid in self.state.active_bids if bid.bid_id == self.state.winner_bid_id), None)
        if self.state.standby_bid is None and self.state.standby_bid_id:
            self.state.standby_bid = next((bid for bid in self.state.active_bids if bid.bid_id == self.state.standby_bid_id), None)
        self.state.summary.branch_name = self.branch_name

    def _fail_bidding_round(self, task_id: str, reason: str) -> dict:
        self._update_bidding_state(generation_mode=self.state.bidding_state.generation_mode, architecture_violation=reason)
        self._set_bidding_metrics([])
        self.emit(
            "bidding.architecture_violation",
            "Bidding architecture violation detected.",
            task_id=task_id,
            reason=reason,
            refresh_view=True,
        )
        self.trace(
            "bidding.architecture_violation",
            "Architecture violation",
            reason,
            task_id=task_id,
            status="danger",
            refresh_view=True,
        )
        self.state.outcome = MissionOutcome.FAILED_EXECUTION
        self.state.governance.stop_reason = reason
        self.state.no_valid_contenders = True
        return {"status": ActivePhase.FINALIZE.value}

    def _refresh_worktree_state(self, reason: str | None = None) -> None:
        state = self.toolset.worktree_state()
        state["branch_name"] = self.branch_name
        state["accepted_checkpoint_id"] = self.state.accepted_checkpoint.checkpoint_id if self.state.accepted_checkpoint else None
        state["accepted_commit"] = self.state.accepted_checkpoint.commit_sha if self.state.accepted_checkpoint else None
        if not state["has_changes"]:
            state["reason"] = reason or ("No uncommitted repo changes; latest accepted checkpoint is current." if self.state.accepted_checkpoint else "No repo changes yet.")
        elif reason:
            state["reason"] = reason
        self.state.worktree_state = state
        self.state.latest_diff_summary = str(state.get("diff_stat", self.state.latest_diff_summary))

    @staticmethod
    def _proposal_score(candidate, bid, task) -> float:
        files = [item.path for item in candidate.proposal.files]
        if not files:
            return -1.0
        score = 1.0
        if candidate.provider == bid.provider and bid.provider not in {None, "system"}:
            score += 0.15
        if len(files) <= max(1, len(bid.touched_files or task.candidate_files or files)):
            score += 0.2
        score -= max(0, len(files) - task.risk_level * 10) * 0.03
        score += min(0.25, len(candidate.invocation.token_usage or {}) * 0.02)
        return score

    def _sync_state(self, status: str) -> None:
        self._restore_runtime_context()
        self.state.summary.branch_name = self.branch_name
        if self.state.accepted_checkpoint:
            self.state.summary.head_commit = self.state.accepted_checkpoint.commit_sha
        self.state.summary.token_usage = dict(self.state.token_usage)
        self.state.summary.cost_usage = dict(self.state.cost_usage)
        self.state.summary.decision_history = list(self.state.decision_history)
        self.state.summary.runtime_seconds = self.state.runtime_seconds
        self.state.summary.outcome = self.state.outcome
        self.state.summary.bidding_state = self.state.bidding_state.model_dump(mode="json")
        control_row = self.store.fetch_control_state(self.spec.mission_id)
        run_state = self.state.control.run_state.value
        requested_action = self.state.control.requested_action
        reason = self.state.control.reason
        if control_row:
            if not requested_action and control_row["requested_action"]:
                requested_action = control_row["requested_action"]
                reason = control_row["reason"] or reason
            if run_state == RunState.RUNNING.value and control_row["run_state"] != RunState.RUNNING.value:
                run_state = control_row["run_state"]
        self.store.upsert_mission(
            mission_id=self.spec.mission_id,
            status=status,
            repo_path=self.spec.repo_path,
            objective=self.spec.objective,
            branch_name=self.branch_name,
            outcome=self.state.outcome.value if self.state.outcome else None,
            spec=self.spec,
            summary=self.state.summary,
            created_at=self.spec.created_at.isoformat(),
        )
        self.store.upsert_runtime(
            mission_id=self.spec.mission_id,
            active_phase=self.state.active_phase.value,
            active_task_id=self.state.active_task_id,
            active_bid_round=self.state.active_bid_round,
            simulation_round=self.state.simulation_summary.budget_used if self.state.simulation_summary else 0,
            recovery_round=self.state.recovery_round,
            winner_bid_id=self.state.winner_bid_id,
            standby_bid_id=self.state.standby_bid_id,
            latest_diff_summary=self.state.latest_diff_summary,
            stop_reason=self.state.governance.stop_reason,
            policy_state=self.state.governance.policy_state.value,
            current_risk_score=self.state.governance.current_risk_score,
            simulation_summary=self.state.simulation_summary,
            worktree_state=self.state.worktree_state,
            bidding_state=self.state.bidding_state.model_dump(mode="json"),
            latest_validation_task_id=self.state.validation_report.task_id if self.state.validation_report else None,
            latest_failure_task_id=self.state.failure_context.task_id if self.state.failure_context else None,
            accepted_checkpoint_id=self.state.accepted_checkpoint.checkpoint_id if self.state.accepted_checkpoint else None,
        )
        self.store.upsert_control_state(
            mission_id=self.spec.mission_id,
            run_state=run_state,
            requested_action=requested_action,
            reason=reason,
            updated_at=self.state.control.updated_at.isoformat(),
        )
        self.mission_checkpoints.save(self._checkpoint_label(status), self.state)
        self.store.refresh_mission_view(self.spec.mission_id)

    def _save_task(self, task) -> None:
        self.store.save_task(
            mission_id=self.spec.mission_id,
            task=task,
            task_id=task.task_id,
            title=task.title,
            task_type=task.task_type.value,
            status=task.status.value,
            required=task.required,
            dependencies=task.dependencies,
        )

    def _save_bid(self, bid, round_index: int) -> None:
        self.store.save_bid(
            mission_id=self.spec.mission_id,
            bid=bid,
            bid_id=bid.bid_id,
            task_id=bid.task_id,
            role=bid.role,
            strategy_family=bid.strategy_family,
            score=bid.score,
            risk=bid.risk,
            cost=bid.cost,
            confidence=bid.confidence,
            is_winner=bid.status == BidStatus.WINNER,
            is_standby=bid.status == BidStatus.STANDBY,
            status=bid.status.value,
            round_index=round_index,
        )

    def _save_execution_step(self, step: ExecutionStep) -> None:
        self.store.save_execution_step(
            mission_id=self.spec.mission_id,
            step=step,
            step_id=step.step_id,
            task_id=step.task_id,
            action=step.action_type,
            result=json.dumps(step.output_payload),
            timestamp=step.created_at.isoformat(),
        )

    def _save_validation(self, report: ValidationReport) -> None:
        self.store.save_validation_report(
            mission_id=self.spec.mission_id,
            report=report,
            record_id=f"{report.task_id}-{uuid4().hex[:8]}",
            task_id=report.task_id,
            passed=report.passed,
            details=report.details,
            timestamp=utc_now().isoformat(),
        )

    def _save_failure(self, failure: FailureContext) -> None:
        self.store.save_failure_context(
            mission_id=self.spec.mission_id,
            failure=failure,
            record_id=f"{failure.task_id}-{uuid4().hex[:8]}",
            task_id=failure.task_id,
            failure_type=failure.failure_type,
            details=failure.details,
            diff_summary=failure.diff_summary,
            strategy_family=failure.strategy_family,
            timestamp=failure.created_at.isoformat(),
        )

    def _save_checkpoint(self, checkpoint: AcceptedCheckpoint) -> None:
        self.store.save_accepted_checkpoint(self.spec.mission_id, checkpoint)
        self.repo_checkpoints.save(
            checkpoint,
            accepted=True,
            checkpoint_kind="accepted",
            label=checkpoint.label,
            worktree_state=self.state.worktree_state,
        )

    def _load_failed_families(self) -> None:
        self.failed_families = {}
        for row in self.store.fetch_all("failure_contexts", self.spec.mission_id):
            payload = json.loads(row["payload_json"])
            family = payload.get("strategy_family")
            if family:
                self.failed_families.setdefault(payload["task_id"], set()).add(family)

    def _prepare_run(self) -> str:
        self.persistence.reconcile_jsonl()
        self.worktree.ensure()
        self._refresh_worktree_state("Mission prepared in an isolated worktree.")
        self._load_failed_families()
        if self.state.control.run_state in {RunState.IDLE, RunState.FINALIZED, RunState.PAUSED}:
            self.state.control = MissionControlState(run_state=RunState.RUNNING, reason=self.state.control.reason)
        self._sync_state("running")
        if self.state.accepted_checkpoint is None:
            checkpoint = AcceptedCheckpoint(
                checkpoint_id=f"{self.spec.mission_id}-accepted-0",
                label="initial",
                commit_sha=self.toolset.git_head(),
                summary="Initial worktree head.",
            )
            self.state.accepted_checkpoint = checkpoint
            self._save_checkpoint(checkpoint)
        self._refresh_worktree_state("Mission starting from the latest accepted checkpoint.")
        if not self.store.fetch_all("events", self.spec.mission_id):
            self.emit("mission.started", "Mission runtime created. Strategy market will govern execution.", repo_path=self.spec.repo_path, branch_name=self.branch_name)
        phase = self.state.active_phase
        if phase == ActivePhase.IDLE:
            return ActivePhase.COLLECT.value
        # Map legacy phases to strategize for resumed missions
        if phase in {ActivePhase.DECOMPOSE, ActivePhase.SELECT_TASK, ActivePhase.MARKET}:
            return ActivePhase.STRATEGIZE.value
        return phase.value

    def _emit_phase_change(self, previous_phase: ActivePhase | None, next_phase: ActivePhase) -> None:
        if previous_phase == next_phase:
            return
        self.emit(
            "phase.changed",
            f"Mission phase changed to {next_phase.value}.",
            phase=next_phase.value,
            previous_phase=previous_phase.value if previous_phase else None,
            task_id=self.state.active_task_id,
            round=self.state.active_bid_round,
            recovery_round=self.state.recovery_round,
            refresh_view=True,
        )
        self.trace(
            "phase.changed",
            "Phase changed",
            f"{(previous_phase.value if previous_phase else 'idle')} -> {next_phase.value}",
            task_id=self.state.active_task_id,
            status="info",
            refresh_view=True,
        )

    def _cooperate(self) -> None:
        control_row = self.store.fetch_control_state(self.spec.mission_id)
        if control_row:
            self.state.control = MissionControlState(
                run_state=RunState(control_row["run_state"]),
                requested_action=control_row["requested_action"],
                reason=control_row["reason"],
            )
        if self.state.control.requested_action == "cancel":
            self.state.control = MissionControlState(run_state=RunState.CANCELLING, reason=self.state.control.reason)
            raise MissionCancelled(self.state.control.reason or "user_cancelled")
        if self.state.control.requested_action == "pause":
            self.state.control = MissionControlState(run_state=RunState.PAUSED, reason=self.state.control.reason)
            self.emit("mission.paused", "Mission paused.", reason=self.state.control.reason, refresh_view=True)
            self._sync_state("paused")
            raise MissionPaused(self.state.control.reason or "user_paused")

    def _workflow_config(self) -> dict:
        return {
            "configurable": {
                "thread_id": self.spec.mission_id,
                "checkpoint_ns": "mission",
            }
        }

    def _workflow_payload(self, status: str | None = None) -> dict:
        return {
            "status": status or self.state.active_phase.value,
            "runtime_state": self.state.model_dump(mode="json"),
        }

    def _restore_from_workflow_state(self, workflow_state: dict | None) -> None:
        if workflow_state and workflow_state.get("runtime_state"):
            self.state = ArbiterState.model_validate(workflow_state["runtime_state"])
        self._restore_runtime_context()

    def workflow_bootstrap(self, workflow_state: dict) -> dict:
        self._restore_from_workflow_state(workflow_state)
        return self._workflow_payload(workflow_state.get("status", self.state.active_phase.value))

    def _run_workflow_node(self, workflow_state: dict, phase: ActivePhase, handler_name: str) -> dict:
        self._restore_from_workflow_state(workflow_state)
        self._cooperate()
        previous_phase = self.state.active_phase
        self._emit_phase_change(previous_phase, phase)
        self.state.active_phase = phase
        self._sync_state("running")
        next_status = getattr(self, handler_name)()["status"]
        return self._workflow_payload(next_status)

    def workflow_collect(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.COLLECT, "node_collect")

    def workflow_strategize(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.STRATEGIZE, "node_strategize")

    def workflow_simulate(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.SIMULATE, "node_simulate")

    def workflow_select(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.SELECT, "node_select")

    def workflow_execute(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.EXECUTE, "node_execute")

    def workflow_validate(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.VALIDATE, "node_validate")

    def workflow_recover(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.RECOVER, "node_recover")

    def workflow_finalize(self, workflow_state: dict) -> dict:
        return self._run_workflow_node(workflow_state, ActivePhase.FINALIZE, "node_finalize")

    def run(self) -> ArbiterState:
        started = time.perf_counter()
        status = self._prepare_run()
        config = self._workflow_config()
        try:
            checkpoint = self.graph_checkpointer.get_tuple(config)
            result = self.workflow.invoke(None if checkpoint else self._workflow_payload(status), config=config)
            self._restore_from_workflow_state(result)
            return self.state
        except MissionPaused:
            return self.state
        except MissionCancelled as exc:
            self.state.outcome = MissionOutcome.FAILED_SAFE_STOP
            self.state.governance.stop_reason = str(exc)
            self.state.control = MissionControlState(run_state=RunState.FINALIZED, reason=str(exc))
            self.emit("mission.cancelled", "Mission cancelled.", reason=str(exc), refresh_view=True)
            return self.state
        finally:
            self.state.runtime_seconds += time.perf_counter() - started
            self._sync_state("finalized" if self.state.control.run_state == RunState.FINALIZED else self.state.control.run_state.value)

    def node_collect(self) -> dict:
        self.state.repo_snapshot = self.collector.collect(run_commands=True)
        repo_decision = self.governance.evaluate_repo(self.state.repo_snapshot, self.spec)
        self.state.governance.last_decision = repo_decision
        self.state.governance.current_risk_score = repo_decision.risk_score
        if not repo_decision.allowed:
            self.state.outcome = MissionOutcome.POLICY_BLOCKED
            self.state.governance.policy_state = PolicyState.BLOCKED
            self.state.governance.stop_reason = "; ".join(repo_decision.reasons)
            return {"status": ActivePhase.FINALIZE.value}
        self.emit(
            "repo.scan.completed",
            "Repository scan completed.",
            runtime=self.state.repo_snapshot.capabilities.runtime,
            risky_paths=self.state.repo_snapshot.capabilities.risky_paths,
            baseline_tests=len(self.state.repo_snapshot.initial_test_results),
            baseline_lint=len(self.state.repo_snapshot.initial_lint_results),
            baseline_static=len(self.state.repo_snapshot.initial_static_results),
        )
        return {"status": ActivePhase.STRATEGIZE.value}

    def _build_mission_context(self) -> dict:
        """Gather full mission context for the strategy market."""
        completed = [t for t in self.state.tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in self.state.tasks if t.status == TaskStatus.FAILED]
        return {
            "objective": self.spec.objective,
            "constraints": self.spec.constraints,
            "preferences": self.spec.preferences,
            "completed_moves": [f"{t.task_id}: {t.title}" for t in completed],
            "failed_moves": [f"{t.task_id}: {t.title}" for t in failed],
            "strategy_round": self.state.strategy_round,
            "mission_landscape": self.state.mission_landscape,
            "failure_context": self.state.failure_context.details if self.state.failure_context else None,
            "failed_families": {tid: sorted(fams) for tid, fams in self.failed_families.items()},
            "decision_history": self.state.decision_history[-8:],
        }

    def _assess_mission_progress(self) -> str | None:
        """Return a finalize reason if the mission objective is met, or None to continue."""
        required = [t for t in self.state.tasks if t.required]
        if required and all(t.status == TaskStatus.COMPLETED for t in required):
            return "all_required_tasks_completed"
        stop = self.governance.evaluate_stop(self.state)
        if stop.should_stop:
            self.state.governance.stop_reason = stop.reason
            self.state.outcome = stop.outcome or self.state.outcome
            return stop.reason
        return None

    def _generate_landscape(self) -> None:
        """Run decomposition as advisory context for the strategy market (first round only)."""
        assert self.state.repo_snapshot is not None
        advisory_tasks = self.decomposer.decompose(
            self.spec.objective,
            self.state.repo_snapshot,
            spec=self.spec,
            strategy_backend=self.strategy_backend,
            on_invocation=self._record_model_invocation,
        )
        self.state.mission_landscape = [
            f"{t.task_type.value}: {t.title} (risk={t.risk_level:.2f}, files={','.join(t.candidate_files[:3])})"
            for t in advisory_tasks
        ]
        source = self.decomposer.last_plan_source
        candidate_scores = self.decomposer.last_candidate_scores
        self.trace(
            "strategy.landscape_generated",
            "Mission landscape generated",
            f"Advisory landscape with {len(advisory_tasks)} potential moves ({source}).",
            status="info",
            source=source,
            candidate_scores=candidate_scores,
            landscape=self.state.mission_landscape,
            refresh_view=True,
        )

    def _synthesize_task_for_round(self, task_type_hint: str | None = None) -> TaskNode:
        """Create a task node for this strategy round. The market drives what it becomes."""
        round_num = self.state.strategy_round
        objective_lower = self.spec.objective.lower()

        # Infer task type from mission context if no hint
        if task_type_hint:
            try:
                task_type = TaskType(task_type_hint)
            except ValueError:
                task_type = TaskType.BUGFIX
        elif not self.state.tasks:
            # First round: start with localization/investigation
            if any(w in objective_lower for w in ("bug", "fail", "error", "fix", "test")):
                task_type = TaskType.BUGFIX
            elif any(w in objective_lower for w in ("perf", "slow", "latency", "speed")):
                task_type = TaskType.PERF_OPTIMIZE
            elif any(w in objective_lower for w in ("refactor", "clean", "structure")):
                task_type = TaskType.REFACTOR
            else:
                task_type = TaskType.BUGFIX
        else:
            completed_types = {t.task_type for t in self.state.tasks if t.status == TaskStatus.COMPLETED}
            if TaskType.VALIDATE not in completed_types and len(completed_types) >= 2:
                task_type = TaskType.VALIDATE
            elif TaskType.TEST not in completed_types and any(t.task_type == TaskType.BUGFIX and t.status == TaskStatus.COMPLETED for t in self.state.tasks):
                task_type = TaskType.TEST
            else:
                task_type = TaskType.BUGFIX

        candidate_files = []
        if self.state.repo_snapshot:
            candidate_files = (
                self.state.repo_snapshot.changed_files[:4]
                + self.state.repo_snapshot.complexity_hotspots[:3]
                + self.state.repo_snapshot.capabilities.risky_paths[:2]
            )
            candidate_files = list(dict.fromkeys(candidate_files))[:8]

        task_id = f"S{round_num}_{task_type.value}"
        task = TaskNode(
            task_id=task_id,
            title=f"Strategy round {round_num}: {self.spec.objective[:80]}",
            task_type=task_type,
            requirement_level=TaskRequirementLevel.REQUIRED,
            dependencies=[],
            success_criteria=SuccessCriteria(
                description=f"Market-driven move toward: {self.spec.objective[:120]}",
                required_validators=["tests"] if task_type.value not in {"localize", "perf_diagnosis", "validate"} else [],
            ),
            allowed_tools=["read_file", "search_code", "edit_file", "run_tests", "run_lint", "revert_to_checkpoint"],
            validator_requirements=["tests"] if task_type.value not in {"localize", "perf_diagnosis"} else [],
            risk_level=0.35,
            runtime_class="medium",
            search_depth=3,
            monte_carlo_samples=28,
            candidate_files=candidate_files,
            strategy_families=["Speed", "Safe", "Quality", "Test", "Performance"],
            status=TaskStatus.READY,
        )
        return task

    def node_strategize(self) -> dict:
        assert self.state.repo_snapshot is not None
        self.state.strategy_round += 1

        # --- 1. Generate advisory landscape on first round ---
        if self.state.strategy_round == 1 and not self.state.mission_landscape:
            self._generate_landscape()

        # --- 2. Assess mission progress ---
        stop_reason = self._assess_mission_progress()
        if stop_reason:
            self.trace(
                "strategy.objective_met",
                "Mission objective assessed",
                f"Strategy market closing: {stop_reason}",
                status="success",
                refresh_view=True,
            )
            return {"status": ActivePhase.FINALIZE.value}

        # --- 3. Open strategy market ---
        self.emit(
            "strategy.market_opened",
            f"Strategy market opened for round {self.state.strategy_round}.",
            round=self.state.strategy_round,
            refresh_view=True,
        )
        mission_context = self._build_mission_context()
        self.trace(
            "strategy.market_opened",
            "Strategy market opened",
            f"Competing strategies will propose the next best move for round {self.state.strategy_round}.",
            status="info",
            round=self.state.strategy_round,
            completed_moves=mission_context["completed_moves"],
            failed_moves=mission_context["failed_moves"],
            landscape=self.state.mission_landscape[:5],
            providers=self.config.enabled_providers,
        )

        # --- 4. Synthesize round task and generate bids ---
        task = self._synthesize_task_for_round()
        self.state.tasks.append(task)
        self._save_task(task)
        self.state.active_task_id = task.task_id
        self.emit("task.created", f"Strategy move {task.task_id} opened for competitive bidding.", task_id=task.task_id, task_type=task.task_type.value)

        self.state.active_bid_round += 1
        failed = self.failed_families.setdefault(task.task_id, set())

        batch = self.simulation.generate(
            task,
            self.state.repo_snapshot,
            allow_fallback=self.spec.bidding_policy.allow_degraded_fallback,
            mission_context=mission_context,
        )
        bids = batch.bids
        self._merge_usage(self.simulation.market_token_usage, self.simulation.market_cost_usage)
        self._update_bidding_state(generation_mode=batch.generation_mode, warning=batch.degraded_reason)
        self._set_bidding_metrics([], provider_invocation_ids=batch.provider_invocation_ids)

        if batch.generation_mode == BidGenerationMode.DETERMINISTIC_FALLBACK:
            self.emit(
                "bidding.degraded_mode_entered",
                "Strategy market entered degraded fallback mode.",
                task_id=task.task_id,
                reason=batch.degraded_reason,
                provider_errors=batch.provider_errors,
                generation_mode=batch.generation_mode.value,
                refresh_view=True,
            )
            self.trace(
                "bidding.degraded_mode_entered",
                "Degraded strategy mode",
                batch.degraded_reason or "Strategy market entered deterministic fallback mode.",
                task_id=task.task_id,
                status="warning",
                generation_mode=batch.generation_mode.value,
                provider_errors=batch.provider_errors,
                refresh_view=True,
            )
        if self.spec.bidding_policy.require_provider_backed_bids and batch.generation_mode != BidGenerationMode.PROVIDER_MODEL:
            if not (batch.generation_mode == BidGenerationMode.DETERMINISTIC_FALLBACK and self.spec.bidding_policy.allow_degraded_fallback and bids):
                reason = batch.degraded_reason or f"Expected provider-backed strategies, but market ran in {batch.generation_mode.value} mode."
                return self._fail_bidding_round(task.task_id, reason)

        # --- 5. Policy filter, score, cluster ---
        valid: list = []
        for bid in bids:
            if bid.generation_mode == BidGenerationMode.PROVIDER_MODEL and not bid.invocation_id:
                return self._fail_bidding_round(task.task_id, f"Provider-backed strategy {bid.bid_id} is missing an invocation reference.")
            decision = self.governance.evaluate_bid(task, bid, self.spec, failed)
            bid.policy_feasibility = decision
            if not decision.allowed:
                bid.rejection_reason = "; ".join(decision.reasons)
                bid.status = BidStatus.REJECTED
                self._save_bid(bid, self.state.active_bid_round)
                self.emit(
                    "bid.rejected",
                    f"Strategy {bid.bid_id} rejected.",
                    bid_id=bid.bid_id,
                    task_id=task.task_id,
                    reason=bid.rejection_reason,
                    role=bid.role,
                    provider=bid.provider,
                    lane=bid.lane,
                    model_id=bid.model_id,
                    invocation_id=bid.invocation_id,
                    generation_mode=bid.generation_mode.value,
                    token_usage=bid.token_usage,
                    cost_usage=bid.cost_usage,
                    usage_unavailable_reason=bid.usage_unavailable_reason,
                )
                self.trace("bid.retired", "Strategy rejected", f"{bid.bid_id} was rejected before scoring.", task_id=task.task_id, bid_id=bid.bid_id, provider=bid.provider, lane=bid.lane, status="danger", reason=bid.rejection_reason)
                continue
            bid.score = score_bid(bid)
            valid.append(bid)

        self.state.active_bids = cluster_and_select(valid, per_family=1, max_candidates=7)
        self._set_bidding_metrics(self.state.active_bids, provider_invocation_ids=batch.provider_invocation_ids)
        for bid in self.state.active_bids:
            self._save_bid(bid, self.state.active_bid_round)
            self.emit(
                "bid.submitted",
                f"Strategy {bid.bid_id} competing.",
                bid_id=bid.bid_id,
                task_id=task.task_id,
                role=bid.role,
                score=bid.score,
                strategy_family=bid.strategy_family,
                mission_rationale=bid.mission_rationale,
                provider=bid.provider,
                lane=bid.lane,
                model_id=bid.model_id,
                invocation_id=bid.invocation_id,
                invocation_kind=bid.invocation_kind,
                generation_mode=bid.generation_mode.value,
                token_usage=bid.token_usage,
                cost_usage=bid.cost_usage,
                usage_unavailable_reason=bid.usage_unavailable_reason,
                estimated_runtime_seconds=bid.estimated_runtime_seconds,
                touched_files=bid.touched_files,
                validator_plan=bid.validator_plan,
                rollback_plan=bid.rollback_plan,
                risk=bid.risk,
                cost=bid.cost,
                confidence=bid.confidence,
            )
            self.trace(
                "bid.generated",
                "Strategy entered market",
                f"{bid.bid_id} is competing for the next mission move.",
                task_id=task.task_id,
                bid_id=bid.bid_id,
                provider=bid.provider,
                lane=bid.lane,
                status="success",
                score=bid.score,
                strategy_family=bid.strategy_family,
                mission_rationale=bid.mission_rationale,
                model_id=bid.model_id,
                invocation_id=bid.invocation_id,
                generation_mode=bid.generation_mode.value,
                usage_unavailable_reason=bid.usage_unavailable_reason,
            )
        if not self.state.active_bids:
            reason = batch.degraded_reason or "Strategy market produced no valid contenders."
            if self.spec.bidding_policy.require_provider_backed_bids:
                return self._fail_bidding_round(task.task_id, reason)
            self.state.no_valid_contenders = True
            return {"status": ActivePhase.FINALIZE.value}
        return {"status": ActivePhase.SIMULATE.value}

    def node_simulate(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        rollout_plan = self.simulation.rollout_plan(task, self.state.active_bids, failure_count=len(self.failed_families.get(task.task_id, set())))
        paper_ids = set(rollout_plan["paper"])
        partial_ids = set(rollout_plan["partial"])
        sandbox_ids = set(rollout_plan["sandbox"])
        base_ref = self.state.accepted_checkpoint.commit_sha if self.state.accepted_checkpoint else "HEAD"
        for bid in self.state.active_bids:
            evidence: list[str] = []
            if bid.bid_id in paper_ids:
                evidence.append("paper")
                self.trace("simulation.rollout", "Paper rollout", f"Paper rollout completed for {bid.bid_id}.", task_id=task.task_id, bid_id=bid.bid_id, provider=bid.provider, lane=bid.lane, status="info", rollout="paper")
            if bid.bid_id in partial_ids:
                files = load_candidate_files(self.paths.worktree_dir, bid.touched_files or task.candidate_files)
                evidence.append("partial")
                self.trace("simulation.rollout", "Partial rollout", f"Partial rollout completed for {bid.bid_id}.", task_id=task.task_id, bid_id=bid.bid_id, provider=bid.provider, lane=bid.lane, status="info", rollout="partial", files=list(files))
            if bid.bid_id in sandbox_ids:
                scratch = Path(self.paths.scratch_worktrees_dir) / bid.bid_id
                self.worktree.ensure_detached(str(scratch), ref=base_ref)
                try:
                    scratch_tools = LocalToolset(str(scratch))
                    files = load_candidate_files(str(scratch), bid.touched_files or task.candidate_files)
                    if hasattr(self.strategy_backend, "scripted"):
                        evidence.append("sandbox:heuristic")
                    else:
                        proposal, invocation = self.strategy_backend.generate_edit_proposal(task=task, bid=bid, mission_objective=self.spec.objective, candidate_files=files, failure_context=self.state.failure_context.details if self.state.failure_context else None, preview=True)
                        self._merge_usage(invocation.token_usage, invocation.cost_usage)
                        if proposal.files:
                            scratch_tools.apply_file_updates({item.path: item.content for item in proposal.files})
                            report = ValidationEngine(scratch_tools, self.spec, self.state.repo_snapshot).validate(task)
                            evidence.append("sandbox:pass" if report.passed else "sandbox:fail")
                        else:
                            evidence.append("sandbox:no_patch")
                finally:
                    self.worktree.remove_path(str(scratch))
                self.trace("simulation.rollout", "Sandbox rollout", f"Sandbox rollout completed for {bid.bid_id}.", task_id=task.task_id, bid_id=bid.bid_id, provider=bid.provider, lane=bid.lane, status="info", rollout="sandbox")
            diagnostics = self.simulation.evaluate_search(
                task,
                bid,
                rollout_evidence=evidence,
                failure_count=len(self.failed_families.get(task.task_id, set())),
            )
            bid.search_diagnostics = diagnostics
            bid.search_reward = float(diagnostics["search_reward"])
            bid.search_score = float(diagnostics["search_score"])
            bid.search_summary = (
                f"{', '.join(evidence) or 'paper'} | "
                f"mc={diagnostics['sample_count']} mean={diagnostics['mean_score']} "
                f"success={diagnostics['success_rate']} rollback={diagnostics['rollback_rate']}"
            )
            bid.status = BidStatus.SIMULATED
            bid.score = score_bid(bid)
            self._save_bid(bid, self.state.active_bid_round)
        self.state.simulation_summary = self.simulation.summarize(task, self.state.active_bids, rollout_plan)
        self.emit(
            "simulation.completed",
            "Bounded simulation completed.",
            task_id=task.task_id,
            summary=self.state.simulation_summary.summary,
            monte_carlo_samples=self.state.simulation_summary.monte_carlo_samples,
            frontier_gap=self.state.simulation_summary.frontier_gap,
        )
        return {"status": ActivePhase.SELECT.value}

    def node_select(self) -> dict:
        ordered = [bid for bid in sorted(self.state.active_bids, key=lambda item: item.score or -999, reverse=True) if not bid.rejection_reason]
        if not ordered:
            self.state.no_valid_contenders = True
            return {"status": ActivePhase.FINALIZE.value}
        winner = ordered[0]
        winner.status = BidStatus.WINNER
        standby = next((bid for bid in ordered[1:] if bid.strategy_family != winner.strategy_family and bid.can_be_standby), ordered[1] if len(ordered) > 1 else None)
        if standby:
            standby.status = BidStatus.STANDBY
        self.state.current_bid = winner
        self.state.standby_bid = standby
        self.state.winner_bid_id = winner.bid_id
        self.state.standby_bid_id = standby.bid_id if standby else None
        winner.selection_reason = "highest_scored_valid_contender_after_bounded_monte_carlo_search"
        self._save_bid(winner, self.state.active_bid_round)
        self.emit(
            "bid.won",
            f"Bid {winner.bid_id} won.",
            task_id=winner.task_id,
            bid_id=winner.bid_id,
            role=winner.role,
            score=winner.score,
            provider=winner.provider,
            lane=winner.lane,
            model_id=winner.model_id,
            invocation_id=winner.invocation_id,
            generation_mode=winner.generation_mode.value,
            selection_reason=winner.selection_reason,
            search_summary=winner.search_summary,
        )
        self.trace(
            "bid.won",
            "Winner selected",
            f"{winner.bid_id} selected as the winner.",
            task_id=winner.task_id,
            bid_id=winner.bid_id,
            provider=winner.provider,
            lane=winner.lane,
            status="success",
            score=winner.score,
            model_id=winner.model_id,
            invocation_id=winner.invocation_id,
            generation_mode=winner.generation_mode.value,
        )
        if standby:
            self._save_bid(standby, self.state.active_bid_round)
            self.emit(
                "standby.selected",
                f"Standby selected for {winner.task_id}.",
                task_id=winner.task_id,
                bid_id=standby.bid_id,
                role=standby.role,
                score=standby.score,
                provider=standby.provider,
                lane=standby.lane,
                model_id=standby.model_id,
                invocation_id=standby.invocation_id,
                generation_mode=standby.generation_mode.value,
            )
            self.trace(
                "standby.selected",
                "Standby selected",
                f"{standby.bid_id} is ready as an alternate.",
                task_id=winner.task_id,
                bid_id=standby.bid_id,
                provider=standby.provider,
                lane=standby.lane,
                status="info",
                score=standby.score,
                model_id=standby.model_id,
                invocation_id=standby.invocation_id,
                generation_mode=standby.generation_mode.value,
            )
        return {"status": ActivePhase.EXECUTE.value}

    def node_execute(self) -> dict:
        task = self._active_task()
        bid = self.state.current_bid
        assert bid is not None
        task.status = TaskStatus.RUNNING
        self._save_task(task)
        self.emit("task.running", f"Task {task.task_id} is running.", task_id=task.task_id)
        self.trace("task.running", "Task running", f"{task.task_id} entered execution.", task_id=task.task_id, bid_id=bid.bid_id, provider=bid.provider, lane=bid.lane, status="info")
        if task.task_type.value in {"localize", "perf_diagnosis", "validate"}:
            self.state.decision_history.append(f"{task.task_id}: evidence-only step completed")
            self.emit("tool.executed", "Evidence-only task executed.", task_id=task.task_id)
            self._refresh_worktree_state("Evidence-only phase completed without modifying repo files.")
            self.trace("diff.updated", "Worktree refreshed", self.state.worktree_state["reason"], task_id=task.task_id, bid_id=bid.bid_id, status="info", worktree_state=self.state.worktree_state)
            return {"status": ActivePhase.VALIDATE.value}
        candidate_files = load_candidate_files(self.paths.worktree_dir, bid.touched_files or task.candidate_files)
        candidates = self.strategy_backend.generate_edit_proposals(
            task=task,
            bid=bid,
            mission_objective=self.spec.objective,
            candidate_files=candidate_files,
            failure_context=self.state.failure_context.details if self.state.failure_context else None,
            on_invocation=self._record_model_invocation,
        )
        for candidate in candidates:
            self._merge_usage(candidate.invocation.token_usage, candidate.invocation.cost_usage)
            candidate.score = self._proposal_score(candidate, bid, task)
        candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
        selected_candidate = candidates[0] if candidates else None
        if not selected_candidate or not selected_candidate.proposal.files:
            return self._stall_failure(task, bid)
        for candidate in candidates[1:]:
            candidate.rejection_reason = "lower_ranked_provider_proposal"
        selected_candidate.selected = True
        selected_candidate.proposal.summary = f"{selected_candidate.proposal.summary} [{selected_candidate.provider}]"
        self.state.decision_history.append(
            f"{task.task_id}: selected {selected_candidate.provider} proposal on {selected_candidate.lane}"
        )
        self.trace(
            "proposal.selected",
            "Proposal selected",
            f"{selected_candidate.provider} proposal selected for {task.task_id}.",
            task_id=task.task_id,
            bid_id=bid.bid_id,
            provider=selected_candidate.provider,
            lane=selected_candidate.lane,
            status="success",
            model_id=selected_candidate.model_id,
            score=selected_candidate.score,
            summary=selected_candidate.proposal.summary,
        )
        proposal = selected_candidate.proposal
        if not proposal.files:
            return self._stall_failure(task, bid)
        intent = ActionIntent(action_type="edit_file", task_id=task.task_id, bid_id=bid.bid_id, file_scope=[item.path for item in proposal.files], payload={"summary": proposal.summary})
        decision = self.governance.authorize_action(task, bid, intent, self.spec)
        outcome = self.civic.authorize_and_execute(
            mission_id=self.spec.mission_id,
            task_id=task.task_id,
            action_type=intent.action_type,
            decision=decision,
            payload=intent.model_dump(mode="json"),
            executor=lambda: {"touched": self.toolset.apply_file_updates({item.path: item.content for item in proposal.files})},
        )
        self.state.last_civic_audit = outcome.audit
        self.state.summary.audit_summary[outcome.audit.audit_id] = outcome.audit.model_dump(mode="json")
        if not outcome.success:
            self.state.policy_collisions += 1
            self.state.governance.policy_state = PolicyState.BLOCKED
            self.state.governance.stop_reason = "; ".join(decision.reasons)
            self.state.failure_context = FailureContext(task_id=task.task_id, failure_type="policy_block", details="; ".join(decision.reasons), diff_summary=self.toolset.diff(), validator_deltas=[], recommended_recovery_scope="rebid", strategy_family=bid.strategy_family, attempted_file_scope=intent.file_scope, rollout_evidence=[value for value in [bid.search_summary, selected_candidate.proposal.summary] if value], civic_action_history=[outcome.audit.audit_id])
            self._save_failure(self.state.failure_context)
            return {"status": ActivePhase.RECOVER.value}
        self._refresh_worktree_state("Material edit applied in the isolated worktree.")
        step = ExecutionStep(
            step_id=f"{task.task_id}-{uuid4().hex[:8]}",
            task_id=task.task_id,
            bid_id=bid.bid_id,
            action_type="edit_file",
            description=proposal.summary,
            tool_name="edit_file",
            input_payload=intent.model_dump(mode="json"),
            output_payload=outcome.result,
            civic_audit_id=outcome.audit.audit_id,
            governance_state=outcome.audit.policy_state,
        )
        self._save_execution_step(step)
        self.emit("tool.executed", "Material edit applied.", task_id=task.task_id, touched_files=outcome.result.get("touched", []))
        self.trace("diff.updated", "Worktree updated", self.state.worktree_state["reason"], task_id=task.task_id, bid_id=bid.bid_id, provider=selected_candidate.provider, lane=selected_candidate.lane, status="success", worktree_state=self.state.worktree_state)
        return {"status": ActivePhase.VALIDATE.value}

    def node_validate(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        self.trace("validation.started", "Validation started", f"Validation started for {task.task_id}.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="info")
        if task.task_type.value in {"localize", "perf_diagnosis"}:
            report = ValidationReport(task_id=task.task_id, passed=bool(task.candidate_files), notes=[] if task.candidate_files else ["No candidate files identified during evidence gathering."], policy_conformance=True)
        elif task.task_type.value == "validate" and not self.toolset.changed_files() and self.state.validation_report and self.state.validation_report.passed:
            report = ValidationReport(
                task_id=task.task_id,
                passed=True,
                command_results=self.state.validation_report.command_results,
                baseline_command_results=self.state.validation_report.baseline_command_results,
                file_churn=self.state.validation_report.file_churn,
                changed_files=self.state.validation_report.changed_files,
                api_guard_passed=self.state.validation_report.api_guard_passed,
                benchmark_delta=self.state.validation_report.benchmark_delta,
                notes=["Reused latest accepted validator report because the worktree is unchanged."],
                policy_conformance=self.state.validation_report.policy_conformance,
                validator_deltas=[],
            )
        else:
            report = ValidationEngine(self.toolset, self.spec, self.state.repo_snapshot).validate(task)
        self.state.validation_report = report
        self._save_validation(report)
        validation_decision = self.governance.evaluate_validation(task, report, self.spec)
        self.state.governance.last_decision = validation_decision
        self.state.governance.current_risk_score = max(self.state.governance.current_risk_score, validation_decision.risk_score)
        if validation_decision.allowed:
            task.status = TaskStatus.COMPLETED
            self._save_task(task)
            changed = self.toolset.changed_files()
            if changed:
                commit_sha = self.toolset.commit(f"Arbiter mission {self.spec.mission_id}: {task.title}")
                checkpoint = AcceptedCheckpoint(
                    checkpoint_id=f"{self.spec.mission_id}-{task.task_id}-{uuid4().hex[:6]}",
                    label=task.task_id,
                    commit_sha=commit_sha,
                    summary=task.title,
                    diff_summary=self.toolset.commit_diff_stat(commit_sha) or self.state.latest_diff_summary,
                    diff_patch=self.toolset.commit_diff(commit_sha),
                    affected_files=changed,
                    validator_results=[result.command[-1] if result.command else "validator" for result in report.command_results],
                    strategy_family=self.state.current_bid.strategy_family if self.state.current_bid else None,
                    civic_audit_ids=self.state.summary.civic_audit_ids[-3:],
                    rollback_pointer=self.state.accepted_checkpoint.checkpoint_id if self.state.accepted_checkpoint else None,
                )
                self.state.accepted_checkpoint = checkpoint
                self._save_checkpoint(checkpoint)
                self.emit("checkpoint.accepted", "Accepted checkpoint committed.", task_id=task.task_id, commit_sha=commit_sha)
                self._refresh_worktree_state("Changes were committed to the Arbiter-managed branch; the worktree is now clean.")
                self.trace("checkpoint.accepted", "Checkpoint accepted", f"Accepted checkpoint committed for {task.task_id}.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="success", commit_sha=commit_sha, checkpoint_id=checkpoint.checkpoint_id)
            self.state.decision_history.append(f"{task.task_id}: completed")
            self.emit("task.completed", f"Strategy move {task.task_id} completed.", task_id=task.task_id)
            self.emit("validation.passed", "Validation passed. Strategy market will reopen.", task_id=task.task_id, refresh_view=True)
            self.trace("validation.completed", "Validation passed", f"Validation passed for {task.task_id}. Returning to strategy market.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="success", notes=report.notes, changed_files=report.changed_files, refresh_view=True)
            return {"status": ActivePhase.STRATEGIZE.value}
        self.state.failure_context = FailureContext(
            task_id=task.task_id,
            failure_type="validation_failure",
            details="; ".join(validation_decision.reasons or report.details) or "Validation failed.",
            diff_summary=self.toolset.diff(),
            validator_deltas=report.validator_deltas,
            recommended_recovery_scope="standby_or_rebid",
            strategy_family=self.state.current_bid.strategy_family if self.state.current_bid else None,
            attempted_file_scope=self.state.current_bid.touched_files if self.state.current_bid else [],
            rollout_evidence=[value for value in [self.state.current_bid.search_summary if self.state.current_bid else None, self.state.current_bid.selection_reason if self.state.current_bid else None] if value],
            civic_action_history=self.state.summary.civic_audit_ids[-3:],
        )
        self.state.latest_diff_summary = self.state.failure_context.diff_summary
        self._save_failure(self.state.failure_context)
        self.emit("task.failed", "Task failed validation.", task_id=task.task_id)
        self.emit("validation.failed", "Validation failed.", task_id=task.task_id, details=self.state.failure_context.details, refresh_view=True)
        self.trace("validation.completed", "Validation failed", f"Validation failed for {task.task_id}.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="danger", details=self.state.failure_context.details, validator_deltas=report.validator_deltas, refresh_view=True)
        return {"status": ActivePhase.RECOVER.value}

    def node_recover(self) -> dict:
        task = self._active_task()
        assert self.state.failure_context is not None
        self.state.recovery_round += 1
        self.trace("recovery.started", "Recovery started", f"Recovery started for {task.task_id}.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="warning", failure_type=self.state.failure_context.failure_type)
        if self.state.accepted_checkpoint:
            intent = ActionIntent(action_type="revert_to_checkpoint", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, file_scope=[], payload={"checkpoint": self.state.accepted_checkpoint.commit_sha})
            outcome = self.civic.authorize_and_execute(
                mission_id=self.spec.mission_id,
                task_id=task.task_id,
                action_type=intent.action_type,
                decision=PolicyDecision(allowed=True),
                payload=intent.model_dump(mode="json"),
                executor=lambda: {"checkpoint": self.state.accepted_checkpoint.commit_sha, "reverted": self._revert()},
            )
            self.state.last_civic_audit = outcome.audit
            reverted = bool(outcome.result.get("reverted"))
            self.state.failure_context.rollback_result = "rollback_succeeded" if reverted else "rollback_failed"
            self.state.failure_context.created_at = utc_now()
            self._save_failure(self.state.failure_context)
            self.emit("checkpoint.reverted", "Worktree reverted to accepted checkpoint.", commit_sha=self.state.accepted_checkpoint.commit_sha)
            self._refresh_worktree_state("Worktree reverted to the latest accepted checkpoint.")
            self.repo_checkpoints.save(
                self.state.accepted_checkpoint,
                accepted=False,
                checkpoint_kind="rollback",
                label=f"{task.task_id}-rollback",
                worktree_state=self.state.worktree_state,
            )
            self.trace("diff.updated", "Worktree reverted", self.state.worktree_state["reason"], task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="warning", worktree_state=self.state.worktree_state)
        plan = self.recovery.plan_recovery(self.state.current_bid, self.state.standby_bid, self.state.failure_context)
        if plan.action == "stop":
            self.state.outcome = MissionOutcome.FAILED_SAFE_STOP
            self.state.governance.stop_reason = plan.reason
            self.trace(
                "recovery.completed",
                "Recovery stopped",
                plan.reason,
                task_id=task.task_id,
                bid_id=self.state.current_bid.bid_id if self.state.current_bid else None,
                status="danger",
                evidence=plan.evidence,
                refresh_view=True,
            )
            return {"status": ActivePhase.FINALIZE.value}
        if plan.action == "promote_standby":
            self.state.current_bid = self.state.standby_bid
            self.state.current_bid.status = BidStatus.WINNER
            self.state.winner_bid_id = self.state.current_bid.bid_id
            self.state.standby_bid = None
            self.state.standby_bid_id = None
            self._save_bid(self.state.current_bid, self.state.active_bid_round)
            self.emit("standby.promoted", "Standby promoted after failure.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id, reason=plan.reason, evidence=plan.evidence, refresh_view=True)
            self.trace("recovery.completed", "Standby promoted", f"Standby {self.state.current_bid.bid_id} promoted after failure.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id, provider=self.state.current_bid.provider, lane=self.state.current_bid.lane, status="success", reason=plan.reason, evidence=plan.evidence, refresh_view=True)
            return {"status": ActivePhase.EXECUTE.value}
        if self.state.current_bid:
            family = plan.failed_family
            if family:
                self.failed_families.setdefault(task.task_id, set()).add(family)
            self.state.summary.failed_attempt_history.append(self.state.current_bid.strategy_summary)
            self.state.current_bid.status = BidStatus.FAILED
            self._save_bid(self.state.current_bid, self.state.active_bid_round)
        if self.state.recovery_round > self.spec.stop_policy.max_recovery_rounds:
            self.state.outcome = MissionOutcome.FAILED_EXECUTION
            self.state.governance.stop_reason = "recovery_budget_exceeded"
            return {"status": ActivePhase.FINALIZE.value}
        task.status = TaskStatus.READY
        self._save_task(task)
        self.emit("recovery.round_opened", "Strategy market reopening with failure evidence.", task_id=task.task_id, round=self.state.recovery_round, failed_families=sorted(self.failed_families.get(task.task_id, set())), reason=plan.reason, evidence=plan.evidence)
        self.trace("recovery.completed", "Strategy market reopened", f"Strategy market reopening for recovery after {task.task_id}.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id if self.state.current_bid else None, status="warning", failed_families=sorted(self.failed_families.get(task.task_id, set())), reason=plan.reason, evidence=plan.evidence)
        return {"status": ActivePhase.STRATEGIZE.value}

    def node_finalize(self) -> dict:
        stop = self.governance.evaluate_stop(self.state)
        if stop.should_stop:
            self.state.governance.stop_reason = stop.reason
            self.state.outcome = stop.outcome or self.state.outcome
        required_tasks = [task for task in self.state.tasks if task.requirement_level == TaskRequirementLevel.REQUIRED]
        optional_tasks = [task for task in self.state.tasks if task.requirement_level == TaskRequirementLevel.OPTIONAL]
        if self.state.outcome is None:
            if required_tasks and all(task.status == TaskStatus.COMPLETED for task in required_tasks):
                self.state.outcome = MissionOutcome.PARTIAL_SUCCESS if any(task.status in {TaskStatus.FAILED, TaskStatus.SKIPPED} for task in optional_tasks) else MissionOutcome.SUCCESS
            elif self.state.governance.policy_state == PolicyState.BLOCKED:
                self.state.outcome = MissionOutcome.POLICY_BLOCKED
            elif self.state.no_valid_contenders:
                self.state.outcome = MissionOutcome.FAILED_EXECUTION
            else:
                self.state.outcome = MissionOutcome.FAILED_SAFE_STOP
        self.state.control = MissionControlState(run_state=RunState.FINALIZED, reason=self.state.governance.stop_reason)
        self.emit("mission.finalized", "Mission finalized.", outcome=self.state.outcome.value, stop_reason=self.state.governance.stop_reason, refresh_view=True)
        return {"status": "done"}

    def _stall_failure(self, task, bid) -> dict:
        self.state.failure_context = FailureContext(
            task_id=task.task_id,
            failure_type="execution_stall",
            details="No file updates were proposed.",
            diff_summary="No diff generated.",
            validator_deltas=[],
            recommended_recovery_scope="rebid",
            strategy_family=bid.strategy_family,
            attempted_file_scope=bid.touched_files,
            rollout_evidence=[value for value in [bid.search_summary, bid.selection_reason] if value],
        )
        self._save_failure(self.state.failure_context)
        self.emit("task.failed", "Task failed to generate an edit.", task_id=task.task_id)
        self.trace("proposal.selected", "No usable proposal", f"No valid provider proposal was available for {task.task_id}.", task_id=task.task_id, bid_id=bid.bid_id, status="danger")
        return {"status": ActivePhase.RECOVER.value}

    def _revert(self) -> bool:
        if not self.state.accepted_checkpoint:
            return False
        self.toolset.revert_to_checkpoint(self.state.accepted_checkpoint.commit_sha)
        self._refresh_worktree_state("Worktree reset to the latest accepted checkpoint.")
        return True

    def _active_task(self):
        return self._task(self.state.active_task_id)

    def _task(self, task_id: str):
        for task in self.state.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def _merge_usage(self, token_usage: dict | None, cost_usage: dict | None) -> None:
        for key, value in (token_usage or {}).items():
            self.state.token_usage[key] = self.state.token_usage.get(key, 0) + int(value)
        for key, value in (cost_usage or {}).items():
            self.state.cost_usage[key] = self.state.cost_usage.get(key, 0.0) + float(value)


def build_mission_spec(repo: str, objective: str, constraints: list[str] | None = None, preferences: list[str] | None = None, max_runtime: int | None = None, benchmark_requirement: str | None = None, protected_paths: list[str] | None = None, public_api_surface: list[str] | None = None, mission_id: str | None = None) -> MissionSpec:
    config = load_runtime_config()
    resolved_repo = resolve_repo_path(repo)
    return MissionSpec(
        mission_id=mission_id or generate_mission_id(),
        repo_path=str(resolved_repo),
        objective=objective,
        constraints=constraints or [],
        preferences=preferences or [],
        max_runtime_minutes=max_runtime or config.max_runtime_minutes,
        benchmark_requirement=benchmark_requirement,
        protected_paths=protected_paths or [],
        public_api_surface=public_api_surface or [],
        bidding_policy={
            "require_provider_backed_bids": config.require_real_provider_bidding and config.replay_mode == "off",
            "allow_degraded_fallback": config.allow_degraded_bid_fallback,
        },
    )


def _adjust_bidding_policy_for_backend(spec: MissionSpec, strategy_backend) -> MissionSpec:
    if strategy_backend and hasattr(strategy_backend, "market_generation_mode"):
        if strategy_backend.market_generation_mode() != BidGenerationMode.PROVIDER_MODEL:
            spec.bidding_policy.require_provider_backed_bids = False
    return spec


def start_mission(repo: str, objective: str, constraints: list[str] | None = None, preferences: list[str] | None = None, max_runtime: int | None = None, benchmark_requirement: str | None = None, protected_paths: list[str] | None = None, public_api_surface: list[str] | None = None, strategy_backend=None, mission_id: str | None = None) -> ArbiterState:
    spec = _adjust_bidding_policy_for_backend(
        build_mission_spec(
            repo=repo,
            objective=objective,
            constraints=constraints,
            preferences=preferences,
            max_runtime=max_runtime,
            benchmark_requirement=benchmark_requirement,
            protected_paths=protected_paths,
            public_api_surface=public_api_surface,
            mission_id=mission_id,
        ),
        strategy_backend,
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    Path(paths.metadata_path).write_text(json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8")
    runtime = MissionRuntime(spec, paths, strategy_backend=strategy_backend)
    try:
        return runtime.run()
    finally:
        runtime.store.close()


def resume_mission(mission_id: str, repo: str, strategy_backend=None) -> ArbiterState:
    paths = build_mission_paths(repo, mission_id)
    migrate_legacy_mission(paths, mission_id)
    store = MissionStore(paths.db_path)
    state = store.rebuild_state(mission_id)
    spec = state.mission
    store.close()
    runtime = MissionRuntime(spec, paths, strategy_backend=strategy_backend)
    runtime.state = state
    runtime._load_failed_families()
    runtime._restore_runtime_context()
    runtime._refresh_worktree_state("Mission resumed in the isolated worktree.")
    try:
        runtime.emit("mission.resumed", "Mission resumed.", mission_id=mission_id, refresh_view=True)
        return runtime.run()
    finally:
        runtime.store.close()


def mission_status(mission_id: str, repo: str) -> dict:
    paths = build_mission_paths(repo, mission_id)
    migrate_legacy_mission(paths, mission_id)
    store = MissionStore(paths.db_path)
    try:
        view = store.get_mission_view(mission_id)
        return {
            "mission_id": mission_id,
            "status": view["status"],
            "outcome": view["outcome"],
            "branch_name": view["branch_name"],
            "decision_history": view["decision_history"],
            "failed_attempt_history": view["failed_attempt_history"],
            "event_count": view["latest_event_id"],
            "run_state": view["run_state"],
            "active_phase": view["active_phase"],
            "stop_reason": view["stop_state"]["stop_reason"],
        }
    finally:
        store.close()
