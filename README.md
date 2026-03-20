# Arbiter V1

Arbiter is a local-first, resumable autonomous mission runner for Python repos and conventional single-package TS/JS repos. It executes missions inside isolated git worktrees, runs a bounded market of specialist contenders, validates aggressively, and recovers through rollback, standby promotion, or rebidding.

## Current V1 shape

- LangGraph-backed mission loop
- SQLite mission state + JSONL event stream
- Separate mission-state and repo-state checkpoints
- Bedrock-first model routing by lane
- Local mediated tools for repo work
- Civic as the privileged external-tool boundary
- CLI-first operator surface

## CLI

```powershell
.\.venv\Scripts\python.exe -m arbiter mission start --repo C:\path\to\repo --objective "Fix failing tests"
.\.venv\Scripts\python.exe -m arbiter mission status <mission-id>
.\.venv\Scripts\python.exe -m arbiter mission events <mission-id> --follow
.\.venv\Scripts\python.exe -m arbiter mission resume <mission-id>
```

## Environment

Copy `.env.example` to `.env` and fill in the values you want to use. `.env.example` is intentionally placeholder-only.

## Outputs

Each mission creates a runtime folder under the target repo's `.arbiter/` directory containing:

- `mission.db`
- `events.jsonl`
- cached reports
- metadata
- replay payloads
- checkpoint records

Successful missions create validated commits on Arbiter-managed `codex/` branches in isolated worktrees.

