from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MissionCreateRequest(BaseModel):
    repo: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    requested_skills: list[str] = Field(default_factory=list)
    max_runtime: int = 10
    benchmark_requirement: str | None = None
    protected_paths: list[str] = Field(default_factory=list)
    public_api_surface: list[str] = Field(default_factory=list)


class MissionControlResponse(BaseModel):
    mission_id: str
    run_state: str
    outcome: str | None = None
    branch_name: str | None = None
    repo_path: str | None = None


class TaskView(BaseModel):
    task_id: str
    title: str
    task_type: str
    status: str
    requirement_level: str
    dependencies: list[str] = Field(default_factory=list)


class BidView(BaseModel):
    bid_id: str
    task_id: str
    role: str
    provider: str | None = None
    lane: str | None = None
    model_id: str | None = None
    invocation_id: str | None = None
    invocation_kind: str | None = None
    generation_mode: str = "deterministic_fallback"
    strategy_family: str
    strategy_summary: str
    score: float | None = None
    confidence: float | None = None
    risk: float
    cost: float
    estimated_runtime_seconds: float
    touched_files: list[str] = Field(default_factory=list)
    validator_plan: list[str] = Field(default_factory=list)
    rollback_plan: str | None = None
    rollout_level: str | None = None
    search_summary: str | None = None
    policy_state: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    optional_skills: list[str] = Field(default_factory=list)
    governed_action_plan: list[str] = Field(default_factory=list)
    external_evidence_plan: list[str] = Field(default_factory=list)
    capability_reliance_score: float = 0.0
    policy_friction_score: float = 0.0
    revocation_risk_score: float = 0.0
    active_envelope_id: str | None = None
    token_usage: dict[str, int] | None = None
    cost_usage: dict[str, float] | None = None
    usage_unavailable_reason: str | None = None
    rejection_reason: str | None = None
    selected: bool = False
    standby: bool = False


class TimelineEventView(BaseModel):
    id: int
    event_type: str
    created_at: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MissionView(BaseModel):
    mission_id: str
    repo_path: str
    objective: str
    created_at: str
    updated_at: str
    runtime_seconds: float = 0.0
    status: str | None = None
    outcome: str | None = None
    run_state: str
    active_phase: str
    active_task_id: str | None = None
    active_bid_round: int
    simulation_round: int = 0
    recovery_round: int = 0
    branch_name: str | None = None
    head_commit: str | None = None
    latest_event_id: int = 0
    latest_diff_summary: str = ""
    winner_bid_id: str | None = None
    standby_bid_id: str | None = None
    decision_history: list[str] = Field(default_factory=list)
    failed_attempt_history: list[str] = Field(default_factory=list)
    tasks: list[TaskView] = Field(default_factory=list)
    active_task: dict[str, Any] | None = None
    bids: list[BidView] = Field(default_factory=list)
    events: list[TimelineEventView] = Field(default_factory=list)
    validation_report: dict[str, Any] | None = None
    failure_context: dict[str, Any] | None = None
    simulation_summary: dict[str, Any] | None = None
    guardrail_state: dict[str, Any] = Field(default_factory=dict)
    recovery_state: dict[str, Any] = Field(default_factory=dict)
    stop_state: dict[str, Any] = Field(default_factory=dict)
    bidding_state: dict[str, Any] = Field(default_factory=dict)
    civic_audit_summary: dict[str, Any] = Field(default_factory=dict)
    civic_connection: dict[str, Any] = Field(default_factory=dict)
    civic_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    available_skills: list[str] = Field(default_factory=list)
    skill_health: dict[str, Any] = Field(default_factory=dict)
    skill_outputs: dict[str, Any] = Field(default_factory=dict)
    mission_meta: dict[str, Any] = Field(default_factory=dict)
    history_metrics: dict[str, Any] = Field(default_factory=dict)
    repo_insights: dict[str, Any] = Field(default_factory=dict)
    outcome_summary: dict[str, Any] = Field(default_factory=dict)
    civic_activity: dict[str, Any] = Field(default_factory=dict)
    activity_summary: dict[str, Any] = Field(default_factory=dict)
    provider_market_summary: dict[str, Any] = Field(default_factory=dict)
    usage_summary: dict[str, Any] = Field(default_factory=dict)
    worktree_state: dict[str, Any] = Field(default_factory=dict)
    accepted_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    mission_state_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    repo_state_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    governed_bid_envelopes: list[dict[str, Any]] = Field(default_factory=list)
    recent_civic_actions: list[dict[str, Any]] = Field(default_factory=list)
    mission_output: dict[str, Any] = Field(default_factory=dict)
    execution_steps: list[dict[str, Any]] = Field(default_factory=list)
    recent_trace: list[dict[str, Any]] = Field(default_factory=list)


class MissionHistoryEntry(BaseModel):
    mission_id: str
    repo_path: str
    objective: str
    created_at: str
    updated_at: str
    runtime_seconds: float = 0.0
    run_state: str
    status: str
    outcome: str | None = None
    branch_name: str | None = None
    total_tokens: int = 0
    total_cost: float = 0.0
    checkpoint_count: int = 0
    failure_count: int = 0
    changed_file_count: int = 0
    recovery_count: int = 0
    validator_status: str | None = None
