from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MissionOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED_SAFE_STOP = "failed_safe_stop"
    FAILED_EXECUTION = "failed_execution"


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    FINALIZED = "finalized"


class ActivePhase(str, Enum):
    IDLE = "idle"
    COLLECT = "collect"
    DECOMPOSE = "decompose"
    SELECT_TASK = "select_task"
    MARKET = "market"
    EXECUTE = "execute"
    VALIDATE = "validate"
    RECOVER = "recover"
    FINALIZE = "finalize"


class TaskRequirementLevel(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskType(str, Enum):
    LOCALIZE = "localize"
    BUGFIX = "bugfix"
    TEST = "test"
    REFACTOR = "refactor"
    PERF_DIAGNOSIS = "perf_diagnosis"
    PERF_OPTIMIZE = "perf_optimize"
    VALIDATE = "validate"


class RolloutLevel(str, Enum):
    PAPER = "paper"
    CHEAP_PARTIAL = "cheap_partial"
    SANDBOX = "sandbox"


class ApprovalMode(str, Enum):
    HARD_POLICY_ONLY = "hard_policy_only"


class StopPolicy(BaseModel):
    max_runtime_minutes: int = 10
    max_recovery_rounds: int = 3
    max_policy_collisions: int = 2
    max_file_churn: int = 8


class RiskPolicy(BaseModel):
    max_cross_module_blast_radius: int = 3
    require_tests_for_bugfix: bool = True
    require_benchmark_for_perf_claim: bool = True
    block_public_api_changes: bool = True


class ApprovalPolicy(BaseModel):
    mode: ApprovalMode = ApprovalMode.HARD_POLICY_ONLY


class SuccessCriteria(BaseModel):
    description: str
    required_validators: list[str] = Field(default_factory=list)
    required_signals: list[str] = Field(default_factory=list)


class MissionSpec(BaseModel):
    mission_id: str
    repo_path: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    max_runtime_minutes: int = 10
    allowed_tool_classes: list[str] = Field(
        default_factory=lambda: ["read", "search", "edit", "diff", "test", "lint", "static", "benchmark", "revert"]
    )
    benchmark_requirement: str | None = None
    protected_paths: list[str] = Field(default_factory=list)
    public_api_surface: list[str] = Field(default_factory=list)
    stop_policy: StopPolicy = Field(default_factory=StopPolicy)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    created_at: datetime = Field(default_factory=utc_now)


class MissionPaths(BaseModel):
    repo_path: str
    root_dir: str
    db_path: str
    events_path: str
    metadata_path: str
    reports_dir: str
    replay_dir: str
    worktree_dir: str


class MissionSummary(BaseModel):
    mission_id: str
    repo_path: str | None = None
    objective: str | None = None
    outcome: MissionOutcome | None = None
    branch_name: str | None = None
    head_commit: str | None = None
    decision_history: list[str] = Field(default_factory=list)
    failed_attempt_history: list[str] = Field(default_factory=list)
    validation_reports: list[str] = Field(default_factory=list)
    runtime_seconds: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usage: dict[str, float] = Field(default_factory=dict)
    audit_summary: dict[str, Any] = Field(default_factory=dict)


class CommandResult(BaseModel):
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class CapabilitySet(BaseModel):
    runtime: Literal["python", "tsjs", "unsupported"]
    test_commands: list[list[str]] = Field(default_factory=list)
    lint_commands: list[list[str]] = Field(default_factory=list)
    static_commands: list[list[str]] = Field(default_factory=list)
    benchmark_commands: list[list[str]] = Field(default_factory=list)
    is_single_package_tsjs: bool = False
    unsupported_reason: str | None = None


class RepoSnapshot(BaseModel):
    repo_path: str
    branch: str | None = None
    head_commit: str | None = None
    dirty: bool = False
    changed_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    tree_summary: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    complexity_hotspots: list[str] = Field(default_factory=list)
    failure_signals: list[str] = Field(default_factory=list)
    capabilities: CapabilitySet
    initial_test_results: list[CommandResult] = Field(default_factory=list)
    initial_lint_results: list[CommandResult] = Field(default_factory=list)
    initial_static_results: list[CommandResult] = Field(default_factory=list)


class TaskNode(BaseModel):
    task_id: str
    title: str
    task_type: TaskType
    requirement_level: TaskRequirementLevel
    dependencies: list[str] = Field(default_factory=list)
    success_criteria: SuccessCriteria
    allowed_tools: list[str] = Field(default_factory=list)
    rollback_conditions: list[str] = Field(default_factory=list)
    validator_requirements: list[str] = Field(default_factory=list)
    risk_level: float = 0.3
    runtime_class: Literal["small", "medium", "large"] = "small"
    expected_artifact: str | None = None
    candidate_files: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


class Bid(BaseModel):
    bid_id: str
    task_id: str
    role: str
    variant_id: str
    strategy_family: str
    strategy_summary: str
    exact_action: str
    expected_benefit: float
    utility: float
    confidence: float
    risk: float
    cost: float
    estimated_runtime_seconds: float
    touched_files: list[str] = Field(default_factory=list)
    validator_plan: list[str] = Field(default_factory=list)
    rollback_plan: str
    dependency_impact: str = "localized"
    rollout_level: RolloutLevel = RolloutLevel.PAPER
    score: float | None = None
    rejection_reason: str | None = None
    can_be_standby: bool = True
    promotion_hints: list[str] = Field(default_factory=list)


class ExecutionStep(BaseModel):
    step_id: str
    task_id: str
    bid_id: str
    action_type: str
    description: str
    tool_name: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    approved: bool = True
    success: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class ValidationReport(BaseModel):
    task_id: str
    passed: bool
    command_results: list[CommandResult] = Field(default_factory=list)
    file_churn: int = 0
    changed_files: list[str] = Field(default_factory=list)
    api_guard_passed: bool = True
    benchmark_delta: float | None = None
    notes: list[str] = Field(default_factory=list)


class FailureContext(BaseModel):
    task_id: str
    failure_type: str
    details: str
    diff_summary: str
    validator_deltas: list[str] = Field(default_factory=list)
    recommended_recovery_scope: str


class AcceptedCheckpoint(BaseModel):
    checkpoint_id: str
    label: str
    commit_sha: str
    created_at: datetime = Field(default_factory=utc_now)
    summary: str | None = None


class MissionEvent(BaseModel):
    event_type: str
    mission_id: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MissionControlState(BaseModel):
    run_state: RunState = RunState.IDLE
    requested_action: Literal["pause", "resume", "cancel"] | None = None
    reason: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class ReplayRecord(BaseModel):
    lane: str
    key: str
    prompt: dict[str, Any]
    response: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class ArbiterState(BaseModel):
    mission: MissionSpec
    summary: MissionSummary = Field(default_factory=lambda: MissionSummary(mission_id=""))
    control: MissionControlState = Field(default_factory=MissionControlState)
    active_phase: ActivePhase = ActivePhase.IDLE
    active_bid_round: int = 0
    active_bids: list[Bid] = Field(default_factory=list)
    winner_bid_id: str | None = None
    standby_bid_id: str | None = None
    repo_snapshot: RepoSnapshot | None = None
    tasks: list[TaskNode] = Field(default_factory=list)
    active_task_id: str | None = None
    current_bid: Bid | None = None
    standby_bid: Bid | None = None
    failure_context: FailureContext | None = None
    validation_report: ValidationReport | None = None
    recovery_round: int = 0
    policy_collisions: int = 0
    runtime_seconds: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usage: dict[str, float] = Field(default_factory=dict)
    decision_history: list[str] = Field(default_factory=list)
    latest_diff_summary: str = ""
    no_valid_contenders: bool = False
    outcome: MissionOutcome | None = None
