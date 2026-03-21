# Arbiter Mission Control

Arbiter is a local-first, resumable autonomous mission runner for Python repos and conventional single-package TS/JS repos. Arbiter decides what should happen, LangGraph owns the workflow lifecycle, and Codex/Claude-class models execute bounded work units. The runtime breaks a coding objective into dependency-aware tasks, runs a provider-backed strategy market, simulates future execution paths with bounded Monte Carlo search, executes the strongest path in an isolated worktree, validates every material result, and recovers through rollback, standby promotion, or rebidding until it lands a safe branch-ready change.

## What is in this repo

- LangGraph-managed mission runtime with persistent checkpoints and resumable phase execution
- Arbiter mission planning that arbitrates between heuristic and provider-backed task graphs
- autonomous mission engine with task decomposition, market rounds, bounded Monte Carlo search, validation, and recovery
- local mission-control API with history, snapshot materialization, controls, and SSE streaming
- React + Vite operator dashboard for live bidding, execution, validation, and recovery
- SQLite mission state + LangGraph checkpoints + JSONL event stream + separate repo checkpoints
- explicit OpenAI + Anthropic planning, bidding, and proposal lanes with durable invocation provenance
- Civic integration path for privileged external tools

## Run the operator UI

From the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m arbiter serve --host 127.0.0.1 --port 8000
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

For frontend development with Vite hot reload:

```powershell
cd frontend
npm install
npm run dev
```

The Vite app proxies `/api` to the local Arbiter server on port `8000`.

## CLI

```powershell
python -m arbiter mission start --repo C:\path\to\repo --objective "Fix failing tests"
python -m arbiter mission status <mission-id> --repo C:\path\to\repo
python -m arbiter mission events <mission-id> --repo C:\path\to\repo --follow
python -m arbiter mission resume <mission-id> --repo C:\path\to\repo
python -m arbiter serve --host 127.0.0.1 --port 8000
```

## Environment

Copy `.env.example` to `.env` and fill in only the providers you want to use. `.env.example` is intentionally placeholder-only.

## Mission outputs

Each mission creates a runtime folder under the target repo's `.arbiter/` directory containing:

- `state.db`
- `events.jsonl`
- `metadata.json`
- LangGraph checkpoint state embedded in `state.db`
- cached reports
- replay payloads
- mission-state checkpoints
- repo-state checkpoints

Successful missions create validated commits on Arbiter-managed `codex/` branches in isolated worktrees, with checkpointed diff evidence, search diagnostics, and provider provenance available through the API and operator UI.
