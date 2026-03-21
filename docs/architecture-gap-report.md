# Architecture Status

## Integrated In This Pass

- Arbiter now remains the decision-maker while LangGraph owns lifecycle progression through `collect -> decompose -> select_task -> market -> simulate -> select -> execute -> validate -> recover -> finalize`.
- The runtime no longer depends on a hand-rolled phase loop. Mission execution is compiled as a LangGraph workflow and persisted with SQLite-backed LangGraph checkpoints.
- Decomposition is no longer heuristic-only. Arbiter now evaluates provider-backed mission-plan proposals from the `triage` lane and selects between those plans and the deterministic fallback graph.
- Competitive search is no longer a light reward tweak. The simulation phase now runs bounded Monte Carlo-style search over each bid using rollout evidence, validator alignment, rollback probability, scope-drift probability, runtime pressure, and provider reliability.
- Provider-backed planning, bidding, and proposal generation all persist invocation provenance, usage metadata, and failure records through the same durable mission store.

## What This Means Architecturally

- Arbiter decides what should happen:
  - selects the task graph
  - scores and filters the bid frontier
  - runs the search math
  - chooses winner and standby
  - governs validation and recovery

- LangGraph manages the workflow lifecycle:
  - phase routing
  - resumable execution
  - persistent checkpointing
  - pause/resume continuity

- Codex/Claude-class models execute bounded work units:
  - propose task graphs
  - propose bids
  - propose concrete file edits
  - never become the unbounded runtime substrate

## Deliberate Bounds That Still Remain

- Search is deeper and quantitative now, but it is still bounded search rather than a large branching planner over arbitrary edit trees.
- Mission planning is provider-backed, but Arbiter still constrains the task vocabulary and normalizes plans into a safe internal contract.
- The runtime still supports one active in-process mission at a time in V1.
