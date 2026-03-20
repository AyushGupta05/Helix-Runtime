from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from arbiter.agents.backend import BedrockModelRouter, DefaultStrategyBackend, load_candidate_files
from arbiter.civic.runtime import CivicRuntime
from arbiter.core.contracts import (
    AcceptedCheckpoint,
    ActionIntent,
    ActivePhase,
    ArbiterState,
    BidStatus,
    ExecutionStep,
    FailureContext,
    MissionControlState,
    MissionEvent,
    MissionOutcome,
    MissionSpec,
    PolicyDecision,
    PolicyState,
    RunState,
    TaskRequirementLevel,
    TaskStatus,
    ValidationReport,
    utc_now,
)
from arbiter.market.clustering import cluster_and_select
from arbiter.market.scoring import score_bid
from arbiter.mission.decomposer import GoalDecomposer
from arbiter.mission.governance import GovernanceEngine
from arbiter.mission.recovery import RecoveryEngine
from arbiter.mission.state import initialize_state
from arbiter.repo.collector import RepoStateCollector
from arbiter.repo.worktree import WorktreeManager
from arbiter.runtime.config import load_runtime_config
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
        self.router = BedrockModelRouter(self.config, self.replay)
        self.strategy_backend = strategy_backend or DefaultStrategyBackend(self.router)
        self.collector = RepoStateCollector(spec.repo_path)
        self.decomposer = GoalDecomposer()
        self.governance = GovernanceEngine()
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
        self.state.summary.branch_name = self.branch_name
        self.failed_families: dict[str, set[str]] = {}

    def emit(self, event_type: str, message: str, refresh_view: bool = False, **payload) -> None:
        event = MissionEvent(event_type=event_type, mission_id=self.spec.mission_id, message=message, payload=payload)
        self.persistence.append_event(event, refresh_view=refresh_view)

    def _sync_state(self, status: str) -> None:
        self.state.summary.branch_name = self.branch_name
        if self.state.accepted_checkpoint:
            self.state.summary.head_commit = self.state.accepted_checkpoint.commit_sha
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
            latest_validation_task_id=self.state.validation_report.task_id if self.state.validation_report else None,
            latest_failure_task_id=self.state.failure_context.task_id if self.state.failure_context else None,
            accepted_checkpoint_id=self.state.accepted_checkpoint.checkpoint_id if self.state.accepted_checkpoint else None,
        )
        self.store.upsert_control_state(
            mission_id=self.spec.mission_id,
            run_state=self.state.control.run_state.value,
            requested_action=self.state.control.requested_action,
            reason=self.state.control.reason,
            updated_at=self.state.control.updated_at.isoformat(),
        )
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
        if not self.store.fetch_all("events", self.spec.mission_id):
            self.emit("mission.started", "Mission runtime created.", repo_path=self.spec.repo_path, branch_name=self.branch_name)
        return self.state.active_phase.value if self.state.active_phase != ActivePhase.IDLE else ActivePhase.COLLECT.value

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

    def run(self) -> ArbiterState:
        started = time.perf_counter()
        status = self._prepare_run()
        try:
            while status != "done":
                self._cooperate()
                self.state.active_phase = ActivePhase(status)
                self._sync_state("running")
                status = getattr(self, f"node_{status}")()["status"]
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
        self.emit("repo.scan.completed", "Repository scan completed.", runtime=self.state.repo_snapshot.capabilities.runtime, risky_paths=self.state.repo_snapshot.capabilities.risky_paths)
        return {"status": ActivePhase.DECOMPOSE.value}

    def node_decompose(self) -> dict:
        assert self.state.repo_snapshot is not None
        self.state.tasks = self.decomposer.decompose(self.spec.objective, self.state.repo_snapshot)
        for task in self.state.tasks:
            self._save_task(task)
            self.emit("task.created", f"Task {task.task_id} created.", task_id=task.task_id, task_type=task.task_type.value)
        return {"status": ActivePhase.SELECT_TASK.value}

    def node_select_task(self) -> dict:
        for task in self.state.tasks:
            if task.status == TaskStatus.PENDING and all(self._task(dep).status == TaskStatus.COMPLETED for dep in task.dependencies):
                task.status = TaskStatus.READY
                self._save_task(task)
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
        failed = self.failed_families.setdefault(task.task_id, set())
        bids = self.simulation.generate(task, self.state.repo_snapshot)
        valid: list = []
        for bid in bids:
            decision = self.governance.evaluate_bid(task, bid, self.spec, failed)
            bid.policy_feasibility = decision
            if not decision.allowed:
                bid.rejection_reason = "; ".join(decision.reasons)
                bid.status = BidStatus.REJECTED
                self._save_bid(bid, self.state.active_bid_round)
                self.emit("bid.rejected", f"Bid {bid.bid_id} rejected.", bid_id=bid.bid_id, task_id=task.task_id, reason=bid.rejection_reason)
                continue
            bid.score = score_bid(bid)
            valid.append(bid)
        self.state.active_bids = cluster_and_select(valid, per_family=2)
        for bid in self.state.active_bids:
            self._save_bid(bid, self.state.active_bid_round)
            self.emit("bid.submitted", f"Bid {bid.bid_id} submitted.", bid_id=bid.bid_id, task_id=task.task_id, role=bid.role, score=bid.score, strategy_family=bid.strategy_family)
        if not self.state.active_bids:
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
            reward = max(0.0, 0.45 + bid.confidence * 0.2 - bid.risk * 0.25 - bid.cost * 0.1)
            evidence: list[str] = []
            if bid.bid_id in paper_ids:
                reward += 0.05
                evidence.append("paper")
            if bid.bid_id in partial_ids:
                files = load_candidate_files(self.paths.worktree_dir, bid.touched_files or task.candidate_files)
                reward += min(0.15, len(files) * 0.05)
                if task.validator_requirements and set(task.validator_requirements).issubset(set(bid.validator_plan)):
                    reward += 0.05
                evidence.append("partial")
            if bid.bid_id in sandbox_ids:
                scratch = Path(self.paths.scratch_worktrees_dir) / bid.bid_id
                self.worktree.ensure_detached(str(scratch), ref=base_ref)
                try:
                    scratch_tools = LocalToolset(str(scratch))
                    files = load_candidate_files(str(scratch), bid.touched_files or task.candidate_files)
                    proposal, invocation = self.strategy_backend.generate_edit_proposal(task=task, bid=bid, mission_objective=self.spec.objective, candidate_files=files, failure_context=self.state.failure_context.details if self.state.failure_context else None, preview=True)
                    self._merge_usage(invocation.token_usage, invocation.cost_usage)
                    if proposal.files:
                        scratch_tools.apply_file_updates({item.path: item.content for item in proposal.files})
                        report = ValidationEngine(scratch_tools, self.spec, self.state.repo_snapshot).validate(task)
                        reward += 0.20 if report.passed else -0.12
                        evidence.append("sandbox:pass" if report.passed else "sandbox:fail")
                    else:
                        reward -= 0.10
                        evidence.append("sandbox:no_patch")
                finally:
                    self.worktree.remove_path(str(scratch))
            bid.search_reward = round(max(0.0, min(1.0, reward)), 4)
            bid.search_score = bid.search_reward
            bid.search_summary = ", ".join(evidence) or "paper"
            bid.status = BidStatus.SIMULATED
            bid.score = score_bid(bid)
            self._save_bid(bid, self.state.active_bid_round)
        self.state.simulation_summary = self.simulation.summarize(task, self.state.active_bids, rollout_plan)
        self.emit("simulation.completed", "Bounded simulation completed.", task_id=task.task_id, summary=self.state.simulation_summary.summary)
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
        self._save_bid(winner, self.state.active_bid_round)
        self.emit("bid.won", f"Bid {winner.bid_id} won.", task_id=winner.task_id, bid_id=winner.bid_id, role=winner.role, score=winner.score)
        if standby:
            self._save_bid(standby, self.state.active_bid_round)
            self.emit("standby.selected", f"Standby selected for {winner.task_id}.", task_id=winner.task_id, bid_id=standby.bid_id, role=standby.role, score=standby.score)
        return {"status": ActivePhase.EXECUTE.value}

    def node_execute(self) -> dict:
        task = self._active_task()
        bid = self.state.current_bid
        assert bid is not None
        task.status = TaskStatus.RUNNING
        self._save_task(task)
        if task.task_type.value in {"localize", "perf_diagnosis", "validate"}:
            self.state.decision_history.append(f"{task.task_id}: evidence-only step completed")
            self.emit("tool.executed", "Evidence-only task executed.", task_id=task.task_id)
            return {"status": ActivePhase.VALIDATE.value}
        candidate_files = load_candidate_files(self.paths.worktree_dir, bid.touched_files or task.candidate_files)
        proposal, invocation = self.strategy_backend.generate_edit_proposal(task=task, bid=bid, mission_objective=self.spec.objective, candidate_files=candidate_files, failure_context=self.state.failure_context.details if self.state.failure_context else None)
        self._merge_usage(invocation.token_usage, invocation.cost_usage)
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
            self.state.failure_context = FailureContext(task_id=task.task_id, failure_type="policy_block", details="; ".join(decision.reasons), diff_summary=self.toolset.diff(), validator_deltas=[], recommended_recovery_scope="rebid", strategy_family=bid.strategy_family, attempted_file_scope=intent.file_scope, civic_action_history=[outcome.audit.audit_id])
            self._save_failure(self.state.failure_context)
            return {"status": ActivePhase.RECOVER.value}
        self.state.latest_diff_summary = self.toolset.diff()
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
        return {"status": ActivePhase.VALIDATE.value}

    def node_validate(self) -> dict:
        task = self._active_task()
        assert self.state.repo_snapshot is not None
        if task.task_type.value in {"localize", "perf_diagnosis"}:
            report = ValidationReport(task_id=task.task_id, passed=bool(task.candidate_files), notes=[] if task.candidate_files else ["No candidate files identified during evidence gathering."], policy_conformance=True)
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
                    diff_summary=self.state.latest_diff_summary or self.toolset.diff(),
                    affected_files=changed,
                    validator_results=[result.command[-1] if result.command else "validator" for result in report.command_results],
                    strategy_family=self.state.current_bid.strategy_family if self.state.current_bid else None,
                    civic_audit_ids=self.state.summary.civic_audit_ids[-3:],
                    rollback_pointer=self.state.accepted_checkpoint.checkpoint_id if self.state.accepted_checkpoint else None,
                )
                self.state.accepted_checkpoint = checkpoint
                self._save_checkpoint(checkpoint)
                self.emit("checkpoint.accepted", "Accepted checkpoint committed.", task_id=task.task_id, commit_sha=commit_sha)
            self.state.decision_history.append(f"{task.task_id}: completed")
            self.emit("task.completed", f"Task {task.task_id} completed.", task_id=task.task_id)
            self.emit("validation.passed", "Validation passed.", task_id=task.task_id, refresh_view=True)
            return {"status": ActivePhase.SELECT_TASK.value}
        self.state.failure_context = FailureContext(
            task_id=task.task_id,
            failure_type="validation_failure",
            details="; ".join(validation_decision.reasons or report.details) or "Validation failed.",
            diff_summary=self.toolset.diff(),
            validator_deltas=report.validator_deltas,
            recommended_recovery_scope="standby_or_rebid",
            strategy_family=self.state.current_bid.strategy_family if self.state.current_bid else None,
            attempted_file_scope=self.state.current_bid.touched_files if self.state.current_bid else [],
            civic_action_history=self.state.summary.civic_audit_ids[-3:],
        )
        self.state.latest_diff_summary = self.state.failure_context.diff_summary
        self._save_failure(self.state.failure_context)
        self.emit("task.failed", "Task failed validation.", task_id=task.task_id)
        self.emit("validation.failed", "Validation failed.", task_id=task.task_id, details=self.state.failure_context.details, refresh_view=True)
        return {"status": ActivePhase.RECOVER.value}

    def node_recover(self) -> dict:
        task = self._active_task()
        assert self.state.failure_context is not None
        self.state.recovery_round += 1
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
            self.emit("checkpoint.reverted", "Worktree reverted to accepted checkpoint.", commit_sha=self.state.accepted_checkpoint.commit_sha)
        if self.recovery.should_promote_standby(self.state.standby_bid, self.state.failure_context):
            self.state.current_bid = self.state.standby_bid
            self.state.current_bid.status = BidStatus.WINNER
            self.state.winner_bid_id = self.state.current_bid.bid_id
            self.state.standby_bid = None
            self.state.standby_bid_id = None
            self._save_bid(self.state.current_bid, self.state.active_bid_round)
            self.emit("standby.promoted", "Standby promoted after failure.", task_id=task.task_id, bid_id=self.state.current_bid.bid_id, refresh_view=True)
            return {"status": ActivePhase.EXECUTE.value}
        if self.state.current_bid:
            family, _ = self.recovery.family_penalty(self.state.failure_context)
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
        self.emit("recovery.round_opened", "Rebidding with prior evidence.", task_id=task.task_id, round=self.state.recovery_round, failed_families=sorted(self.failed_families.get(task.task_id, set())))
        return {"status": ActivePhase.MARKET.value}

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
        )
        self._save_failure(self.state.failure_context)
        self.emit("task.failed", "Task failed to generate an edit.", task_id=task.task_id)
        return {"status": ActivePhase.RECOVER.value}

    def _revert(self) -> bool:
        if not self.state.accepted_checkpoint:
            return False
        self.toolset.revert_to_checkpoint(self.state.accepted_checkpoint.commit_sha)
        self.state.latest_diff_summary = self.toolset.diff()
        return True

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
    )


def start_mission(repo: str, objective: str, constraints: list[str] | None = None, preferences: list[str] | None = None, max_runtime: int | None = None, benchmark_requirement: str | None = None, protected_paths: list[str] | None = None, public_api_surface: list[str] | None = None, strategy_backend=None, mission_id: str | None = None) -> ArbiterState:
    spec = build_mission_spec(repo=repo, objective=objective, constraints=constraints, preferences=preferences, max_runtime=max_runtime, benchmark_requirement=benchmark_requirement, protected_paths=protected_paths, public_api_surface=public_api_surface, mission_id=mission_id)
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
