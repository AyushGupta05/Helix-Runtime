from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MissionCreateRequest(BaseModel):
    repo: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    max_runtime: int = 10
    benchmark_requirement: str | None = None
    protected_paths: list[str] = Field(default_factory=list)
    public_api_surface: list[str] = Field(default_factory=list)


class MissionControlResponse(BaseModel):
    mission_id: str
    run_state: str
    outcome: str | None = None
    branch_name: str | None = None


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
    strategy_family: str
    strategy_summary: str
    score: float | None = None
    risk: float
    cost: float
    estimated_runtime_seconds: float
    touched_files: list[str] = Field(default_factory=list)
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
    outcome: str | None = None
    run_state: str
    active_phase: str
    active_bid_round: int
    branch_name: str | None = None
    head_commit: str | None = None
    latest_event_id: int = 0
    latest_diff_summary: str = ""
    winner_bid_id: str | None = None
    standby_bid_id: str | None = None
    decision_history: list[str] = Field(default_factory=list)
    failed_attempt_history: list[str] = Field(default_factory=list)
    tasks: list[TaskView] = Field(default_factory=list)
    bids: list[BidView] = Field(default_factory=list)
    events: list[TimelineEventView] = Field(default_factory=list)
    validation_report: dict[str, Any] | None = None


class MissionHistoryEntry(BaseModel):
    mission_id: str
    repo_path: str
    objective: str
    created_at: str
    updated_at: str
    run_state: str
    status: str
    outcome: str | None = None
    branch_name: str | None = None
