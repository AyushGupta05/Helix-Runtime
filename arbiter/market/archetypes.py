from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchetypeDefinition:
    role: str
    risk_bias: float
    diff_bias: float
    validator_bias: float
    rollback_bias: float
    default_lane: str


ARCHETYPES = [
    ArchetypeDefinition("Speed", risk_bias=0.25, diff_bias=0.2, validator_bias=0.35, rollback_bias=0.3, default_lane="bid_fast"),
    ArchetypeDefinition("Safe", risk_bias=0.65, diff_bias=0.15, validator_bias=0.8, rollback_bias=0.9, default_lane="bid_deep"),
    ArchetypeDefinition("Quality", risk_bias=0.45, diff_bias=0.6, validator_bias=0.7, rollback_bias=0.5, default_lane="bid_deep"),
    ArchetypeDefinition("Test", risk_bias=0.3, diff_bias=0.25, validator_bias=0.95, rollback_bias=0.6, default_lane="test_gen"),
    ArchetypeDefinition("Performance", risk_bias=0.4, diff_bias=0.5, validator_bias=0.75, rollback_bias=0.55, default_lane="perf_reason"),
]

