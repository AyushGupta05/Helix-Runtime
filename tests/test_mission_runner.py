from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from arbiter.agents.backend import EditOperation, EditProposal, FileUpdate, ModelInvocationResult, ProposalCandidate, ScriptedStrategyBackend
from arbiter.core.contracts import (
    ActivePhase,
    Bid,
    BidGenerationMode,
    CapabilitySet,
    CivicAuditRecord,
    CivicCapability,
    CivicConnectionStatus,
    GovernedActionRecord,
    GovernedBidEnvelope,
    MissionOutcome,
    MissionSummary,
    PolicyState,
    RepoSnapshot,
    RunState,
    RolloutLevel,
    SkillHealth,
    SuccessCriteria,
    TaskNode,
    TaskRequirementLevel,
    TaskType,
    utc_now,
)
from arbiter.mission.runner import MissionRuntime, build_mission_spec, mission_status, start_mission
from arbiter.runtime.paths import build_mission_paths
from arbiter.repo.worktree import WorktreeSetupError


class FakeGovernedCivic:
    def __init__(self, *, available_skills: list[str] | None = None, connected: bool = True) -> None:
        self.available_skills = available_skills or []
        self.connected = connected
        self.actions: list[str] = []

    def refresh_capability_state(self, repo_snapshot=None, force: bool = False):
        del force
        return {
            "connection": CivicConnectionStatus(
                configured=True,
                connected=self.connected,
                available=self.connected,
                required=False,
                status="connected" if self.connected else "unavailable",
                base_url="https://civic.example",
                toolkit_id="toolkit-demo",
                last_checked_at=utc_now(),
            ),
            "capabilities": [
                CivicCapability(
                    capability_id="github_read",
                    display_name="GitHub Read Context",
                    tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"],
                )
            ] if self.connected else [],
            "available_skills": list(self.available_skills) if repo_snapshot is not None else [],
            "skill_health": {
                "github_context": SkillHealth(
                    skill_id="github_context",
                    status="available" if "github_context" in self.available_skills else "inactive",
                    available="github_context" in self.available_skills,
                    required_tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"],
                    available_tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"] if self.connected else [],
                    last_checked_at=utc_now(),
                )
            },
        }

    def execute_governed_action(self, *, mission_id, task_id, bid_id, action_type, payload, skill_id=None, envelope=None, executor=None):
        del mission_id, bid_id, envelope, executor
        self.actions.append(action_type)
        record = GovernedActionRecord(
            action_id=f"{action_type}-record",
            mission_id="mission",
            task_id=task_id,
            bid_id=None,
            skill_id=skill_id,
            action_type=action_type,
            tool_name=action_type,
            status="executed",
            allowed=True,
            policy_state=PolicyState.CLEAR,
            input_payload=payload,
            output_payload={"status": "success"},
        )
        if action_type == "fetch_ci_status":
            record.output_payload = {"summary": "ci-failing", "failing_checks": ["tests"]}
        elif action_type == "open_pr_metadata":
            record.output_payload = {"number": payload["pr_number"], "title": "Fix regression"}
        elif action_type == "open_issue_metadata":
            record.output_payload = {"number": payload["issue_number"], "title": "Broken flow"}
        return type("GovernedActionResult", (), {"success": True, "result": record.output_payload, "record": record})()

    def record_audit(self, record: GovernedActionRecord) -> CivicAuditRecord:
        return CivicAuditRecord(
            audit_id=f"audit-{record.action_id}",
            mission_id="mission",
            task_id=record.task_id or "collect",
            action_type=record.action_type,
            status="executed",
            policy_state=record.policy_state,
            reasons=list(record.reasoning),
            payload=record.input_payload,
        )

    def preflight_bid(
        self,
        *,
        mission_id,
        task_id,
        bid_id,
        required_skills,
        optional_skills,
        governed_action_plan,
        estimated_runtime_seconds=None,
        token_budget=None,
        repo_snapshot=None,
    ):
        del mission_id, estimated_runtime_seconds, token_budget, repo_snapshot
        blocked = bid_id.endswith("blocked")
        return GovernedBidEnvelope(
            envelope_id=f"env-{bid_id}",
            mission_id="mission",
            task_id=task_id,
            bid_id=bid_id,
            status="blocked" if blocked else "approved",
            allowed_skills=[] if blocked else [*required_skills, *optional_skills],
            allowed_actions=[] if blocked else list(governed_action_plan),
            toolkit_id="toolkit-demo",
            policy_state=PolicyState.BLOCKED if blocked else PolicyState.CLEAR,
            policy_decision="block" if blocked else "allow",
            reasoning=["Civic policy denied bid."] if blocked else ["Bid is admissible."],
            audit_id=f"audit-env-{bid_id}",
        )


def test_python_bugfix_mission_recovers_via_standby(python_bug_repo: Path) -> None:
    backend = ScriptedStrategyBackend(
        [
            EditProposal(
                summary="Apply an incorrect patch first to force recovery.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a - b + 1\n")],
            ),
            EditProposal(
                summary="Apply the correct bugfix.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
            ),
            EditProposal(
                summary="Expand regression coverage.",
                files=[
                    FileUpdate(
                        path="tests/test_calc.py",
                        content="from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
                    )
                ],
            ),
        ]
    )
    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and improve reliability",
        strategy_backend=backend,
    )
    assert state.outcome is not None
    assert state.outcome.value == "success"
    assert state.summary.branch_name

    mission_root = python_bug_repo / ".arbiter" / "missions" / state.mission.mission_id
    assert (mission_root / "events.jsonl").exists()
    events = (mission_root / "events.jsonl").read_text(encoding="utf-8")
    assert "standby.promoted" in events
    assert "checkpoint.accepted" in events
    assert "phase.changed" in events

    status = mission_status(state.mission.mission_id, str(python_bug_repo))
    assert status["outcome"] == "success"

    branches = subprocess.run(["git", "branch", "--list", state.summary.branch_name], cwd=str(python_bug_repo), capture_output=True, text=True, check=True)
    assert state.summary.branch_name in branches.stdout

    db_path = mission_root / "state.db"
    connection = sqlite3.connect(db_path)
    task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    checkpoint_count = connection.execute("SELECT COUNT(*) FROM accepted_checkpoints").fetchone()[0]
    mission_checkpoint_count = connection.execute("SELECT COUNT(*) FROM mission_state_checkpoints").fetchone()[0]
    repo_checkpoint_count = connection.execute("SELECT COUNT(*) FROM repo_state_checkpoints").fetchone()[0]
    langgraph_checkpoint_count = connection.execute("SELECT COUNT(*) FROM langgraph_checkpoints").fetchone()[0]
    view_count = connection.execute("SELECT COUNT(*) FROM mission_view_cache").fetchone()[0]
    invocation_count = connection.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0]
    trace_count = connection.execute("SELECT COUNT(*) FROM trace_entries").fetchone()[0]
    latest_failure = connection.execute(
        "SELECT payload_json FROM failure_contexts ORDER BY timestamp DESC, id DESC LIMIT 1"
    ).fetchone()[0]
    assert task_count >= 1
    assert checkpoint_count >= 1
    assert mission_checkpoint_count >= 1
    assert repo_checkpoint_count >= 1
    assert langgraph_checkpoint_count >= 1
    assert view_count == 1
    assert invocation_count >= 1
    assert trace_count >= 1
    latest_checkpoint = connection.execute("SELECT payload_json FROM accepted_checkpoints ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    connection.close()
    assert "diff_patch" in json.loads(latest_checkpoint)
    assert json.loads(latest_failure)["rollback_result"] == "rollback_succeeded"


def test_python_bugfix_mission_accepts_operation_only_execution_proposals(python_bug_repo: Path) -> None:
    backend = ScriptedStrategyBackend(
        [
            EditProposal(
                summary="Patch the calculator bug with a compact replacement.",
                operations=[
                    EditOperation(
                        type="replace",
                        path="calc.py",
                        target="return a - b",
                        content="return a + b",
                    )
                ],
            ),
        ]
    )

    state = start_mission(
        repo=str(python_bug_repo),
        objective="Fix failing tests and tighten maintainability",
        strategy_backend=backend,
    )

    assert state.outcome is not None
    assert state.outcome.value == "success"
    calc_branch = subprocess.run(
        ["git", "show", f"{state.summary.branch_name}:calc.py"],
        cwd=str(python_bug_repo),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "return a + b" in calc_branch.stdout


def test_execute_recovers_when_edit_operation_target_is_missing(python_bug_repo: Path) -> None:
    backend = ScriptedStrategyBackend(
        [
            EditProposal(
                summary="Attempt a stale compact patch first.",
                operations=[
                    EditOperation(
                        type="replace",
                        path="calc.py",
                        target="return does not exist",
                        content="return a + b",
                    )
                ],
            ),
        ]
    )
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests with the safest bounded change",
        mission_id="missing-edit-target",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    try:
        runtime._prepare_run()
        task = TaskNode(
            task_id="task-1",
            title="Apply calculator fix",
            task_type=TaskType.BUGFIX,
            requirement_level=TaskRequirementLevel.REQUIRED,
            success_criteria=SuccessCriteria(description="Tests pass"),
            allowed_tools=["read_file", "search_code", "edit_file", "run_tests", "revert_to_checkpoint"],
            candidate_files=["calc.py"],
        )
        bid = Bid(
            bid_id="bid-1",
            task_id="task-1",
            role="safe",
            provider="scripted",
            lane="scripted",
            model_id="scripted",
            variant_id="safe-base",
            strategy_family="checkpoint-first",
            strategy_summary="Attempt a targeted calculator fix.",
            exact_action="Edit calc.py with a compact replacement.",
            expected_benefit=0.7,
            utility=0.7,
            confidence=0.8,
            risk=0.2,
            cost=0.01,
            estimated_runtime_seconds=15,
            touched_files=["calc.py"],
            rollback_plan="revert",
            rollout_level=RolloutLevel.PAPER,
            generation_mode=BidGenerationMode.MOCK,
        )
        runtime.state.tasks = [task]
        runtime.state.active_task_id = task.task_id
        runtime.state.current_bid = bid
        runtime.state.active_bids = [bid]
        runtime.state.winner_bid_id = bid.bid_id

        result = runtime.node_execute()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.RECOVER.value}
    assert runtime.state.failure_context is not None
    assert runtime.state.failure_context.failure_type == "execution_failure"
    assert runtime.toolset.read_file("calc.py") == "def add(a, b):\n    return a - b\n"


def test_start_mission_requires_initial_commit(tmp_path: Path) -> None:
    repo = tmp_path / "no_commit_repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True, text=True)

    with pytest.raises(WorktreeSetupError, match="at least one commit"):
        start_mission(
            repo=str(repo),
            objective="Fix failing tests",
            strategy_backend=ScriptedStrategyBackend([]),
        )


def test_execute_prefers_winning_provider_before_widening(python_bug_repo: Path) -> None:
    class RecordingProposalBackend:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.router = type(
                "Router",
                (),
                {
                    "config": type(
                        "Config",
                        (),
                        {"enabled_providers": ["anthropic", "openai"]},
                    )()
                },
            )()

        def generate_edit_proposals(
            self,
            task,
            bid,
            mission_objective,
            candidate_files,
            failure_context=None,
            preview=False,
            providers=None,
            on_invocation=None,
        ):
            del task, bid, mission_objective, candidate_files, failure_context, preview, on_invocation
            requested = list(providers or [])
            self.calls.append(requested)
            provider = requested[0] if requested else "anthropic"
            proposal = EditProposal(
                summary=f"{provider} proposal",
                files=[] if provider == "anthropic" else [FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
            )
            invocation = ModelInvocationResult(
                content=proposal.model_dump_json(),
                provider=provider,
                model_id=f"{provider}-proposal",
                lane=f"proposal_gen.{provider}",
                generation_mode=BidGenerationMode.PROVIDER_MODEL,
                prompt_preview="",
                response_preview=proposal.summary,
                started_at=utc_now().isoformat(),
                completed_at=utc_now().isoformat(),
            )
            return [
                ProposalCandidate(
                    candidate_id=f"{provider}-candidate",
                    task_id="task-1",
                    bid_id="bid-1",
                    provider=provider,
                    lane=f"proposal_gen.{provider}",
                    model_id=f"{provider}-proposal",
                    proposal=proposal,
                    invocation=invocation,
                )
            ]

    backend = RecordingProposalBackend()
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests",
        mission_id="provider-first-execute",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    try:
        runtime.store.upsert_mission(
            mission_id=spec.mission_id,
            status="running",
            repo_path=spec.repo_path,
            objective=spec.objective,
            branch_name=runtime.branch_name,
            outcome=None,
            spec=spec,
            summary=MissionSummary(mission_id=spec.mission_id, repo_path=spec.repo_path, objective=spec.objective),
            created_at=spec.created_at.isoformat(),
        )
        runtime.store.upsert_control_state(
            spec.mission_id,
            RunState.RUNNING.value,
            None,
            None,
            utc_now().isoformat(),
        )

        task = TaskNode(
            task_id="task-1",
            title="Apply calculator fix",
            task_type=TaskType.BUGFIX,
            requirement_level=TaskRequirementLevel.REQUIRED,
            success_criteria=SuccessCriteria(description="Tests pass"),
            candidate_files=["calc.py"],
        )
        bid = Bid(
            bid_id="bid-1",
            task_id="task-1",
            role="quality",
            provider="anthropic",
            lane="bid_fast.anthropic",
            model_id="anthropic-bid-fast",
            variant_id="quality-base",
            strategy_family="Quality",
            strategy_summary="Apply a careful calculator fix.",
            exact_action="Update calc.py",
            expected_benefit=0.8,
            utility=0.8,
            confidence=0.82,
            risk=0.15,
            cost=0.02,
            estimated_runtime_seconds=20,
            touched_files=["calc.py"],
            rollback_plan="revert",
            rollout_level=RolloutLevel.PAPER,
            generation_mode=BidGenerationMode.PROVIDER_MODEL,
        )

        candidates = runtime._generate_execution_candidates(
            task,
            bid,
            {"calc.py": "def add(a, b):\n    return a - b\n"},
        )
    finally:
        runtime.store.close()

    assert backend.calls == [["anthropic"], ["openai"]]
    assert any(candidate.provider == "openai" and candidate.proposal.files for candidate in candidates)


def test_collect_defers_baseline_commands_for_fast_market_open(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests",
        mission_id="lightweight-collect",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    calls: list[bool] = []

    def fake_collect(*, run_commands: bool = True, objective: str | None = None):
        del objective
        calls.append(run_commands)
        return RepoSnapshot(
            repo_path=str(python_bug_repo),
            branch="main",
            head_commit="abc12345",
            tracking_branch="origin/main",
            dirty=False,
            remotes={},
            default_remote=None,
            remote_provider=None,
            remote_slug=None,
            objective_hints={},
            changed_files=[],
            untracked_files=[],
            tree_summary=["calc.py", "tests"],
            dependency_files=["pyproject.toml"],
            complexity_hotspots=["calc.py"],
            failure_signals=[],
            capabilities=CapabilitySet(runtime="python", risky_paths=["calc.py"], protected_interfaces=[]),
            initial_test_results=[],
            initial_lint_results=[],
            initial_static_results=[],
        )

    runtime.collector.collect = fake_collect
    try:
        runtime.store.upsert_mission(
            mission_id=spec.mission_id,
            status="running",
            repo_path=spec.repo_path,
            objective=spec.objective,
            branch_name=runtime.branch_name,
            outcome=None,
            spec=spec,
            summary=MissionSummary(mission_id=spec.mission_id, repo_path=spec.repo_path, objective=spec.objective),
            created_at=spec.created_at.isoformat(),
        )
        runtime.store.upsert_control_state(
            spec.mission_id,
            RunState.RUNNING.value,
            None,
            None,
            utc_now().isoformat(),
        )
        result = runtime.node_collect()
        runtime.state.repo_snapshot = None
        runtime.state.active_phase = ActivePhase.STRATEGIZE
        runtime._restore_runtime_context()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.STRATEGIZE.value}
    assert calls == [False, False]


def test_collect_activates_github_context_before_bidding(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Investigate PR #12 and issue #7 before fixing failing tests",
        mission_id="civic-github-context",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(available_skills=["github_context"])
    try:
        runtime._prepare_run()
        result = runtime.node_collect()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.STRATEGIZE.value}
    assert runtime.state.civic_connection.connected is True
    assert "github_context" in runtime.state.available_skills
    assert runtime.state.skill_outputs["github_context"]["ci_summary"] == "ci-failing"
    assert "Civic CI summary: ci-failing" in runtime.state.repo_snapshot.failure_signals


def test_preflight_governed_bids_rejects_blocked_strategy(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests",
        mission_id="civic-preflight",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(available_skills=["github_context"])
    try:
        runtime._prepare_run()
        runtime.state.repo_snapshot = runtime.collector.collect(run_commands=False, objective=spec.objective)
        runtime._refresh_civic_capability_plane("test")
        task = TaskNode(
            task_id="task-1",
            title="Select the next governed move",
            task_type=TaskType.BUGFIX,
            requirement_level=TaskRequirementLevel.REQUIRED,
            success_criteria=SuccessCriteria(description="Tests pass"),
            candidate_files=["calc.py"],
        )
        allowed_bid = Bid(
            bid_id="bid-allowed",
            task_id=task.task_id,
            role="Test",
            variant_id="test-base",
            strategy_family="coverage-first",
            strategy_summary="Use Civic evidence to guide the fix.",
            exact_action="Inspect CI and patch calc.py.",
            expected_benefit=0.8,
            utility=0.8,
            confidence=0.7,
            risk=0.2,
            cost=0.1,
            estimated_runtime_seconds=30,
            rollback_plan="revert",
            required_skills=["github_context"],
            governed_action_plan=["fetch_ci_status"],
        )
        blocked_bid = allowed_bid.model_copy(update={"bid_id": "bid-blocked"})
        runtime.state.active_bids = [allowed_bid, blocked_bid]
        runtime.state.active_bid_round = 1

        runtime._preflight_governed_bids(task)
    finally:
        runtime.store.close()

    assert runtime.state.governed_bid_envelopes["bid-allowed"].status == "approved"
    assert runtime.state.governed_bid_envelopes["bid-blocked"].status == "blocked"
    assert blocked_bid.rejection_reason == "civic_policy_block"


def test_collect_safe_stops_when_requested_skill_is_unavailable(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Use GitHub context before fixing the regression",
        requested_skills=["github_context"],
        mission_id="civic-required-skill",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(available_skills=[], connected=False)
    try:
        runtime._prepare_run()
        result = runtime.node_collect()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.FINALIZE.value}
    assert runtime.state.outcome == MissionOutcome.FAILED_SAFE_STOP
    assert runtime.state.governance.stop_reason == "requested_skill_unavailable:github_context"
