import React from "react";

import { useBidRanking } from "../hooks/useBidRanking";
import {
  formatInteger,
  formatRuntime,
  formatUsageCost,
  usageCostStatusDetail
} from "../lib/format";
import { missionPullRequestUrl } from "../lib/pullRequest";
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

function researchContextSummary(mission) {
  const knowledge = mission?.skill_outputs?.knowledge_context;
  if (!knowledge || typeof knowledge !== "object") {
    return null;
  }
  return {
    summary: String(knowledge.summary ?? "").trim(),
    queries: Array.isArray(knowledge.queries) ? knowledge.queries.filter(Boolean) : [],
    sourceUrls: Array.isArray(knowledge.source_urls) ? knowledge.source_urls.filter(Boolean) : [],
    trusted: Boolean(knowledge?.provenance?.trusted)
  };
}

function trustRows(mission, passed) {
  const noRegression = passed;
  const fallbackUnused =
    !String(mission?.bidding_state?.generation_mode ?? "").includes("deterministic_fallback");
  return [
    { label: "Tests passed", state: passed ? "ok" : "warn" },
    { label: "No regression", state: noRegression ? "ok" : "warn" },
    {
      label: "Fallback",
      state: fallbackUnused ? "ok" : "warn",
      detail: fallbackUnused ? "Not used" : "Used"
    }
  ];
}

function governanceRows(mission, research) {
  const winnerEnvelope =
    (mission?.governed_bid_envelopes ?? []).find(
      (envelope) => envelope.bid_id === mission?.winner_bid_id
    ) ?? null;
  const reviewedBidCount = Math.min(
    3,
    new Set((mission?.governed_bid_envelopes ?? []).map((envelope) => envelope.bid_id)).size
  );
  return [
    {
      label: "Civic",
      value: String(mission?.civic_connection?.status ?? "idle").replace(/[_-]/g, " ")
    },
    {
      label: "Skills used",
      value: (mission?.available_skills ?? []).join(", ") || "None"
    },
    {
      label: "Governed actions",
      value: formatInteger((mission?.recent_civic_actions ?? []).length)
    },
    {
      label: "Top bids reviewed",
      value: formatInteger(reviewedBidCount)
    },
    {
      label: "Winner envelope",
      value: winnerEnvelope?.status ?? "Approved"
    },
    {
      label: "Constraints",
      value:
        Array.isArray(winnerEnvelope?.constraints) && winnerEnvelope.constraints.length
          ? winnerEnvelope.constraints.join(", ")
          : "No extra constraints"
    },
    {
      label: "Research",
      value: research
        ? `${formatInteger(research.sourceUrls.length)} sources / ${formatInteger(research.queries.length)} queries`
        : "Not used"
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
  const spendDetail = usageCostStatusDetail(missionTotals);
  const branchLabel = String(mission?.branch_name ?? "branch");
  const research = researchContextSummary(mission);
  const trust = trustRows(mission, passed);
  const governance = governanceRows(mission, research);
  const pullRequestUrl = missionPullRequestUrl(mission);
  const publishSummary =
    mission?.skill_outputs?.github_publish?.summary ??
    mission?.mission_output?.pull_request_summary ??
    null;
  const acceptedDiffSummary = mission?.mission_output?.accepted_diff_summary ?? null;
  const resultHeading =
    mission?.outcome === "success"
      ? "Mission completed"
      : `${selectedBid?.role ?? selectedBid?.strategy_family ?? "Mission result"} selected`;
  const summaryBullets = [
    mission?.outcome === "success"
      ? "Validated changes accepted and finalized."
      : selectedBid
        ? `${selectedBid.role ?? selectedBid.strategy_family} selected`
        : "Winner strategy pending",
    publishSummary ??
      mission?.latest_diff_summary ??
      selectedBid?.mission_rationale ??
      selectedBid?.search_summary ??
      "Execution rationale captured in mission trace.",
    mission?.latest_diff_summary ||
      acceptedDiffSummary ||
      "Validated diff recorded in the accepted checkpoint.",
    pullRequestUrl
      ? `Pull request ready: ${pullRequestUrl}`
      : mission?.branch_name
        ? `Managed branch ready: ${mission.branch_name}`
        : "No publish target surfaced for this mission yet."
  ];
  const topbarItems = [
    { label: "Repo", value: repoLabel(mission?.repo_path) },
    { label: "Objective", value: mission?.objective ?? "Mission objective pending" },
    { label: "Status", value: mission?.outcome ?? mission?.run_state ?? "pending" }
  ];
  const summaryItems = [
    { label: "Duration", value: formatRuntime(elapsedSeconds) },
    { label: "Spend", value: formatInteger(missionTotals.total_tokens ?? 0), suffix: "tokens" },
    { label: "Cost", value: formatUsageCost(missionTotals) },
    spendDetail ? { label: "Billing", value: spendDetail } : null,
    { label: "Validation", value: passed ? "Passed" : "Needs review" },
    { label: "Branch", value: branchLabel }
  ].filter(Boolean);

  return (
    <div className="workspace-view screen-ref screen-ref-three">
      <section className="panel screen-ref-topbar">
        <div className="screen-ref-topbar-items">
          {topbarItems.map((item) => (
            <div key={item.label} className="screen-ref-topbar-item">
              <span className="screen-ref-topbar-label">{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="panel screen-ref-summary-strip">
        <div className="screen-ref-summary-grid">
          {summaryItems.map((item) => (
            <article key={item.label} className="screen-ref-summary-card">
              <span className="screen-ref-topbar-label">{item.label}</span>
              <strong>
                {item.value}
                {item.suffix ? ` ${item.suffix}` : ""}
              </strong>
            </article>
          ))}
        </div>
      </section>

      <div className="screen-ref-three-grid">
        <aside className="panel screen-ref-nav-panel">
          <div className="screen-ref-nav-section">
            <h2>Mission</h2>
            <div className="screen-ref-nav-item is-active">
              <span className="screen-ref-nav-dot" /> Current Mission
            </div>
            <div className="screen-ref-nav-item">
              <span className="screen-ref-nav-dot" /> Past Missions
            </div>
          </div>

          <div className="screen-ref-nav-section">
            <h2>Structure</h2>
            <div className="screen-ref-nav-item">
              <span className="screen-ref-nav-dot" /> Checkpoints
            </div>
            <div className="screen-ref-nav-item">
              <span className="screen-ref-nav-dot" /> Changed Files
            </div>
            <div className="screen-ref-nav-item is-active">
              <span className="screen-ref-nav-dot" /> Validation
            </div>
          </div>

          <div className="screen-ref-nav-section">
            <h2>Governance</h2>
            <div className="screen-ref-nav-item">
              <span className="screen-ref-nav-dot" /> Civic Evidence
            </div>
            <div className="screen-ref-nav-item">
              <span className="screen-ref-nav-dot" /> Recovery Log
            </div>
          </div>
        </aside>

        <main className="panel screen-ref-result-main">
          <div className="screen-ref-heading-block">
            <h1>{resultHeading}</h1>
          </div>

          <section className="screen-ref-main-section">
            <h2>Outcome Summary</h2>
            <ul>
              {summaryBullets.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>

          <section className="screen-ref-main-section">
            <h2>Files Touched</h2>
            {files.length ? (
              <ul>
                {files.slice(0, 8).map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            ) : (
              <p>No changed files surfaced in the current snapshot.</p>
            )}
          </section>

          <section className="screen-ref-main-section">
            <h2>Impact</h2>
            <p>
              {files.length <= 3
                ? "Low risk, bounded file surface."
                : "Moderate risk due to wider file impact."}
            </p>
          </section>

          <section className="screen-ref-main-section">
            <h2>Remaining Risk</h2>
            <p>
              {passed
                ? "No high-severity risk detected."
                : "Validation did not fully pass. Review evidence before merge."}
            </p>
          </section>
        </main>

        <aside className="screen-ref-three-side">
          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Trust</h2>
            </div>
            <div className="screen-ref-status-list">
              {trust.map((item) => (
                <div key={item.label} className={`screen-ref-status-row is-${item.state}`}>
                  <span className="screen-ref-status-dot" />
                  <span>{item.label}</span>
                  <strong>{item.detail ?? (item.state === "ok" ? "OK" : "Watch")}</strong>
                </div>
              ))}
            </div>
            <p className="screen-ref-confidence">Confidence {confidence}%</p>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Governance</h2>
            </div>
            <div className="screen-ref-kv-list">
              {governance.map((row) => (
                <div key={row.label} className="screen-ref-kv-row">
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>GitHub</h2>
            </div>
            <div className="screen-ref-kv-list">
              <div className="screen-ref-kv-row">
                <span>Branch</span>
                <strong>{mission?.branch_name ?? "Pending"}</strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Pull request</span>
                <strong>{pullRequestUrl ? "Ready" : "Not published"}</strong>
              </div>
            </div>
            {pullRequestUrl ? (
              <a className="primary-button" href={pullRequestUrl} target="_blank" rel="noreferrer">
                Open PR
              </a>
            ) : null}
          </section>
        </aside>
      </div>
    </div>
  );
});
