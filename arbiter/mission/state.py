from __future__ import annotations

from arbiter.core.contracts import ArbiterState, MissionSpec, MissionSummary


def initialize_state(spec: MissionSpec) -> ArbiterState:
    return ArbiterState(
        mission=spec,
        summary=MissionSummary(mission_id=spec.mission_id),
    )

