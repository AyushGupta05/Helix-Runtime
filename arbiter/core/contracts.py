from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class MissionOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED_SAFE_STOP = "failed_safe_stop"
    FAILED_EXECUTION = "failed_execution"
    POLICY_BLOCKED = "policy_blocked"


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    FINALIZED = "finalized"


class ActivePhase(str, Enum):
    IDLE = "idle"
    COLLECT = "collect"
    STRATEGIZE = "strategize"
    SIMULATE = "simulate"
    SELECT = "select"
    EXECUTE = "execute"
    VALIDATE = "validate"
    RECOVER = "recover"
    FINALIZE = "finalize"
    # Legacy phases kept for state-resume compatibility.
    DECOMPOSE = "decompose"
    SELECT_TASK = "select_task"
    MARKET = "market"


class TaskRequirementLevel(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "complete"
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
    PARTIAL = "partial"
    SANDBOX = "sandbox"


class ApprovalMode(str, Enum):
    HARD_POLICY_ONLY = "hard_policy_only"


class PolicyState(str, Enum):
    CLEAR = "clear"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"


class GenerationMode(str, Enum):
    PROVIDER_MODEL = "provider_model"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    MOCK = "mock"
    REPLAY = "replay"


BidGenerationMode = GenerationMode


class BidStatus(str, Enum):
    GENERATED = "generated"
    REJECTED = "rejected"
    SIMULATED = "simulated"
    WINNER = "winner"
    STANDBY = "standby"
    EXECUTING = "executing"
    FAILED = "failed"
    RETIRED = "retired"


class ActionStatus(str, Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    EXECUTED = "executed"


class StopPolicy(BaseModel):
    max_runtime_minutes: int = 10
    max_recovery_rounds: int = 3
    max_policy_collisions: int = 2
    max_file_churn: int = 8
    max_diff_lines: int = 600
    max_file_scope: int = 8


class RiskPolicy(BaseModel):
    max_cross_module_blast_radius: int = 3
    require_tests_for_bugfix: bool = True
    require_benchmark_for_perf_claim: bool = True
    block_public_api_changes: bool = True
    max_risk_score: float = 0.85


class ApprovalPolicy(BaseModel):
    mode: ApprovalMode = ApprovalMode.HARD_POLICY_ONLY


class BiddingPolicy(BaseModel):
    require_provider_backed_bids: bool = True
    allow_degraded_fallback: bool = False


class SuccessCriteria(BaseModel):
    description: str
    required_validators: list[str] = Field(default_factory=list)
    required_signals: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)


class MissionSpec(BaseModel):
    mission_id: str
    repo_path: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    max_runtime_minutes: int = 10
    allowed_tool_classes: list[str] = Field(
        default_factory=lambda: [
            "read_file",
            "search_code",
            "edit_file",
            "run_tests",
            "run_lint",
            "static_analysis",
            "benchmark",
            "create_commit",
            "revert_to_checkpoint",
            "fetch_ci_status",
            "open_pr_metadata",
            "request_patch_apply",
        ]
    )
    benchmark_requirement: str | None = None
    protected_paths: list[str] = Field(default_factory=list)
    public_api_surface: list[str] = Field(default_factory=list)
    requested_skills: list[str] = Field(default_factory=list)
    stop_policy: StopPolicy = Field(default_factory=StopPolicy)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    bidding_policy: BiddingPolicy = Field(default_factory=BiddingPolicy)
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
    scratch_worktrees_dir: str
    legacy_root_dir: str | None = None


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
    current_risk_score: float = 0.0
    stop_reason: str | None = None
    civic_audit_ids: list[str] = Field(default_factory=list)
    bidding_state: dict[str, Any] = Field(default_factory=dict)


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
    risky_paths: list[str] = Field(default_factory=list)
    protected_interfaces: list[str] = Field(default_factory=list)


class RepoSnapshot(BaseModel):
    repo_path: str
    branch: str | None = None
    head_commit: str | None = None
    tracking_branch: str | None = None
    dirty: bool = False
    remotes: dict[str, str] = Field(default_factory=dict)
    default_remote: str | None = None
    remote_provider: str | None = None
    remote_slug: str | None = None
    objective_hints: dict[str, Any] = Field(default_factory=dict)
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


class PolicyDecision(BaseModel):
    allowed: bool
    state: PolicyState = PolicyState.CLEAR
    reasons: list[str] = Field(default_factory=list)
    risk_score: float = 0.0
    validators_required: list[str] = Field(default_factory=list)
    blocked_files: list[str] = Field(default_factory=list)


class GovernanceSnapshot(BaseModel):
    policy_state: PolicyState = PolicyState.CLEAR
    stop_reason: str | None = None
    current_risk_score: float = 0.0
    last_decision: PolicyDecision | None = None
    active_guardrails: list[str] = Field(default_factory=list)


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
    search_depth: int = 2
    monte_carlo_samples: int = 24
    expected_artifact: str | None = None
    candidate_files: list[str] = Field(default_factory=list)
    policy_constraints: list[str] = Field(default_factory=list)
    strategy_families: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING

    @property
    def required(self) -> bool:
        return self.requirement_level == TaskRequirementLevel.REQUIRED


class Bid(BaseModel):
    bid_id: str
    task_id: str
    role: str
    provider: str | None = None
    lane: str | None = None
    model_id: str | None = None
    invocation_id: str | None = None
    invocation_kind: Literal["bid_generation"] = "bid_generation"
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
    mutation_parent_id: str | None = None
    mutation_kind: str | None = None
    policy_feasibility: PolicyDecision = Field(default_factory=lambda: PolicyDecision(allowed=True))
    civic_permission_footprint: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    optional_skills: list[str] = Field(default_factory=list)
    governed_action_plan: list[str] = Field(default_factory=list)
    external_evidence_plan: list[str] = Field(default_factory=list)
    capability_reliance_score: float = 0.0
    policy_friction_score: float = 0.0
    revocation_risk_score: float = 0.0
    active_envelope_id: str | None = None
    score: float | None = None
    search_score: float | None = None
    search_reward: float | None = None
    search_summary: str | None = None
    search_diagnostics: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int] | None = None
    cost_usage: dict[str, float] | None = None
    usage_unavailable_reason: str | None = None
    prompt_preview: str | None = None
    response_preview: str | None = None
    mission_rationale: str = ""
    proposed_task_title: str = ""
    proposed_task_type: str = ""
    selection_reason: str | None = None
    rejection_reason: str | None = None
    can_be_standby: bool = True
    promotion_hints: list[str] = Field(default_factory=list)
    generation_mode: GenerationMode = GenerationMode.DETERMINISTIC_FALLBACK
    status: BidStatus = BidStatus.GENERATED


class SimulationSummary(BaseModel):
    task_id: str
    search_mode: str = "bounded_monte_carlo"
    total_bids: int = 0
    valid_bids: int = 0
    paper_rollouts: int = 0
    partial_rollouts: int = 0
    sandbox_rollouts: int = 0
    budget_used: int = 0
    frontier_size: int = 0
    monte_carlo_samples: int = 0
    frontier_gap: float = 0.0
    risk_forecast: float = 0.0
    validator_stability: float = 0.0
    rollback_safety: float = 0.0
    policy_confidence: float = 0.0
    capability_availability: float = 1.0
    policy_friction: float = 0.0
    revocation_risk: float = 0.0
    evidence_quality: float = 0.0
    freshness_score: float = 1.0
    guardrail_narrowing: float = 0.0
    summary: str = ""


class ActionIntent(BaseModel):
    action_type: str
    task_id: str
    bid_id: str | None = None
    file_scope: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class CivicAuditRecord(BaseModel):
    audit_id: str
    mission_id: str
    task_id: str
    action_type: str
    status: ActionStatus
    policy_state: PolicyState
    reasons: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ActionOutcome(BaseModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    audit: CivicAuditRecord
    command_result: CommandResult | None = None


class CivicConnectionStatus(BaseModel):
    configured: bool = False
    connected: bool = False
    available: bool = False
    required: bool = False
    status: Literal["unconfigured", "connected", "degraded", "unavailable"] = "unconfigured"
    base_url: str | None = None
    toolkit_id: str | None = None
    required_tools: list[str] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list)
    discovered_tool_count: int = 0
    last_checked_at: datetime | None = None
    message: str | None = None
    errors: list[str] = Field(default_factory=list)


class CivicCapability(BaseModel):
    capability_id: str
    display_name: str
    available: bool = True
    read_only: bool = True
    tools: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillHealth(BaseModel):
    skill_id: str
    status: Literal["inactive", "available", "degraded", "blocked"] = "inactive"
    available: bool = False
    required_tools: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    reason: str | None = None
    last_checked_at: datetime | None = None


class GovernedBidEnvelope(BaseModel):
    envelope_id: str
    mission_id: str
    task_id: str
    bid_id: str
    status: Literal["approved", "blocked", "revoked", "expired"] = "approved"
    allowed_skills: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    toolkit_id: str | None = None
    profile_id: str | None = None
    read_only: bool = True
    read_write_scope: str = "read_only"
    runtime_budget_seconds: float | None = None
    token_budget: int | None = None
    constraints: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    policy_state: PolicyState = PolicyState.CLEAR
    policy_decision: str = "allow"
    reasoning: list[str] = Field(default_factory=list)
    audit_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class GovernedActionRecord(BaseModel):
    action_id: str
    mission_id: str
    task_id: str | None = None
    bid_id: str | None = None
    envelope_id: str | None = None
    skill_id: str | None = None
    action_type: str
    tool_name: str | None = None
    status: Literal[
        "preflight_allowed",
        "preflight_blocked",
        "executed",
        "failed",
        "revoked",
        "unavailable",
    ] = "preflight_allowed"
    allowed: bool = True
    audit_id: str | None = None
    policy_state: PolicyState = PolicyState.CLEAR
    reasoning: list[str] = Field(default_factory=list)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


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
    civic_audit_id: str | None = None
    governance_state: PolicyState = PolicyState.CLEAR
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def action(self) -> str:
        return self.action_type


class ValidationReport(BaseModel):
    task_id: str
    passed: bool
    command_results: list[CommandResult] = Field(default_factory=list)
    baseline_command_results: list[CommandResult] = Field(default_factory=list)
    file_churn: int = 0
    changed_files: list[str] = Field(default_factory=list)
    api_guard_passed: bool = True
    benchmark_delta: float | None = None
    notes: list[str] = Field(default_factory=list)
    policy_conformance: bool = True
    validator_deltas: list[str] = Field(default_factory=list)

    @property
    def details(self) -> list[str]:
        return self.notes


class FailureContext(BaseModel):
    task_id: str
    failure_type: str
    details: str
    diff_summary: str
    validator_deltas: list[str] = Field(default_factory=list)
    recommended_recovery_scope: str
    strategy_family: str | None = None
    attempted_file_scope: list[str] = Field(default_factory=list)
    rollout_evidence: list[str] = Field(default_factory=list)
    civic_action_history: list[str] = Field(default_factory=list)
    rollback_result: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AcceptedCheckpoint(BaseModel):
    checkpoint_id: str
    label: str
    commit_sha: str
    created_at: datetime = Field(default_factory=utc_now)
    summary: str | None = None
    diff_summary: str = ""
    diff_patch: str = ""
    affected_files: list[str] = Field(default_factory=list)
    validator_results: list[str] = Field(default_factory=list)
    strategy_family: str | None = None
    civic_audit_ids: list[str] = Field(default_factory=list)
    rollback_pointer: str | None = None


class MissionStateCheckpoint(BaseModel):
    checkpoint_id: str
    mission_id: str
    label: str
    active_phase: ActivePhase = ActivePhase.IDLE
    active_task_id: str | None = None
    active_bid_round: int = 0
    recovery_round: int = 0
    winner_bid_id: str | None = None
    standby_bid_id: str | None = None
    accepted_checkpoint_id: str | None = None
    run_state: RunState = RunState.IDLE
    policy_state: PolicyState = PolicyState.CLEAR
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class RepoStateCheckpoint(BaseModel):
    checkpoint_id: str
    mission_id: str
    label: str
    checkpoint_kind: Literal["accepted", "rollback", "worktree"]
    branch_name: str | None = None
    commit_sha: str | None = None
    accepted: bool = False
    diff_summary: str = ""
    diff_patch: str = ""
    affected_files: list[str] = Field(default_factory=list)
    worktree_state: dict[str, Any] = Field(default_factory=dict)
    rollback_pointer: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ModelInvocation(BaseModel):
    invocation_id: str
    mission_id: str
    task_id: str | None = None
    bid_id: str | None = None
    provider: str
    lane: str
    model_id: str | None = None
    invocation_kind: Literal["bid_generation", "proposal_generation", "simulation", "mission_planning"]
    status: Literal["started", "completed", "failed"]
    generation_mode: GenerationMode = GenerationMode.PROVIDER_MODEL
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    prompt_preview: str | None = None
    response_preview: str | None = None
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int] | None = None
    cost_usage: dict[str, float] | None = None
    usage_unavailable_reason: str | None = None
    error: str | None = None


class BiddingState(BaseModel):
    generation_mode: GenerationMode = GenerationMode.PROVIDER_MODEL
    require_provider_backed_bids: bool = True
    allow_degraded_fallback: bool = False
    degraded: bool = False
    warning: str | None = None
    architecture_violation: str | None = None
    total_provider_invocations: int = 0
    round_provider_invocations: int = 0
    active_provider_bids: int = 0
    active_fallback_bids: int = 0


class TraceEntry(BaseModel):
    trace_type: str
    title: str
    message: str
    status: str = "info"
    task_id: str | None = None
    bid_id: str | None = None
    provider: str | None = None
    lane: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


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
    governance: GovernanceSnapshot = Field(default_factory=GovernanceSnapshot)
    bidding_state: BiddingState = Field(default_factory=BiddingState)
    active_phase: ActivePhase = ActivePhase.IDLE
    active_bid_round: int = 0
    active_bids: list[Bid] = Field(default_factory=list)
    simulation_summary: SimulationSummary | None = None
    winner_bid_id: str | None = None
    standby_bid_id: str | None = None
    repo_snapshot: RepoSnapshot | None = None
    civic_connection: CivicConnectionStatus = Field(default_factory=CivicConnectionStatus)
    civic_capabilities: list[CivicCapability] = Field(default_factory=list)
    available_skills: list[str] = Field(default_factory=list)
    skill_health: dict[str, SkillHealth] = Field(default_factory=dict)
    skill_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    governed_bid_envelopes: dict[str, GovernedBidEnvelope] = Field(default_factory=dict)
    recent_civic_actions: list[GovernedActionRecord] = Field(default_factory=list)
    tasks: list[TaskNode] = Field(default_factory=list)
    active_task_id: str | None = None
    current_bid: Bid | None = None
    standby_bid: Bid | None = None
    mission_landscape: list[str] = Field(default_factory=list)
    strategy_round: int = 0
    failure_context: FailureContext | None = None
    validation_report: ValidationReport | None = None
    accepted_checkpoint: AcceptedCheckpoint | None = None
    last_civic_audit: CivicAuditRecord | None = None
    recovery_round: int = 0
    policy_collisions: int = 0
    runtime_seconds: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usage: dict[str, float] = Field(default_factory=dict)
    decision_history: list[str] = Field(default_factory=list)
    latest_diff_summary: str = ""
    worktree_state: dict[str, Any] = Field(default_factory=dict)
    no_valid_contenders: bool = False
    outcome: MissionOutcome | None = None

    @model_validator(mode="after")
    def sync_summary(self) -> ArbiterState:
        self.summary.current_risk_score = self.governance.current_risk_score
        self.summary.stop_reason = self.governance.stop_reason
        self.summary.token_usage = dict(self.token_usage)
        self.summary.cost_usage = dict(self.cost_usage)
        self.summary.decision_history = list(self.decision_history)
        self.summary.runtime_seconds = self.runtime_seconds
        self.summary.outcome = self.outcome
        self.summary.bidding_state = self.bidding_state.model_dump(mode="json")
        if self.last_civic_audit and self.last_civic_audit.audit_id not in self.summary.civic_audit_ids:
            self.summary.civic_audit_ids.append(self.last_civic_audit.audit_id)
        return self
