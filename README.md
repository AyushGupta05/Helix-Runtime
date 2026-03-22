# Helix Runtime

Helix Runtime is a local-first autonomous software execution runtime designed to make AI agents more reliable before they act and safer while they act.

Most agent systems take one plan, run it blindly, and if it fails, a human has to step in and recover the workflow. Helix Runtime is built to handle that differently. Instead of committing to a single plan, it generates multiple competing strategies for each phase of work, evaluates them with Monte Carlo simulation, and selects the strategy most likely to succeed before execution.

Execution happens inside isolated git worktrees and is validated against the repository baseline before changes are accepted. If something fails, Helix does not just stop. It can roll back to the last accepted checkpoint, promote a standby strategy, or reopen the bidding market and try again.

Civic is integrated as the governance and trust layer throughout the runtime. It governs higher-trust actions like external context access, GitHub context, and pull request publishing, while also enforcing guardrails and keeping actions auditable.

In short, Helix Runtime is an autonomous decision and execution layer that helps agent systems choose better, fail safer, and recover on their own.

## Local Setup

### Requirements

- Python 3.13
- Node.js 20+
- npm
- Git

### Install

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
cd frontend
npm install
cd ..
```

### Environment

Create `.env` in the project root using `.env.example` as a starting point:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
CIVIC_URL=
CIVIC_TOKEN=
```

The app can still run without every provider configured, but the full bidding and Civic-governed workflow will not be available.

### Run

Start the backend from the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m arbiter serve --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```powershell
cd frontend
npm run dev
```

Then open `http://127.0.0.1:5173`.

## Architecture

### High-level system design

```text
+---------------------------------------------------------------+
| Frontend                                                      |
| React + Vite                                                  |
|                                                               |
| - Prompt / mission launcher                                   |
| - Live bidding board                                          |
| - Simulation and intelligence views                           |
| - Outcome and checkpoint views                                |
| - SSE-driven mission updates                                  |
+---------------------------------------------------------------+
                           |
                           | HTTP / SSE
                           v
+---------------------------------------------------------------+
| Backend Runtime                                               |
| FastAPI + LangGraph                                           |
|                                                               |
| - Repo scan and context building                              |
| - Task decomposition                                          |
| - Multi-model bidding                                         |
| - Monte Carlo simulation                                      |
| - Strategy selection                                          |
| - Isolated worktree execution                                 |
| - Validation, rollback, standby promotion, rebidding          |
| - Mission persistence and replay                              |
+---------------------------------------------------------------+
                           |
                           v
+---------------------------------------------------------------+
| Model Providers                                               |
| OpenAI + Anthropic                                            |
|                                                               |
| - Competing strategy generation                               |
| - Lightweight lane-specific bidding                           |
| - Proposal generation and reasoning                           |
+---------------------------------------------------------------+
                           |
                           v
+---------------------------------------------------------------+
| Civic Governance Layer                                        |
|                                                               |
| - Capability preflight                                        |
| - Governed external context                                   |
| - GitHub context and PR publishing                            |
| - Tool guardrails and audit trail                             |
+---------------------------------------------------------------+
                           |
                           v
+---------------------------------------------------------------+
| Target Repository                                             |
|                                                               |
| - Isolated git worktrees                                      |
| - Baseline-aware validation                                   |
| - Accepted checkpoints                                        |
| - Autonomous recovery on failure                              |
+---------------------------------------------------------------+
```

### Runtime flow

1. The user enters a prompt.
2. Helix scans the repository and builds context.
3. The objective is broken into subtasks.
4. Multiple models generate competing bids for the current phase.
5. Helix runs Monte Carlo simulation to estimate which strategy is most likely to succeed.
6. Civic governs the allowed capabilities and actions.
7. The winning strategy executes inside an isolated git worktree.
8. The result is validated against the repo baseline.
9. If validation passes, Helix accepts the checkpoint.
10. If validation fails, Helix can roll back, promote a standby strategy, or rebid.
