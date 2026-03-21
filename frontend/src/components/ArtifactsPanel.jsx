import {
  formatInteger,
  formatNumber,
  formatUsageCost,
  shortCommit,
  summarizeProvider,
  usageCostStatusDetail
} from "../lib/format";

function humanizeStrategy(value) {
  return String(value || "pending")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function truncate(value, max = 140) {
  const text = String(value || "");
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max - 1)}...`;
}

function decisionReason(selectedBid, latestProposalTrace, failureContext) {
  if (latestProposalTrace?.payload?.summary) {
    return truncate(latestProposalTrace.payload.summary);
  }
  if (selectedBid) {
    const parts = [];
    if (selectedBid.score !== null && selectedBid.score !== undefined) {
      parts.push(`score ${formatNumber(selectedBid.score)}`);
    }
    if (selectedBid.confidence !== null && selectedBid.confidence !== undefined) {
      parts.push(`confidence ${formatNumber(selectedBid.confidence)}`);
    }
    const numericRisk = Number(selectedBid.risk ?? 0);
    parts.push(numericRisk >= 0.66 ? "high risk" : numericRisk >= 0.33 ? "medium risk" : "low risk");
    if (selectedBid.search_summary) {
      parts.push(selectedBid.search_summary);
    }
    return `Selected on ${parts.join(", ")}.`;
  }
  if (failureContext?.details) {
    return truncate(failureContext.details);
  }
  return "No winner has been selected yet.";
}

function validationItems(report) {
  if (!report) {
    return [];
  }
  if (report.command_results?.length) {
    return report.command_results.map((result) => ({
      label: result.command?.[result.command.length - 1] ?? "validator",
      passed: Number(result.exit_code) === 0
    }));
  }
  return [
    {
      label: "validation",
      passed: Boolean(report.passed)
    }
  ];
}

function branchSummary(mission, acceptedCheckpoint) {
  const branchName = mission.branch_name ?? "branch pending";
  const headCommit = acceptedCheckpoint?.commit_sha ?? mission.head_commit ?? mission.worktree_state?.accepted_commit ?? null;
  return {
    branchName,
    headCommit,
    checkpoint: acceptedCheckpoint
  };
}

export default function ArtifactsPanel({
  mission,
  diffState,
  usageSummary,
  selectedBid,
  latestProposalTrace,
  latestCheckpoint
}) {
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  const failureContext = mission.failure_context ?? null;
  const acceptedCheckpoint = latestCheckpoint ?? mission.accepted_checkpoints?.at(-1) ?? null;
  const repoLineage = branchSummary(mission, acceptedCheckpoint);
  const providerTotals = Object.values(usageSummary?.by_provider ?? {}).sort(
    (left, right) => right.total_tokens - left.total_tokens
  );
  const changedFiles = worktree.changed_files?.length
    ? worktree.changed_files
    : mission.validation_report?.changed_files ?? [];
  const validators = validationItems(mission.validation_report);
  const winnerProvider = latestProposalTrace?.provider ?? selectedBid?.provider ?? null;
  const standbyBid =
    mission.bids.find((bid) => bid.bid_id === mission.standby_bid_id) ??
    mission.bids.find((bid) => bid.standby) ??
    null;
  const branchIsClean = !changedFiles.length && Boolean(repoLineage.headCommit);

  return (
    <div className="artifact-stack">
      <section className="artifact-card artifact-card-focus">
        <div className="artifact-card-head">
          <h3>Current Decision</h3>
          <span>{mission.active_task?.task_id ?? mission.active_task_id ?? "waiting"}</span>
        </div>
        <div className="decision-grid">
          <div className="decision-block">
            <span>Winner</span>
            <strong>
              {selectedBid ? humanizeStrategy(selectedBid.strategy_family) : "Waiting"}
              {winnerProvider ? ` (${summarizeProvider(winnerProvider)})` : ""}
            </strong>
          </div>
          <div className="decision-block">
            <span>Standby</span>
            <strong>
              {standbyBid
                ? `${humanizeStrategy(standbyBid.strategy_family)} (${summarizeProvider(standbyBid.provider)})`
                : "No standby"}
            </strong>
          </div>
        </div>
        <div className="decision-reason">
          <span>Why selected</span>
          <p>{decisionReason(selectedBid, latestProposalTrace, failureContext)}</p>
        </div>
      </section>

      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Cost Monitor</h3>
          <span>{formatInteger(usageSummary?.mission?.total_tokens ?? 0)} tok</span>
        </div>
        <div className="usage-stack">
          <div className="usage-summary-card">
            <span>Mission Usage</span>
            <strong>
              {formatInteger(usageSummary?.mission?.total_tokens ?? 0)} tok |{" "}
              {formatUsageCost(usageSummary?.mission)}
            </strong>
            {usageCostStatusDetail(usageSummary?.mission) ? (
              <p>{usageCostStatusDetail(usageSummary?.mission)}</p>
            ) : null}
          </div>
          <div className="usage-summary-card">
            <span>Current Round</span>
            <strong>
              {formatInteger(usageSummary?.active_task?.total_tokens ?? 0)} tok |{" "}
              {formatUsageCost(usageSummary?.active_task)}
            </strong>
            {usageCostStatusDetail(usageSummary?.active_task) ? (
              <p>{usageCostStatusDetail(usageSummary?.active_task)}</p>
            ) : null}
          </div>
        </div>
        <div className="provider-totals">
          {providerTotals.length ? (
            providerTotals.map((row) => (
              <div key={row.provider} className="provider-total-row">
                <strong>{summarizeProvider(row.provider)}</strong>
                <span>
                  {formatInteger(row.total_tokens)} tok | {formatUsageCost(row)}
                </span>
              </div>
            ))
          ) : (
            <p>No provider spend recorded yet.</p>
          )}
        </div>
      </section>

      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Repo State</h3>
          <span>{branchIsClean ? "Validated branch" : `${changedFiles.length} live changes`}</span>
        </div>
        <div className={`repo-state-banner ${branchIsClean ? "is-accepted" : changedFiles.length ? "is-changed" : "is-clean"}`}>
          <strong>
            {acceptedCheckpoint
              ? `Accepted checkpoint ${acceptedCheckpoint.label}`
              : "No checkpoint accepted yet"}
          </strong>
          <p>
            {acceptedCheckpoint
              ? `${repoLineage.branchName} is anchored at ${shortCommit(repoLineage.headCommit)}`
              : "The managed branch will show up here once the first validator gate passes."}
          </p>
        </div>
        <div className="repo-lineage-grid">
          <div className="repo-lineage-card">
            <span>Branch</span>
            <strong>{repoLineage.branchName}</strong>
          </div>
          <div className="repo-lineage-card">
            <span>Head commit</span>
            <strong>{shortCommit(repoLineage.headCommit)}</strong>
          </div>
          <div className="repo-lineage-card">
            <span>Rollback anchor</span>
            <strong>
              {acceptedCheckpoint?.rollback_pointer
                ? shortCommit(acceptedCheckpoint.rollback_pointer)
                : "Latest checkpoint"}
            </strong>
          </div>
          <div className="repo-lineage-card">
            <span>Diff summary</span>
            <strong>{truncate(acceptedCheckpoint?.diff_summary ?? mission.latest_diff_summary ?? worktree.diff_stat ?? "No diff yet.", 90)}</strong>
          </div>
        </div>
        {changedFiles.length ? (
          <ul className="repo-state-list">
            {changedFiles.slice(0, 6).map((file) => (
              <li key={file}>{file}</li>
            ))}
          </ul>
        ) : (
          <p className="repo-clean-note">
            {branchIsClean
              ? "The live worktree is clean because the latest accepted changes are already committed on the managed branch."
              : "No patch has been accepted yet."}
          </p>
        )}
        <div className="repo-validation">
          <span>Validation</span>
          <div className="validation-list">
            {validators.map((item) => (
              <span
                key={item.label}
                className={`validation-item ${item.passed ? "is-pass" : "is-fail"}`}
              >
                {item.label}: {item.passed ? "PASS" : "FAIL"}
              </span>
            ))}
            {!validators.length ? <span className="validation-item">No validation yet</span> : null}
          </div>
          <p>
            Checkpoint:{" "}
            {acceptedCheckpoint
              ? `${acceptedCheckpoint.label} accepted (${shortCommit(acceptedCheckpoint.commit_sha)})`
              : "No checkpoint accepted yet"}
          </p>
        </div>
        <details className="artifact-detail">
          <summary>View diff</summary>
          <pre>{worktree.diff_patch || worktree.diff_stat || "No diff yet."}</pre>
        </details>
      </section>
    </div>
  );
}
