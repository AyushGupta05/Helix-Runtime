import React from "react";

import { useBidRanking } from "../hooks/useBidRanking";
import { formatInteger, formatRuntime } from "../lib/format";
import { useMissionElapsedSeconds } from "../lib/useMissionElapsed";
import "../styles/screens.css";

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? "Repo";
}

function changedFiles(mission) {
  const outcomeFiles = mission?.outcome_summary?.changed_files;
  if (Array.isArray(outcomeFiles) && outcomeFiles.length) {
    return outcomeFiles;
  }
  const worktree = mission?.worktree_state?.changed_files;
  if (Array.isArray(worktree) && worktree.length) {
    return worktree;
  }
  const checkpointFiles = mission?.accepted_checkpoints?.at(-1)?.affected_files;
  if (Array.isArray(checkpointFiles) && checkpointFiles.length) {
    return checkpointFiles;
  }
  return [];
}

function confidencePercent(bid, mission) {
  const value = Number(
    bid?.confidence ??
      mission?.outcome_summary?.confidence ??
      mission?.validation_report?.confidence ??
      0.55
  );
  return Math.round(value * 100);
}

function validationPassed(report) {
  return Boolean(report?.passed);
}

function reliabilityNotes(mission, bid, fileCount, passed) {
  const notes = [
    "Winner selected after simulation and validation",
    bid?.provider ? `${bid.provider} context influenced fix` : "Provider context captured in mission trace",
    fileCount
      ? `${fileCount} file${fileCount === 1 ? "" : "s"} touched with bounded scope`
      : "No widened file surface detected",
    passed ? "No policy violations in execution" : "Validation requires follow-up review"
  ];
  return notes;
}

function trustRows(mission, passed) {
  const noRegression = passed;
  const fallbackUnused =
    !String(mission?.bidding_state?.generation_mode ?? "").includes("deterministic_fallback");
  return [
    { label: "Tests Passed", state: passed ? "ok" : "warn" },
    { label: "No Regression", state: noRegression ? "ok" : "warn" },
    {
      label: "Fallback",
      state: fallbackUnused ? "ok" : "warn",
      detail: fallbackUnused ? "Not Used" : "Used"
    }
  ];
}

function governanceRows(mission) {
  return [
    {
      label: "Civic",
      value: String(mission?.civic_connection?.status ?? "idle").replace(/[_-]/g, " ")
    },
    {
      label: "Skills Used",
      value: (mission?.available_skills ?? []).join(", ") || "None"
    },
    {
      label: "Governed Actions",
      value: formatInteger((mission?.recent_civic_actions ?? []).length)
    },
    {
      label: "Envelope",
      value:
        (mission?.governed_bid_envelopes ?? []).find(
          (envelope) => envelope.bid_id === mission?.winner_bid_id
        )?.status ?? "Approved"
    }
  ];
}

export default React.memo(function OutcomeResultsScreen({ mission, usageSummary }) {
  const selectedBid = useBidRanking(mission, 1)[0] ?? null;
  const files = changedFiles(mission);
  const passed = validationPassed(mission?.validation_report);
  const elapsedSeconds = useMissionElapsedSeconds(mission);
  const confidence = confidencePercent(selectedBid, mission);
  const missionTotals = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const trust = trustRows(mission, passed);
  const governance = governanceRows(mission);
  const notes = reliabilityNotes(mission, selectedBid, files.length, passed);
  const summaryBullets = [
    selectedBid
      ? `${selectedBid.role ?? selectedBid.strategy_family} selected`
      : "Winner strategy pending",
    selectedBid?.mission_rationale ??
      selectedBid?.search_summary ??
      "Execution rationale captured in mission trace.",
    mission?.latest_diff_summary || "No diff summary available yet."
  ];

  return (
    <section className="console-screen console-screen-outcome panel">
      <header className="console-topbar console-topbar-outcome">
        <div className="console-topbar-group">
          <span>Repo: {repoLabel(mission?.repo_path)}</span>
          <span>Objective: {mission?.objective ?? "Mission objective pending"}</span>
          <span>Status: {mission?.outcome ?? mission?.run_state ?? "pending"}</span>
        </div>
      </header>

      <div className="console-result-summarybar">
        <span>
          Repo: <strong>{repoLabel(mission?.repo_path)}</strong>
        </span>
        <span>
          Status: <strong>{mission?.outcome ?? mission?.run_state ?? "pending"}</strong>
        </span>
        <span>
          Duration: <strong>{formatRuntime(elapsedSeconds)}</strong>
        </span>
        <span>
          Spend: <strong>{formatInteger(missionTotals.total_tokens ?? 0)} tokens</strong>
        </span>
        <span>
          Files: <strong>{passed ? "Passed" : "Needs review"}</strong>
        </span>
      </div>

      <div className="console-result-grid">
        <aside className="console-result-left panel-like">
          <div className="console-result-nav-section">
            <h3>Mission</h3>
            <div className="console-nav-item active">Current Mission</div>
            <div className="console-nav-item">Past Missions</div>
          </div>
          <div className="console-result-nav-section">
            <h3>Structure</h3>
            <div className="console-nav-item">Checkpoints</div>
            <div className="console-nav-item">Changed Files</div>
            <div className="console-nav-item active">Validation</div>
          </div>
          <div className="console-result-nav-section">
            <h3>Governance</h3>
            <div className="console-nav-item">Civic Evidence</div>
            <div className="console-nav-item">Recovery Log</div>
          </div>
        </aside>

        <main className="console-result-main panel-like">
          <nav className="console-result-tabs">
            <button type="button" className="active">
              Result
            </button>
            <button type="button">Diff</button>
            <button type="button">Validation</button>
            <button type="button">Governance</button>
            <button type="button">Recovery</button>
          </nav>

          <section className="console-outcome-summary">
            <h2>Outcome Summary</h2>
            <ul>
              {summaryBullets.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          <section className="console-outcome-summary">
            <h2>Files Touched</h2>
            {files.length ? (
              <ul>
                {files.slice(0, 8).map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            ) : (
              <p>No changed files surfaced in current snapshot.</p>
            )}
          </section>

          <section className="console-outcome-summary">
            <h2>Impact</h2>
            <p>
              {files.length <= 3
                ? "Low risk, bounded file surface."
                : "Moderate risk due to wider file impact."}
            </p>
          </section>

          <section className="console-outcome-summary">
            <h2>Remaining Risk</h2>
            <p>
              {passed
                ? "No high-severity risk detected."
                : "Validation did not fully pass. Review evidence before merge."}
            </p>
          </section>
        </main>

        <aside className="console-result-right">
          <section className="panel-like console-result-card">
            <h3>Trust</h3>
            {trust.map((item) => (
              <div key={item.label} className={`console-status-row ${item.state}`}>
                <span className="console-status-dot" />
                <span>{item.label}</span>
                <strong>{item.detail ?? (item.state === "ok" ? "OK" : "Watch")}</strong>
              </div>
            ))}
            <p className="console-confidence-line">Confidence: {confidence}%</p>
          </section>

          <section className="panel-like console-result-card">
            <h3>Governance</h3>
            {governance.map((row) => (
              <div key={row.label} className="console-kv-row">
                <span>{row.label}</span>
                <strong>{row.value}</strong>
              </div>
            ))}
          </section>

          <section className="panel-like console-result-card">
            <h3>Why this result is reliable</h3>
            <ul>
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </section>
  );
});
