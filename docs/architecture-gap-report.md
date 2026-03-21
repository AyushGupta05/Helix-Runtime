# Architecture Gap Report

## What Already Matched

- Arbiter already operated as a mission runner over real repositories with isolated worktrees and managed branches.
- The runtime already persisted missions, tasks, bids, execution steps, validations, failures, and events to SQLite plus JSONL.
- Competitive bidding, simulation rollouts, validation commands, and accepted checkpoints already existed in partial form.

## Structural Gaps Found

- Mission state persistence was not authoritative enough for resume or operator rendering. Winner, standby, phase, and control state were reconstructable only indirectly and not checkpointed as first-class runtime state.
- Repo-state checkpoints were missing as a distinct persisted concept, so rollback anchors, accepted branch state, and reversible repo boundaries were weaker than the intended architecture.
- Recovery was too mechanical. It could roll back and sometimes promote standby, but it did not score recovery choices with enough evidence from the failure context.
- Task selection was too shallow. Ready work defaulted toward queue order instead of governance priority.
- Validation acceptance was not baseline-aware, which caused weak no-regression semantics and made existing repo failures harder to govern correctly.
- Resume safety around worktrees was weak. Existing worktrees could be reused too optimistically, and paused missions could be incorrectly finalized as orphaned.
- Provider-backed bidding provenance needed stronger end-to-end durability and operator visibility, especially around degraded fallback and invocation accounting.
- The operator UI lagged the persisted runtime truth. The live feed, bid board, and repo state panels did not fully reflect phase progression, accepted checkpoint lineage, winner/standby continuity, or degraded bidding state.

## Cosmetic Or Fidelity Gaps

- The event strip was too chat-like for an operator dashboard.
- A clean managed branch after acceptance was rendered like “no changes yet,” which hid the real output of successful missions.
- Bedrock references and routing were inconsistent with the desired OpenAI/Anthropic-only model surface.

## Fixes Implemented In This Pass

- Added durable `MissionStateCheckpoint` and `RepoStateCheckpoint` persistence, migration, API exposure, and mission-view rendering support.
- Made mission resume hydrate authoritative checkpoint state, restore winner and standby, and recover repo context more safely.
- Hardened isolated worktree reuse and branch reattachment.
- Reworked recovery planning to choose between safe stop, standby promotion, or evidence-aware rebidding based on rollback result, failed family, scope overlap, and risk.
- Added governance-based task prioritization instead of pure FIFO ready-task selection.
- Made validation baseline-aware so accepted results are tied to no-regression evidence, while still enforcing stronger gates for bugfix and performance claims.
- Recorded richer failure context and rollback outcomes.
- Removed Bedrock from runtime configuration and kept provider routing to OpenAI and Anthropic lanes only.
- Updated default lane models to lower-token OpenAI and Anthropic coding-oriented choices.
- Repaired the mission-control UI so it shows phase progression, mission timeline, bid provenance, degraded bidding banners, winner and standby continuity, and accepted branch lineage grounded in persisted runtime state.
- Added and updated tests for mission checkpoints, provenance, stream reduction, timeline rendering, and browser mission-control behavior.

## Out Of Scope For This Pass

- Real external-provider benchmarking with live OpenAI or Anthropic credentials.
- A deeper search policy beyond the current bounded shallow rollout approach.
- More sophisticated learning or analytics over historical missions.
- Additional task families beyond the current bugfix, refactor, and benchmark-gated performance flow.
