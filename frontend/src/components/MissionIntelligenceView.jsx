import { useEffect, useMemo, useState } from "react";

import StatusBadge from "./StatusBadge";
import {
  formatCurrency,
  formatInteger,
  formatNumber,
  humanizeEventType,
  relativeTime,
  shortCommit,
  summarizeProvider
} from "../lib/format";

const SECTIONS = [
  { id: "simulation", label: "Monte Carlo" },
  { id: "validation", label: "Validation" },
  { id: "diff", label: "Diff Explorer" },
  { id: "civic", label: "Civic Activity" },
  { id: "history", label: "Mission History" }
];

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? repoPath ?? "repo";
}

function validatorRows(report, historyMetrics, runState) {
  if (report?.command_results?.length) {
    return report.command_results.map((result, index) => ({
      id: `${result.command?.join(" ") ?? result.command}-${index}`,
      label: result.command?.join(" ") ?? `validator ${index + 1}`,
      status: Number(result.exit_code) === 0 ? "passed" : "failed",
      summary: result.summary ?? result.output_summary ?? result.stderr ?? result.stdout ?? "No output captured."
    }));
  }

  const historyCommands = historyMetrics?.validation?.commands ?? [];
  if (historyCommands.length) {
    return historyCommands.map((result, index) => ({
      id: `${result.command}-${index}`,
      label: result.command ?? `validator ${index + 1}`,
      status: result.status === "passed" ? "passed" : "failed",
      summary: result.stderr_excerpt || result.stdout_excerpt || "No output captured."
    }));
  }

  if (!report && !historyMetrics?.validation) {
    return [];
  }

  if (runState !== "finalized") {
    return [
      {
        id: "default",
        label: "Mission validation",
        status: "pending",
        summary: "Validation has not finished emitting detailed command output yet."
      }
    ];
  }

  return [
    {
      id: "default",
      label: "Mission validation",
      status: Boolean(report?.passed ?? historyMetrics?.validation?.passed) ? "passed" : "failed",
      summary:
        report?.notes?.join(" ") ||
        historyMetrics?.validation?.notes?.join(" ") ||
        "Validation completed without detailed command output."
    }
  ];
}

function changedFilesFor(mission, diffState) {
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  return worktree.changed_files?.length
    ? worktree.changed_files
    : mission.outcome_summary?.changed_files?.length
      ? mission.outcome_summary.changed_files
      : mission.validation_report?.changed_files ?? [];
}

function publicApiSurfaceFor(mission) {
  return mission.repo_insights?.public_api_surface ?? [];
}

function simulationEntries(mission, trace) {
  if (mission.simulation_activity?.length) {
    return mission.simulation_activity;
  }
  return [...(mission.events ?? []), ...(trace ?? [])]
    .filter((entry) => String(entry.event_type ?? entry.trace_type ?? "").startsWith("simulation."))
    .sort((left, right) => new Date(left.created_at ?? 0).getTime() - new Date(right.created_at ?? 0).getTime())
    .slice(-20);
}

function simulationBidRows(mission) {
  return (mission.bids ?? [])
    .filter((bid) => bid.task_id === mission.active_task_id)
    .sort((left, right) => Number(right.score ?? -1) - Number(left.score ?? -1));
}

function githubAuthUrl(mission) {
  const actions = [...(mission.recent_civic_actions ?? [])].reverse();
  const challenge =
    actions.find((entry) => entry?.output_payload?.authorization_url) ??
    actions.find((entry) => entry?.payload?.output_payload?.authorization_url) ??
    null;
  return (
    challenge?.output_payload?.authorization_url ??
    challenge?.payload?.output_payload?.authorization_url ??
    null
  );
}

function SimulationSection({ mission, trace }) {
  const summary = mission.simulation_summary ?? {};
  const entries = simulationEntries(mission, trace).reverse();
  const bids = simulationBidRows(mission);

  const cards = [
    {
      label: "Search mode",
      value: summary.search_mode ?? "bounded_monte_carlo",
      detail: `${formatInteger(summary.total_bids ?? bids.length)} contenders`
    },
    {
      label: "Samples per bid",
      value: formatInteger(summary.monte_carlo_samples ?? 0),
      detail: `${formatInteger(summary.paper_rollouts ?? 0)} paper | ${formatInteger(summary.partial_rollouts ?? 0)} partial | ${formatInteger(summary.sandbox_rollouts ?? 0)} sandbox`
    },
    {
      label: "Frontier gap",
      value: formatNumber(summary.frontier_gap ?? 0, 3),
      detail: `Budget ${formatInteger(summary.budget_used ?? 0)}`
    },
    {
      label: "Rollback safety",
      value: formatNumber(summary.rollback_safety ?? 0, 2),
      detail: `Validator stability ${formatNumber(summary.validator_stability ?? 0, 2)}`
    },
    {
      label: "Capability availability",
      value: formatNumber(summary.capability_availability ?? 1, 2),
      detail: `Policy friction ${formatNumber(summary.policy_friction ?? 0, 2)}`
    },
    {
      label: "Evidence quality",
      value: formatNumber(summary.evidence_quality ?? 0, 2),
      detail: `Freshness ${formatNumber(summary.freshness_score ?? 1, 2)}`
    }
  ];

  return (
    <div className="intelligence-section-grid">
      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Monte Carlo state</h2>
          <p>The simulation engine is shown as a live decision system instead of a post-hoc score dump.</p>
        </div>
        <div className="intelligence-card-grid">
          {cards.map((card) => (
            <article key={card.label} className="insight-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
        {summary.summary ? <p className="mission-intelligence-summary">{summary.summary}</p> : null}
      </section>

      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Live simulation tape</h2>
          <p>Rollouts, scoring passes, and search updates stream here as the market is evaluated.</p>
        </div>
        <div className="ledger-list">
          {entries.length ? (
            entries.map((entry) => {
              const payload = entry.payload ?? entry;
              return (
                <article key={entry.id ?? `${entry.event_type}-${entry.created_at}`} className="ledger-row">
                  <div>
                    <strong>{humanizeEventType(entry.event_type ?? entry.trace_type ?? "simulation")}</strong>
                    <p>{entry.message ?? payload.search_summary ?? "Monte Carlo update recorded."}</p>
                  </div>
                  <div className="ledger-meta">
                    {payload.rollout ? <span>{payload.rollout}</span> : null}
                    {payload.bid_id ? <span>{String(payload.bid_id).slice(0, 8)}</span> : null}
                    <span>{entry.created_at ? relativeTime(entry.created_at) : "now"}</span>
                  </div>
                </article>
              );
            })
          ) : (
            <div className="section-empty">Monte Carlo activity will appear here once simulation starts.</div>
          )}
        </div>
      </section>

      <section className="panel intelligence-panel intelligence-panel-full">
        <div className="section-title">
          <h2>Candidate score frontier</h2>
          <p>Each contender keeps its policy, runtime, and simulation profile in one place.</p>
        </div>
        <div className="simulation-bid-grid">
          {bids.length ? (
            bids.map((bid) => {
              const diagnostics = bid.search_diagnostics ?? {};
              return (
                <article key={bid.bid_id} className="simulation-bid-card">
                  <div className="simulation-bid-head">
                    <div>
                      <strong>{bid.role ?? bid.strategy_family}</strong>
                      <p>{summarizeProvider(bid.provider ?? "system")}</p>
                    </div>
                    <StatusBadge value={bid.bid_id === mission.winner_bid_id ? "winner" : bid.status ?? "generated"} quiet />
                  </div>
                  <div className="simulation-bid-stats">
                    <span>Score {formatNumber(bid.score)}</span>
                    <span>Search {formatNumber(bid.search_score)}</span>
                    <span>Runtime {formatNumber(bid.estimated_runtime_seconds ?? 0, 0)}s</span>
                    <span>Samples {formatInteger(diagnostics.sample_count ?? 0)}</span>
                    <span>Success {formatNumber(diagnostics.success_rate ?? 0, 2)}</span>
                    <span>Rollback {formatNumber(diagnostics.rollback_rate ?? 0, 2)}</span>
                    <span>Capability {formatNumber(diagnostics.capability_availability_probability ?? 1, 2)}</span>
                    <span>Policy {formatNumber(diagnostics.policy_friction_cost ?? 0, 2)}</span>
                  </div>
                  <p>{bid.search_summary ?? bid.mission_rationale ?? bid.strategy_summary}</p>
                </article>
              );
            })
          ) : (
            <div className="section-empty">Candidate diagnostics will appear here once bidding begins.</div>
          )}
        </div>
      </section>
    </div>
  );
}

function ValidationSection({ mission }) {
  const rows = validatorRows(mission.validation_report, mission.history_metrics, mission.run_state);

  return (
    <section className="panel intelligence-panel">
      <div className="section-title">
        <h2>Validation reports</h2>
        <p>Checks are grouped into readable outcome cards first, with raw summaries tucked underneath.</p>
      </div>
      <div className="validation-report-grid">
        {rows.length ? (
          rows.map((row) => (
            <article key={row.id} className="validation-report-card">
              <div className="validation-report-head">
                <strong>{row.label}</strong>
                <StatusBadge value={row.status === "passed" ? "success" : row.status === "failed" ? "failed" : "pending"} quiet />
              </div>
              <p>{row.summary}</p>
            </article>
          ))
        ) : (
          <div className="section-empty">Validation has not emitted report data yet.</div>
        )}
      </div>
    </section>
  );
}

function DiffSection({ mission, diffState }) {
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  const changedFiles = changedFilesFor(mission, diffState);
  const patch = worktree.diff_patch || worktree.diff_stat || mission.latest_diff_summary || "No diff captured yet.";
  const publicApiSurface = publicApiSurfaceFor(mission);

  return (
    <div className="intelligence-section-grid">
      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Changed files</h2>
          <p>Review-ready file grouping comes first, with the raw patch below only when you need it.</p>
        </div>
        <div className="diff-file-list">
          {changedFiles.length ? (
            changedFiles.map((file) => (
              <article key={file} className="diff-file-row">
                <strong>{file}</strong>
                <span>{publicApiSurface.includes(file) ? "Public API" : "Working change"}</span>
              </article>
            ))
          ) : (
            <div className="section-empty">No changed files recorded yet.</div>
          )}
        </div>
      </section>

      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Patch preview</h2>
          <p>The raw diff remains available, but it sits behind a calmer surface.</p>
        </div>
        <pre className="code-block">{patch}</pre>
      </section>
    </div>
  );
}

function CivicSection({ mission, trace }) {
  const authUrl = githubAuthUrl(mission);
  const connection = mission.civic_connection ?? {};
  const rows = mission.recent_civic_actions?.length
    ? mission.recent_civic_actions
    : (trace ?? []).filter((entry) => String(entry.trace_type ?? "").includes("civic"));
  const skillOutputs = Object.entries(mission.skill_outputs ?? {});

  return (
    <section className="panel intelligence-panel">
      <div className="section-title">
        <h2>Civic capability plane</h2>
        <p>Governed external context, envelopes, and auth state stay together instead of bleeding into every screen.</p>
      </div>

      <div className="intelligence-card-grid">
        <article className="insight-card">
          <span>Connection</span>
          <strong>{String(connection.status ?? "idle").replace(/[_-]/g, " ")}</strong>
          <p>{connection.message ?? "Connection health will appear here after the next Civic check."}</p>
        </article>
        <article className="insight-card">
          <span>Toolkit</span>
          <strong>{connection.toolkit_id ?? "default toolkit"}</strong>
          <p>{connection.last_checked_at ? relativeTime(connection.last_checked_at) : "not checked yet"}</p>
        </article>
        <article className="insight-card">
          <span>Active skills</span>
          <strong>{formatInteger((mission.available_skills ?? []).length)}</strong>
          <p>{(mission.available_skills ?? []).join(" | ") || "No active skills yet"}</p>
        </article>
        <article className="insight-card">
          <span>Governed actions</span>
          <strong>{formatInteger((mission.recent_civic_actions ?? []).length)}</strong>
          <p>{authUrl ? "GitHub auth is waiting on user approval." : "Recent Civic actions are captured below."}</p>
        </article>
      </div>

      {authUrl ? (
        <div className="civic-auth-banner">
          <div>
            <strong>GitHub access needs Civic authorization</strong>
            <p>Approve the GitHub read connection so Arbiter can use governed GitHub context during the mission.</p>
          </div>
          <a className="primary-button" href={authUrl} target="_blank" rel="noreferrer">
            Connect GitHub
          </a>
        </div>
      ) : null}

      <div className="section-title" style={{ marginTop: "1rem" }}>
        <h2>Skill outputs</h2>
        <p>Read-only evidence packets stay grouped by skill so you can see exactly what influenced the market.</p>
      </div>
      <div className="intelligence-card-grid">
        {skillOutputs.length ? (
          skillOutputs.map(([skill, value]) => (
            <article key={skill} className="insight-card">
              <span>{skill}</span>
              <strong>{value?.ci_summary ?? value?.summary ?? value?.detail ?? "Evidence packet captured"}</strong>
              <p>
                {value?.freshness?.checked_at ? relativeTime(value.freshness.checked_at) : "freshness pending"}
                {value?.confidence !== undefined ? ` | confidence ${Math.round(Number(value.confidence) * 100)}%` : ""}
              </p>
            </article>
          ))
        ) : (
          <div className="section-empty">No skill outputs have been captured yet.</div>
        )}
      </div>

      <div className="section-title" style={{ marginTop: "1rem" }}>
        <h2>Governed ledger</h2>
        <p>Envelopes, actions, and policy decisions stay readable as a chronological ledger.</p>
      </div>
      <div className="ledger-list">
        {(mission.governed_bid_envelopes ?? []).map((envelope, index) => (
          <article key={envelope.envelope_id ?? `${envelope.bid_id ?? "envelope"}-${index}`} className="ledger-row">
            <div>
              <strong>{envelope.bid_id ?? "Bid envelope"}</strong>
              <p>{(envelope.reasoning ?? []).join(" ") || "Governed policy contract recorded."}</p>
            </div>
            <div className="ledger-meta">
              <span>{String(envelope.status ?? envelope.policy_decision ?? "governed").replace(/[_-]/g, " ")}</span>
              <span>{envelope.toolkit_id ?? "Civic"}</span>
            </div>
          </article>
        ))}
        {rows.length ? (
          rows.map((row, index) => (
            <article key={row.audit_id ?? row.id ?? index} className="ledger-row">
              <div>
                <strong>{humanizeEventType(row.action_type ?? row.trace_type ?? row.event_type ?? "civic")}</strong>
                <p>
                  {row.reason ??
                    row.message ??
                    row.output_payload?.error ??
                    row.payload?.output_payload?.error ??
                    "Governed Civic action"}
                </p>
              </div>
              <div className="ledger-meta">
                <span>{row.status ?? row.policy_state ?? "captured"}</span>
                <span>{row.created_at ? relativeTime(row.created_at) : "summary"}</span>
              </div>
            </article>
          ))
        ) : !(mission.governed_bid_envelopes ?? []).length ? (
          <div className="section-empty">No Civic-specific audit events have been recorded yet.</div>
        ) : null}
      </div>
    </section>
  );
}

function HistorySection({ history, onSelectMission }) {
  return (
    <section className="panel intelligence-panel">
      <div className="section-title">
        <h2>Mission history</h2>
        <p>Past runs stay close by so comparison and replay do not clutter the primary mission view.</p>
      </div>
      <div className="history-comparison-list">
        {history.length ? (
          history.map((item) => (
            <button key={item.mission_id} className="history-compare-card" onClick={() => onSelectMission(item)} type="button">
              <div className="history-item-head">
                <strong>{item.objective}</strong>
                <StatusBadge value={item.outcome ?? item.run_state} quiet />
              </div>
              <div className="history-item-meta">
                <span>{repoLabel(item.repo_path)}</span>
                <span>{item.branch_name ?? "branch pending"}</span>
                <span>{relativeTime(item.updated_at)}</span>
              </div>
              <div className="history-item-meta">
                <span>{item.runtime_seconds ? `${Math.round(item.runtime_seconds)}s runtime` : "runtime pending"}</span>
                <span>{formatCurrency(item.total_cost ?? 0)}</span>
                <span>{formatInteger(item.checkpoint_count ?? 0)} checkpoints</span>
                <span>{formatInteger(item.failure_count ?? 0)} failures</span>
                <span>{item.validator_status ?? "validator pending"}</span>
              </div>
            </button>
          ))
        ) : (
          <div className="section-empty">No prior missions are available for comparison.</div>
        )}
      </div>
    </section>
  );
}

export default function MissionIntelligenceView({
  mission,
  history,
  trace,
  diffState,
  initialSection,
  onSelectMission
}) {
  const [activeSection, setActiveSection] = useState(initialSection || "simulation");

  useEffect(() => {
    if (initialSection) {
      setActiveSection(initialSection);
    }
  }, [initialSection]);

  const currentSection = useMemo(
    () => SECTIONS.find((section) => section.id === activeSection) ?? SECTIONS[0],
    [activeSection]
  );

  return (
    <div className="workspace-view workspace-intelligence">
      <div className="intelligence-layout">
        <aside className="panel intelligence-rail">
          <div className="section-title">
            <h2>Mission Intelligence</h2>
            <p>Simulation, governance, validation, and final review evidence stay separated so nothing repeats needlessly.</p>
          </div>
          <nav className="intelligence-nav" aria-label="Mission intelligence sections">
            {SECTIONS.map((section) => (
              <button
                key={section.id}
                type="button"
                className={`intelligence-nav-item ${activeSection === section.id ? "is-active" : ""}`}
                onClick={() => setActiveSection(section.id)}
              >
                {section.label}
              </button>
            ))}
          </nav>
        </aside>

        <div className="intelligence-main">
          <div className="workspace-section-header">
            <div>
              <p className="eyebrow">Evidence View</p>
              <h1>{currentSection.label}</h1>
              <p className="workspace-section-copy">
                This view is reserved for deep inspection, not repeated summaries of the live workspace.
              </p>
            </div>
          </div>

          {activeSection === "simulation" ? <SimulationSection mission={mission} trace={trace} /> : null}
          {activeSection === "validation" ? <ValidationSection mission={mission} /> : null}
          {activeSection === "diff" ? <DiffSection mission={mission} diffState={diffState} /> : null}
          {activeSection === "civic" ? <CivicSection mission={mission} trace={trace} /> : null}
          {activeSection === "history" ? (
            <HistorySection history={history} onSelectMission={onSelectMission} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
