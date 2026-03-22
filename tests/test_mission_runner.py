from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from arbiter.agents.backend import DefaultStrategyBackend, EditOperation, EditProposal, FileUpdate, ModelInvocationResult, ProposalCandidate, ScriptedStrategyBackend
from arbiter.core.contracts import (
    ActivePhase,
    Bid,
    BidGenerationMode,
    BidStatus,
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
from arbiter.runtime.paths import build_managed_branch_name, build_mission_paths
from arbiter.repo.worktree import WorktreeSetupError


class FakeGovernedCivic:
    def __init__(self, *, available_skills: list[str] | None = None, connected: bool = True, configured: bool = False) -> None:
        self.available_skills = available_skills or []
        self.connected = connected
        self.configured = configured
        self.actions: list[str] = []

    def available(self) -> bool:
        return self.configured

    def refresh_capability_state(self, repo_snapshot=None, force: bool = False):
        del force
        capabilities: list[CivicCapability] = []
        if self.connected:
            capabilities.extend(
                [
                    CivicCapability(
                        capability_id="github_read",
                        display_name="GitHub Read Context",
                        tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"],
                    ),
                    CivicCapability(
                        capability_id="github_write",
                        display_name="GitHub Branch and Pull Request Delivery",
                        read_only=False,
                        tools=["github-remote-create_branch", "github-remote-push_files", "github-remote-create_pull_request"],
                    ),
                ]
            )
            if "knowledge_context" in self.available_skills:
                capabilities.append(
                    CivicCapability(
                        capability_id="knowledge_read",
                        display_name="Knowledge Retrieval",
                        tools=["knowledge_retrieval"],
                    )
                )
            if "trusted_external_context" in self.available_skills:
                capabilities.append(
                    CivicCapability(
                        capability_id="guardrail_proxy",
                        display_name="Trusted External Context",
                        tools=["guardrail_proxy", "pass_through_proxy", "bodyguard"],
                    )
                )
        return {
            "connection": CivicConnectionStatus(
                configured=self.configured or self.connected,
                connected=self.connected,
                available=self.connected,
                required=False,
                status="connected" if self.connected else "unavailable",
                base_url="https://civic.example",
                toolkit_id="toolkit-demo",
                last_checked_at=utc_now(),
            ),
            "capabilities": capabilities,
            "available_skills": list(self.available_skills) if repo_snapshot is not None else [],
            "skill_health": {
                "github_context": SkillHealth(
                    skill_id="github_context",
                    status="available" if "github_context" in self.available_skills else "inactive",
                    available="github_context" in self.available_skills,
                    required_tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"],
                    available_tools=["fetch_ci_status", "open_pr_metadata", "open_issue_metadata"] if self.connected else [],
                    last_checked_at=utc_now(),
                ),
                "github_publish": SkillHealth(
                    skill_id="github_publish",
                    status="available" if "github_publish" in self.available_skills else "inactive",
                    available="github_publish" in self.available_skills,
                    required_tools=["github-remote-create_branch", "github-remote-push_files", "github-remote-create_pull_request"],
                    available_tools=["github-remote-create_branch", "github-remote-push_files", "github-remote-create_pull_request"] if self.connected else [],
                    last_checked_at=utc_now(),
                ),
                "knowledge_context": SkillHealth(
                    skill_id="knowledge_context",
                    status="available" if "knowledge_context" in self.available_skills else "inactive",
                    available="knowledge_context" in self.available_skills,
                    required_tools=["knowledge_retrieval"],
                    available_tools=["knowledge_retrieval"] if self.connected and "knowledge_context" in self.available_skills else [],
                    last_checked_at=utc_now(),
                ),
                "trusted_external_context": SkillHealth(
                    skill_id="trusted_external_context",
                    status="available" if "trusted_external_context" in self.available_skills else "inactive",
                    available="trusted_external_context" in self.available_skills,
                    required_tools=["guardrail_proxy", "pass_through_proxy", "bodyguard"],
                    available_tools=["guardrail_proxy", "pass_through_proxy", "bodyguard"] if self.connected and "trusted_external_context" in self.available_skills else [],
                    last_checked_at=utc_now(),
                ),
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
        elif action_type == "github-remote-create_branch":
            record.output_payload = {"branch": payload["branch"], "base": payload.get("from_branch")}
        elif action_type == "github-remote-push_files":
            record.output_payload = {
                "branch": payload["branch"],
                "pushed_files": [item["path"] for item in payload.get("files", [])],
            }
        elif action_type == "github-remote-create_pull_request":
            record.output_payload = {
                "number": 42,
                "html_url": f"https://github.com/{payload['owner']}/{payload['repo']}/pull/42",
                "title": payload["title"],
                "head": payload["head"],
                "base": payload["base"],
            }
        elif action_type == "knowledge_retrieval":
            query = str(payload.get("query") or "governed research").strip()
            record.output_payload = {
                "summary": f"Research brief for {query}. Civic found the latest guidance and safety notes for this task.",
                "source_urls": [
                    "https://docs.langchain.com/langgraph",
                    "https://docs.civic.com/guardrails",
                ],
                "results": [
                    {
                        "title": "LangGraph docs",
                        "snippet": "Checkpointer setup and workflow recovery guidance.",
                        "url": "https://docs.langchain.com/langgraph",
                    },
                    {
                        "title": "Civic guardrails",
                        "snippet": "Use governed tools and safety checks around external actions.",
                        "url": "https://docs.civic.com/guardrails",
                    },
                ],
                "confidence": 0.91,
                "freshness": {"checked_at": utc_now().isoformat(), "age_seconds": 0},
            }
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

    def authorize_and_execute(self, *, mission_id, task_id, action_type, decision, payload, executor):
        audit = CivicAuditRecord(
            audit_id=f"audit-exec-{task_id}",
            mission_id=mission_id,
            task_id=task_id,
            action_type=action_type,
            status="executed" if decision.allowed else "blocked",
            policy_state=decision.state,
            reasons=list(decision.reasons),
            payload=payload,
        )
        if not decision.allowed:
            return type(
                "GovernedExecutionResult",
                (),
                {"success": False, "result": {"blocked": True}, "audit": audit},
            )()
        if self.configured and not self.connected:
            audit.status = "blocked"
            audit.policy_state = PolicyState.RESTRICTED
            audit.reasons.append("civic_transport_unavailable")
            return type(
                "GovernedExecutionResult",
                (),
                {"success": False, "result": {"blocked": True, "reasons": list(audit.reasons)}, "audit": audit},
            )()
        result = executor() if executor is not None else {}
        return type(
            "GovernedExecutionResult",
            (),
            {"success": True, "result": result, "audit": audit},
        )()

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
            constraints=["read_write_scope:read_only", "civic_connection:connected"],
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


def test_execute_compares_all_enabled_provider_proposals_with_winner_first(python_bug_repo: Path) -> None:
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
            research_context=None,
            preview=False,
            providers=None,
            on_invocation=None,
        ):
            del task, bid, mission_objective, candidate_files, failure_context, research_context, preview, on_invocation
            requested = list(providers or [])
            self.calls.append(requested)
            candidates = []
            for provider in requested or ["anthropic"]:
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
                candidates.append(
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
                )
            return candidates

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

    assert backend.calls == [["anthropic", "openai"]]
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


def test_collect_skips_civic_context_for_plain_github_repo_without_requested_skills(python_bug_repo: Path) -> None:
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/python_bug_repo.git"],
        cwd=str(python_bug_repo),
        check=True,
        capture_output=True,
        text=True,
    )
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests in the local repo",
        mission_id="civic-checkbox-off",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(
        available_skills=["github_context", "knowledge_context", "trusted_external_context"],
        connected=True,
        configured=True,
    )
    try:
        runtime._prepare_run()
        result = runtime.node_collect()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.STRATEGIZE.value}
    assert runtime.state.available_skills == []
    assert runtime.state.skill_outputs == {}
    assert runtime.civic.actions == []


def test_build_managed_branch_name_uses_repo_and_objective_context(python_bug_repo: Path) -> None:
    branch_name = build_managed_branch_name(
        python_bug_repo,
        "Fix failing tests and improve reliability",
        "missionabcdef12",
    )

    assert branch_name.startswith("codex/")
    assert "python_bug_repo" in branch_name
    assert "fix-failing-tests" in branch_name
    assert not branch_name.startswith("codex/helix-")


def test_build_mission_spec_defaults_to_unbounded_runtime(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests without a hard runtime budget",
        mission_id="runtime-unbounded",
    )

    assert spec.max_runtime_minutes is None
    assert spec.stop_policy.max_runtime_minutes is None


def test_governance_keeps_unbounded_runtime_missions_running(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests without a hard runtime budget",
        mission_id="runtime-unbounded-governance",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    try:
        runtime._prepare_run()
        runtime.state.runtime_seconds = 60 * 60 * 12
        runtime.state.repo_snapshot = runtime.collector.collect(run_commands=False, objective=spec.objective)
        task = TaskNode(
            task_id="task-1",
            title="Implement the fix",
            task_type=TaskType.BUGFIX,
            requirement_level=TaskRequirementLevel.REQUIRED,
            success_criteria=SuccessCriteria(description="Tests pass"),
            candidate_files=["calc.py"],
        )
        bid = Bid(
            bid_id="bid-unbounded",
            task_id=task.task_id,
            role="Safe",
            variant_id="safe-base",
            strategy_family="localized-fix",
            strategy_summary="Patch the calculator defect with minimal churn.",
            exact_action="Fix calc.py and validate tests.",
            expected_benefit=0.8,
            utility=0.81,
            confidence=0.84,
            risk=0.18,
            cost=0.1,
            estimated_runtime_seconds=60 * 60 * 18,
            touched_files=["calc.py"],
            rollback_plan="revert",
        )

        decision = runtime.governance.evaluate_bid(task, bid, spec, failed_families=set())
        progress = runtime.governance.evaluate_mission_progress(runtime.state)
        stop = runtime.governance.evaluate_stop(runtime.state)
    finally:
        runtime.store.close()

    assert "runtime_budget_exceeded" not in decision.reasons
    assert progress["budget_remaining_pct"] is None
    assert stop.should_stop is False


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
            score=0.92,
        )
        plain_allowed_bid = allowed_bid.model_copy(
            update={
                "bid_id": "bid-plain-allowed",
                "required_skills": [],
                "optional_skills": [],
                "governed_action_plan": [],
                "score": 0.88,
            }
        )
        blocked_bid = allowed_bid.model_copy(
            update={
                "bid_id": "bid-plain-blocked",
                "required_skills": [],
                "optional_skills": [],
                "governed_action_plan": [],
                "score": 0.84,
            }
        )
        lower_ranked_bid = allowed_bid.model_copy(update={"bid_id": "bid-lower-ranked", "score": 0.33})
        runtime.state.active_bids = [allowed_bid, plain_allowed_bid, blocked_bid, lower_ranked_bid]
        runtime.state.active_bid_round = 1

        runtime._preflight_governed_bids(task)
    finally:
        runtime.store.close()

    assert runtime.state.governed_bid_envelopes["bid-allowed"].status == "approved"
    assert runtime.state.governed_bid_envelopes["bid-allowed"].constraints == [
        "read_write_scope:read_only",
        "civic_connection:connected",
    ]
    assert runtime.state.governed_bid_envelopes["bid-plain-allowed"].status == "approved"
    assert runtime.state.governed_bid_envelopes["bid-plain-blocked"].status == "blocked"
    assert runtime.state.governed_bid_envelopes["bid-plain-blocked"].constraints == [
        "read_write_scope:read_only",
        "civic_connection:connected",
    ]
    assert "bid-lower-ranked" not in runtime.state.governed_bid_envelopes
    assert blocked_bid.rejection_reason == "civic_policy_block"
    assert blocked_bid.status == BidStatus.REJECTED


def test_collect_enriches_knowledge_context_before_bidding(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Investigate the LangGraph checkpoint issue using Civic and Firecrawl-style research",
        requested_skills=["knowledge_context"],
        mission_id="civic-knowledge-context",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(
        available_skills=["knowledge_context", "trusted_external_context"],
        connected=True,
        configured=True,
    )
    try:
        runtime._prepare_run()
        result = runtime.node_collect()
    finally:
        runtime.store.close()

    knowledge = runtime.state.skill_outputs["knowledge_context"]
    assert result == {"status": ActivePhase.STRATEGIZE.value}
    assert knowledge["summary"]
    assert knowledge["source_urls"]
    assert knowledge["queries"]
    assert knowledge["provenance"]["trusted"] is True
    assert any(record.action_type == "knowledge_retrieval" for record in runtime.state.recent_civic_actions)


def test_provider_backed_mission_reaches_civic_preflight_and_executes_with_openai_usage(python_bug_repo: Path) -> None:
    from tests.fake_provider_backend import FakeProviderRouter

    class GovernedProviderRouter(FakeProviderRouter):
        @staticmethod
        def _bid_payload(user_prompt: str, provider: str, lane: str) -> str:
            payload = json.loads(FakeProviderRouter._bid_payload(user_prompt, provider, lane))
            payload["required_skills"] = ["github_context"]
            payload["governed_action_plan"] = ["fetch_ci_status"]
            return json.dumps(payload)

    backend = DefaultStrategyBackend(GovernedProviderRouter(providers=("openai",)))
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests with provider-backed execution",
        requested_skills=["github_context"],
        mission_id="provider-civic-openai",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    runtime.civic = FakeGovernedCivic(available_skills=["github_context"])
    try:
        state = runtime.run()
    finally:
        runtime.store.close()

    assert state.outcome is not None
    assert state.outcome.value == "success"

    mission_root = python_bug_repo / ".arbiter" / "missions" / spec.mission_id
    connection = sqlite3.connect(mission_root / "state.db")
    connection.row_factory = sqlite3.Row
    try:
        langgraph_checkpoint_count = connection.execute("SELECT COUNT(*) FROM langgraph_checkpoints").fetchone()[0]
        traces = connection.execute("SELECT trace_type FROM trace_entries ORDER BY id ASC").fetchall()
        invocations = connection.execute(
            "SELECT provider, invocation_kind, status, cost_usage_json FROM model_invocations ORDER BY id ASC"
        ).fetchall()
        execution_steps = connection.execute("SELECT COUNT(*) FROM execution_steps").fetchone()[0]
    finally:
        connection.close()

    assert langgraph_checkpoint_count >= 1
    assert any(row["trace_type"] == "civic.bid.preflight_allowed" for row in traces)
    assert any(
        row["provider"] == "openai"
        and row["invocation_kind"] == "proposal_generation"
        and row["status"] == "completed"
        and row["cost_usage_json"] not in {None, "null"}
        for row in invocations
    )
    assert execution_steps >= 1


def test_provider_backed_mission_threads_governed_research_into_bid_and_proposal_prompts(python_bug_repo: Path) -> None:
    from tests.fake_provider_backend import FakeProviderRouter

    backend = DefaultStrategyBackend(FakeProviderRouter(providers=("openai",)))
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix the LangGraph checkpoint error with Civic-safe research and provider-backed execution",
        requested_skills=["knowledge_context"],
        mission_id="provider-civic-research",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    runtime.civic = FakeGovernedCivic(
        available_skills=["knowledge_context", "trusted_external_context"],
        connected=True,
        configured=True,
    )
    try:
        state = runtime.run()
    finally:
        runtime.store.close()

    assert state.outcome is not None
    assert state.outcome.value == "success"
    assert state.skill_outputs["knowledge_context"]["summary"]

    mission_root = python_bug_repo / ".arbiter" / "missions" / spec.mission_id
    connection = sqlite3.connect(mission_root / "state.db")
    connection.row_factory = sqlite3.Row
    try:
        bid_prompts = connection.execute(
            "SELECT prompt_preview FROM model_invocations WHERE invocation_kind = 'bid_generation' ORDER BY id ASC"
        ).fetchall()
        proposal_prompts = connection.execute(
            "SELECT prompt_preview FROM model_invocations WHERE invocation_kind = 'proposal_generation' ORDER BY id ASC"
        ).fetchall()
        bid_rows = connection.execute("SELECT payload_json FROM bids ORDER BY id ASC").fetchall()
    finally:
        connection.close()

    assert any("Governed research brief:" in str(row["prompt_preview"]) for row in bid_prompts)
    assert any("Governed external research:" in str(row["prompt_preview"]) for row in proposal_prompts)
    assert any(
        "knowledge_context" in (json.loads(row["payload_json"]).get("required_skills") or [])
        or "knowledge_context" in (json.loads(row["payload_json"]).get("optional_skills") or [])
        for row in bid_rows
    )
    assert any(
        "knowledge_retrieval" in (json.loads(row["payload_json"]).get("external_evidence_plan") or [])
        for row in bid_rows
    )


def test_successful_github_mission_publishes_pull_request_via_civic(python_bug_repo: Path) -> None:
    from tests.fake_provider_backend import FakeProviderRouter

    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/python_bug_repo.git"],
        cwd=str(python_bug_repo),
        check=True,
        capture_output=True,
        text=True,
    )
    backend = DefaultStrategyBackend(FakeProviderRouter(providers=("openai",)))
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests and open a review branch",
        requested_skills=["github_context"],
        mission_id="github-pr-publish",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    runtime.civic = FakeGovernedCivic(
        available_skills=["github_context", "github_publish"],
        connected=True,
        configured=True,
    )
    try:
        state = runtime.run()
    finally:
        runtime.store.close()

    assert state.outcome is not None
    assert state.outcome.value == "success"
    assert state.summary.branch_name.startswith("codex/")
    assert "python_bug_repo" in state.summary.branch_name
    assert not state.summary.branch_name.startswith("codex/helix-")
    publish = state.skill_outputs["github_publish"]
    assert publish["published"] is True
    assert publish["pull_request"]["number"] == 42
    assert publish["pull_request"]["base"] == "main"
    assert "github-remote-create_branch" in runtime.civic.actions
    assert "github-remote-push_files" in runtime.civic.actions
    assert "github-remote-create_pull_request" in runtime.civic.actions


def test_successful_plain_github_mission_autopublishes_pull_request_without_prior_civic_context(python_bug_repo: Path) -> None:
    from tests.fake_provider_backend import FakeProviderRouter

    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/python_bug_repo.git"],
        cwd=str(python_bug_repo),
        check=True,
        capture_output=True,
        text=True,
    )
    backend = DefaultStrategyBackend(FakeProviderRouter(providers=("openai",)))
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests in the local repo",
        mission_id="github-pr-autopublish",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=backend)
    runtime.civic = FakeGovernedCivic(
        available_skills=["github_context", "github_publish"],
        connected=True,
        configured=True,
    )
    try:
        state = runtime.run()
    finally:
        runtime.store.close()

    assert state.outcome is not None
    assert state.outcome.value == "success"
    assert "github_context" not in state.skill_outputs
    publish = state.skill_outputs["github_publish"]
    assert publish["published"] is True
    assert publish["pull_request"]["number"] == 42
    assert "fetch_ci_status" not in runtime.civic.actions
    assert "github-remote-create_branch" in runtime.civic.actions
    assert "github-remote-push_files" in runtime.civic.actions
    assert "github-remote-create_pull_request" in runtime.civic.actions


def test_collect_ignores_unavailable_civic_when_no_skills_are_requested(python_bug_repo: Path) -> None:
    spec = build_mission_spec(
        repo=str(python_bug_repo),
        objective="Fix failing tests",
        mission_id="civic-always-governed",
    )
    paths = build_mission_paths(spec.repo_path, spec.mission_id)
    runtime = MissionRuntime(spec, paths, strategy_backend=ScriptedStrategyBackend([]))
    runtime.civic = FakeGovernedCivic(available_skills=[], connected=False, configured=True)
    try:
        runtime._prepare_run()
        result = runtime.node_collect()
    finally:
        runtime.store.close()

    assert result == {"status": ActivePhase.STRATEGIZE.value}
    assert runtime.state.outcome is None
    assert runtime.state.governance.stop_reason is None


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
