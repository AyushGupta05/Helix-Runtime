import { useEffect, useMemo, useState } from "react";

import StatusBadge from "./StatusBadge";
import {
  formatCurrency,
  formatInteger,
  humanizeEventType,
  relativeTime,
  shortCommit,
  summarizeProvider
} from "../lib/format";

const SECTIONS = [
  { id: "overview", label: "Repo Insights" },
  { id: "checkpoints", label: "Checkpoints" },
  { id: "validation", label: "Validation" },
  { id: "diff", label: "Diff Explorer" },
  { id: "usage", label: "Model Usage" },
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

function auditRows(mission, trace) {
  const ledgerRows = (mission.civic_activity?.ledger ?? []).map((entry) => ({
    id: `ledger-${entry.audit_id ?? entry.created_at ?? entry.action_type ?? "civic"}`,
    time: entry.created_at,
    action: humanizeEventType(entry.action_type ?? "civic activity"),
    result: entry.status ?? entry.policy_state ?? "captured",
    reason:
      entry.reasons?.join(" ") ||
      entry.target ||
      "Governance evidence captured in the Civic ledger."
  }));
  const traceRows = governedActionRows(mission, trace);
  const summaryRows = Object.entries(mission.civic_audit_summary ?? {}).map(([key, value]) => ({
    id: `summary-${key}`,
    time: null,
    action: humanizeEventType(key),
    result: typeof value === "number" ? `${value} records` : "captured",
    reason: "Aggregated from mission audit summary."
  }));
  return [...ledgerRows, ...traceRows, ...summaryRows];
}

function usageRows(usageSummary) {
  return Object.values(usageSummary?.by_provider ?? {}).sort(
    (left, right) => Number(right.total_tokens ?? 0) - Number(left.total_tokens ?? 0)
  );
}

function civicConnectionSummary(mission) {
  const connection = mission.civic_connection ?? {};
  const status = String(connection.status ?? connection.state ?? "idle").replace(/[_-]/g, " ");
  const toolkit = connection.toolkit_id ?? connection.toolkit ?? "default toolkit";
  return {
    status,
    toolkit,
    detail: connection.checked_at ? `${toolkit} | ${relativeTime(connection.checked_at)}` : toolkit
  };
}

function civicCapabilityCards(mission) {
  const connection = civicConnectionSummary(mission);
  const capabilities = mission.civic_capabilities ?? {};
  const availableSkills = mission.available_skills ?? [];
  const health = mission.skill_health ?? {};
  const skillOutputs = mission.skill_outputs ?? {};
  const envelopes = mission.governed_bid_envelopes ?? [];
  return [
    {
      label: "Connection",
      value: connection.status,
      detail: connection.detail
    },
    {
      label: "Toolkit",
      value: connection.toolkit,
      detail: capabilities.provider ?? capabilities.source ?? "Civic capability plane"
    },
    {
      label: "Active skills",
      value: String(availableSkills.length),
      detail: availableSkills.length ? availableSkills.slice(0, 3).join(" | ") : "No active skills surfaced yet"
    },
    {
      label: "Health",
      value: Object.keys(health).length ? `${Object.keys(health).length} signals` : "Pending",
      detail: health.blocked ? "One or more capabilities are blocked" : "No degraded capability reported"
    },
    {
      label: "Skill outputs",
      value: Object.keys(skillOutputs).length ? `${Object.keys(skillOutputs).length} packets` : "None",
      detail: Object.keys(skillOutputs).length ? "Read-only evidence packets recorded" : "Skill evidence has not been surfaced yet"
    },
    {
      label: "Envelopes",
      value: `${envelopes.length}`,
      detail: envelopes.length ? "Civic-issued bid contracts are present" : "No governed envelopes recorded yet"
    }
  ];
}

function skillOutputEntries(mission) {
  const outputs = mission.skill_outputs ?? {};
  return Object.entries(outputs).map(([skill, value]) => ({
    id: skill,
    skill,
    summary:
      typeof value === "string"
        ? value
        : value?.summary ??
          value?.CI_summary ??
          value?.ci_summary ??
          value?.detail ??
          "No summary captured",
    provenance: typeof value === "object" && value !== null ? value?.provenance ?? value?.source ?? "Civic" : "Civic",
    freshness: typeof value === "object" && value !== null ? value?.freshness ?? value?.last_checked ?? value?.updated_at ?? null : null,
    confidence: typeof value === "object" && value !== null ? value?.confidence ?? value?.score ?? null : null
  }));
}

function governedActionRows(mission, trace) {
  const actions = mission.recent_civic_actions ?? [];
  if (actions.length) {
    return actions.map((entry, index) => ({
      id: entry.audit_id ?? `${entry.event_type ?? "civic"}-${index}`,
      time: entry.created_at,
      action: humanizeEventType(entry.event_type ?? entry.action_type ?? "civic action"),
      result: entry.status ?? entry.policy_state ?? "captured",
      reason: entry.reason ?? entry.message ?? entry.details ?? "Governed Civic action"
    }));
  }

  return (trace ?? [])
    .filter(
      (entry) =>
        String(entry.trace_type ?? "").toLowerCase().includes("civic") ||
        String(entry.title ?? "").toLowerCase().includes("skill") ||
        String(entry.title ?? "").toLowerCase().includes("envelope")
    )
    .map((entry) => ({
      id: `trace-${entry.id}`,
      time: entry.created_at,
      action: entry.title ?? humanizeEventType(entry.trace_type),
      result: entry.status ?? "captured",
      reason: entry.message ?? "Governed Civic state captured in mission trace."
    }));
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

function repoInsightCards(mission, diffState, usageSummary, trace) {
  const repoInsights = mission.repo_insights ?? {};
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  const validators = validatorRows(mission.validation_report, mission.history_metrics, mission.run_state);
  const providerCount = Object.keys(usageSummary?.by_provider ?? {}).length;
  const latestTrace = [...(trace ?? [])].reverse()[0];
  const toolchain = repoInsights.toolchain ?? {};
  const toolCommands = [...(toolchain.tests ?? []), ...(toolchain.lint ?? []), ...(toolchain.static ?? [])];
  const riskSurface = [...(repoInsights.risky_paths ?? []), ...(repoInsights.protected_interfaces ?? [])];
  const dependencyFiles = repoInsights.dependency_files ?? [];
  const hotspots = repoInsights.complexity_hotspots ?? [];

  return [
    {
      label: "Repository",
      value: repoLabel(mission.repo_path),
      detail: `${mission.branch_name ?? "Managed branch pending"} | ${repoInsights.runtime ?? "runtime pending"}`
    },
    {
      label: "Toolchain",
      value: toolCommands.length ? `${toolCommands.length} commands mapped` : "Pending",
      detail: toolCommands.length ? toolCommands.slice(0, 2).join(" | ") : "No baseline commands detected yet"
    },
    {
      label: "Risk surface",
      value: riskSurface.length ? `${riskSurface.length} sensitive paths` : "Low surface area",
      detail: riskSurface.length ? riskSurface.slice(0, 2).join(" | ") : "No risky or protected paths detected yet"
    },
    {
      label: "Baseline evidence",
      value: repoInsights.latest_validation?.status ?? (validators.length ? `${validators.length} checks` : "Pending"),
      detail:
        dependencyFiles[0] ??
        hotspots[0] ??
        worktree.reason ??
        mission.latest_diff_summary ??
        "Waiting for a material patch."
    },
    {
      label: "Model surface",
      value: providerCount ? `${providerCount} providers active` : "No provider usage yet",
      detail: latestTrace?.message ?? "Activity will appear after bidding and execution begin."
    }
  ];
}

function OverviewSection({ mission, diffState, usageSummary, trace }) {
  const insights = repoInsightCards(mission, diffState, usageSummary, trace);
  const validators = validatorRows(mission.validation_report, mission.history_metrics, mission.run_state);
  const changedFiles = changedFilesFor(mission, diffState);
  const repoInsights = mission.repo_insights ?? {};
  const dependencyFiles = repoInsights.dependency_files ?? [];
  const hotspots = repoInsights.complexity_hotspots ?? [];
  const riskSurface = [...(repoInsights.risky_paths ?? []), ...(repoInsights.protected_interfaces ?? [])];

  return (
    <div className="intelligence-section-grid">
      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Repo understanding</h2>
          <p>Helix keeps the operational understanding of the repo visible without forcing the operator into logs first.</p>
        </div>
        <div className="intelligence-card-grid">
          {insights.map((card) => (
            <article key={card.label} className="insight-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Baseline evidence</h2>
          <p>Current validation and changed files are grouped here so repo sensitivity is easy to judge quickly.</p>
        </div>
        <div className="evidence-stack">
          <div className="intel-callout-grid">
            <article className="intel-callout">
              <span>Active task</span>
              <strong>{mission.active_task?.task_id ?? mission.active_task_id ?? "Awaiting task selection"}</strong>
            </article>
            <article className="intel-callout">
              <span>Failure signal</span>
              <strong>{mission.failure_context?.failure_type ?? "No active failure"}</strong>
            </article>
            <article className="intel-callout">
              <span>Dependencies</span>
              <strong>{dependencyFiles.length ? dependencyFiles.length : "No manifest"}</strong>
            </article>
            <article className="intel-callout">
              <span>Hotspots</span>
              <strong>{hotspots[0] ?? "No hotspot captured yet"}</strong>
            </article>
          </div>

          <div className="validation-pill-row">
            {validators.length ? (
              validators.map((row) => (
                <span
                  key={row.id}
                  className={`validation-item ${row.status === "passed" ? "is-pass" : row.status === "failed" ? "is-fail" : ""}`}
                >
                  {row.label}: {row.status === "passed" ? "PASS" : row.status === "failed" ? "FAIL" : "PENDING"}
                </span>
              ))
            ) : (
              <span className="validation-item">Validation pending</span>
            )}
          </div>

          <div className="file-token-list">
            {riskSurface.length ? (
              riskSurface.slice(0, 6).map((path) => (
                <span key={path} className="muted-chip">
                  {path}
                </span>
              ))
            ) : (
              <p className="muted-copy">No risky or protected paths have been flagged yet.</p>
            )}
          </div>

          <div className="file-token-list">
            {changedFiles.length ? (
              changedFiles.map((file) => (
                <span key={file} className="file-chip">
                  {file}
                </span>
              ))
            ) : (
              <p className="muted-copy">No changed files recorded yet.</p>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function CheckpointSection({ mission, diffState }) {
  const checkpoints = [...(mission.accepted_checkpoints ?? [])].reverse();
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};

  return (
    <div className="intelligence-section-grid">
      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Checkpoint trail</h2>
          <p>Accepted checkpoints stay readable as a progression instead of a raw event list.</p>
        </div>
        <div className="checkpoint-list">
          {checkpoints.length ? (
            checkpoints.map((checkpoint) => (
              <article key={checkpoint.checkpoint_id} className="checkpoint-card">
                <div className="checkpoint-head">
                  <strong>{checkpoint.label ?? "Accepted checkpoint"}</strong>
                  <StatusBadge value="complete" quiet />
                </div>
                <div className="checkpoint-meta">
                  <span>{relativeTime(checkpoint.created_at)}</span>
                  <span>{shortCommit(checkpoint.commit_sha)}</span>
                  <span>{checkpoint.strategy_family ?? "strategy not attached"}</span>
                </div>
                <p>{checkpoint.summary ?? checkpoint.diff_summary ?? "No checkpoint summary captured."}</p>
                <div className="file-token-list">
                  {(checkpoint.affected_files ?? []).length ? (
                    checkpoint.affected_files.map((file) => (
                      <span key={file} className="file-chip">
                        {file}
                      </span>
                    ))
                  ) : (
                    <span className="muted-chip">Rollback available from latest accepted anchor</span>
                  )}
                </div>
              </article>
            ))
          ) : (
            <div className="section-empty">No accepted checkpoints yet.</div>
          )}
        </div>
      </section>

      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Current branch state</h2>
          <p>Branch and rollback readiness sit next to the checkpoint ledger so trust stays grounded in repo state.</p>
        </div>
        <div className="intelligence-card-grid">
          <article className="insight-card">
            <span>Branch</span>
            <strong>{mission.branch_name ?? "branch pending"}</strong>
            <p>Managed branch created for this mission.</p>
          </article>
          <article className="insight-card">
            <span>Head commit</span>
            <strong>{shortCommit(mission.head_commit)}</strong>
            <p>{worktree.accepted_commit ? `Anchored at ${shortCommit(worktree.accepted_commit)}.` : "Commit anchor appears after first acceptance."}</p>
          </article>
          <article className="insight-card">
            <span>Rollback pointer</span>
            <strong>{shortCommit(checkpoints[0]?.rollback_pointer)}</strong>
            <p>{checkpoints[0]?.rollback_pointer ? "Rollback target recorded." : "Latest checkpoint becomes rollback anchor."}</p>
          </article>
          <article className="insight-card">
            <span>Diff summary</span>
            <strong>{(checkpoints[0]?.diff_summary ?? mission.latest_diff_summary ?? "Waiting").slice(0, 48)}</strong>
            <p>Short summary of the most recent accepted or active change.</p>
          </article>
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

function UsageSection({ usageSummary, trace }) {
  const rows = usageRows(usageSummary);
  const invocations = (usageSummary?.invocations ?? []).length
    ? usageSummary.invocations
    : (trace ?? []).filter((entry) => String(entry.trace_type ?? "").startsWith("model.invocation"));

  return (
    <div className="intelligence-section-grid">
      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Provider usage</h2>
          <p>Spend is split by provider so cost, token use, and provider mix can be understood quickly.</p>
        </div>
        <div className="usage-provider-list">
          {rows.length ? (
            rows.map((row) => (
              <article key={row.provider} className="usage-provider-card">
                <strong>{summarizeProvider(row.provider)}</strong>
                <span>{formatInteger(row.total_tokens ?? 0)} tok</span>
                <p>{formatCurrency(row.total_cost ?? 0)}</p>
              </article>
            ))
          ) : (
            <div className="section-empty">Usage totals will appear once provider calls complete.</div>
          )}
        </div>
      </section>

      <section className="panel intelligence-panel">
        <div className="section-title">
          <h2>Recent model activity</h2>
          <p>Invocation evidence is available here without taking over the live market view.</p>
        </div>
        <div className="ledger-list">
          {invocations.length ? (
            invocations.slice(-8).reverse().map((entry) => (
              <article
                key={entry.id ?? entry.invocation_id ?? `${entry.provider}-${entry.started_at}`}
                className="ledger-row"
              >
                <div>
                  <strong>{humanizeEventType(entry.trace_type ?? entry.invocation_kind ?? "model invocation")}</strong>
                  <p>{entry.message ?? entry.model_id ?? "No invocation message attached."}</p>
                </div>
                <span>{entry.provider ? summarizeProvider(entry.provider) : "Unknown"}</span>
              </article>
            ))
          ) : (
            <div className="section-empty">No invocation trace records yet.</div>
          )}
        </div>
      </section>
    </div>
  );
}

function CivicSection({ mission, trace }) {
  const rows = auditRows(mission, trace);
  const capabilityCards = civicCapabilityCards(mission);
  const skillOutputs = skillOutputEntries(mission);
  const envelopes = mission.governed_bid_envelopes ?? [];

  return (
    <section className="panel intelligence-panel">
      <div className="section-title">
        <h2>Civic evidence</h2>
        <p>Governance stays inspectable as a ledger rather than being mixed into every other panel.</p>
      </div>
      <div className="intelligence-card-grid">
        {capabilityCards.map((card) => (
          <article key={card.label} className="insight-card">
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </article>
        ))}
      </div>

      <div className="section-title" style={{ marginTop: "1rem" }}>
        <h2>Skill outputs</h2>
        <p>Read-only evidence packets stay grouped by skill so operators can see what shaped the market.</p>
      </div>
      <div className="intelligence-card-grid">
        {skillOutputs.length ? (
          skillOutputs.map((item) => (
            <article key={item.id} className="insight-card">
              <span>{item.skill}</span>
              <strong>{item.summary}</strong>
              <p>
                {item.provenance}
                {item.freshness ? ` | ${relativeTime(item.freshness)}` : ""}
                {item.confidence !== null && item.confidence !== undefined ? ` | confidence ${Math.round(Number(item.confidence) * 100)}%` : ""}
              </p>
            </article>
          ))
        ) : (
          <div className="section-empty">No skill outputs have been captured yet.</div>
        )}
      </div>

      <div className="section-title" style={{ marginTop: "1rem" }}>
        <h2>Governed envelopes</h2>
        <p>Shortlisted strategies carry Civic-issued constraints that shape what they are allowed to do.</p>
      </div>
      <div className="ledger-list">
        {envelopes.length ? (
          envelopes.map((envelope, index) => (
            <article key={envelope.envelope_id ?? envelope.bid_id ?? index} className="ledger-row">
              <div>
                <strong>{envelope.bid_id ?? envelope.task_id ?? "Bid envelope"}</strong>
                <p>
                  {envelope.allowed_skills?.length ? envelope.allowed_skills.join(" | ") : "No allowed skills surfaced"}
                </p>
              </div>
              <div className="ledger-meta">
                <span>{String(envelope.policy_decision ?? envelope.status ?? "governed").replace(/[_-]/g, " ")}</span>
                <span>{envelope.runtime_limit_seconds ? `${envelope.runtime_limit_seconds}s` : envelope.toolkit_id ?? "Civic"}</span>
              </div>
            </article>
          ))
        ) : (
          <div className="section-empty">No governed envelopes have been recorded yet.</div>
        )}
      </div>

      <div className="section-title" style={{ marginTop: "1rem" }}>
        <h2>Governed actions</h2>
        <p>Audited Civic actions and revocations are retained here for operational review.</p>
      </div>
      <div className="ledger-list">
        {rows.length ? (
          rows.map((row) => (
            <article key={row.id} className="ledger-row">
              <div>
                <strong>{row.action}</strong>
                <p>{row.reason}</p>
              </div>
              <div className="ledger-meta">
                <span>{row.result}</span>
                <span>{row.time ? relativeTime(row.time) : "summary"}</span>
              </div>
            </article>
          ))
        ) : (
          <div className="section-empty">No Civic-specific audit events have been recorded yet.</div>
        )}
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
            <button key={item.mission_id} className="history-compare-card" onClick={() => onSelectMission(item)}>
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
  usageSummary,
  initialSection,
  onSelectMission
}) {
  const [activeSection, setActiveSection] = useState(initialSection || "overview");

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
            <p>Inspect the run in analytical mode without losing the calm of the mission workspace.</p>
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
                History, checkpoints, repo understanding, validation evidence, diff, usage, and governance are grouped here for deliberate review.
              </p>
            </div>
          </div>

          {activeSection === "overview" ? (
            <OverviewSection mission={mission} diffState={diffState} usageSummary={usageSummary} trace={trace} />
          ) : null}
          {activeSection === "checkpoints" ? <CheckpointSection mission={mission} diffState={diffState} /> : null}
          {activeSection === "validation" ? <ValidationSection mission={mission} /> : null}
          {activeSection === "diff" ? <DiffSection mission={mission} diffState={diffState} /> : null}
          {activeSection === "usage" ? <UsageSection usageSummary={usageSummary} trace={trace} /> : null}
          {activeSection === "civic" ? <CivicSection mission={mission} trace={trace} /> : null}
          {activeSection === "history" ? (
            <HistorySection history={history} onSelectMission={onSelectMission} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
