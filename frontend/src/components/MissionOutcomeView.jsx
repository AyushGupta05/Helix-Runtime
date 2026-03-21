import {
  formatCurrency,
  formatInteger,
  formatNumber,
  formatRuntime,
  humanizeEventType,
  humanizeMissionStage,
  shortCommit,
  summarizeBidOrigin
} from "../lib/format";

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? repoPath ?? "repo";
}

function changedFilesFor(mission, diffState) {
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  return worktree.changed_files?.length
    ? worktree.changed_files
    : mission.validation_report?.changed_files ?? [];
}

function publicApiSurfaceFor(mission) {
  return mission.repo_insights?.public_api_surface ?? [];
}

function summaryText(mission, selectedBid, latestProposalTrace) {
  if (mission.outcome === "success") {
    return latestProposalTrace?.payload?.summary ?? "Mission completed successfully with a validated change set.";
  }
  if (mission.outcome === "partial_success") {
    return "Mission produced a partial result that likely needs operator review before adoption.";
  }
  if (mission.run_state === "finalized") {
    return mission.stop_reason ?? mission.failure_context?.details ?? "Mission finalized, but the final state needs review.";
  }
  return selectedBid?.mission_rationale ?? "Mission is still progressing toward a deliverable outcome.";
}

function confidenceSummary(selectedBid, mission) {
  const confidence = Number(selectedBid?.confidence ?? 0);
  if (confidence >= 0.8 && mission.validation_report?.passed) {
    return "High confidence because the selected strategy validated cleanly and remained within a bounded path.";
  }
  if (confidence >= 0.55) {
    return "Moderate confidence because the mission has a credible path, but still carries some review risk.";
  }
  return "Lower confidence because validation, fallback, or recovery signals suggest closer human review.";
}

function riskList(mission, selectedBid) {
  const risks = [];
  if (!mission.validation_report?.passed) {
    risks.push("Validation did not fully pass or has not finished yet.");
  }
  if (mission.failure_context?.failure_type) {
    risks.push(`Recent recovery context: ${mission.failure_context.failure_type}.`);
  }
  if (Number(selectedBid?.risk ?? 0) >= 0.5) {
    risks.push("The chosen strategy carried a medium-to-high risk score.");
  }
  if (!mission.accepted_checkpoints?.length) {
    risks.push("No accepted checkpoint has been recorded yet.");
  }
  return risks.length ? risks : ["No acute risk flags are currently surfaced by the mission snapshot."];
}

function nextActions(mission, changedFiles, validationPassed) {
  const publicApiSurface = publicApiSurfaceFor(mission);
  const actions = [];
  if (changedFiles.length) {
    actions.push(`Review ${changedFiles.slice(0, 3).join(", ")} first.`);
  }
  if (!validationPassed) {
    actions.push("Inspect the validation report before adopting the branch.");
  }
  if (publicApiSurface.length) {
    actions.push("Confirm the public API surface still behaves as expected.");
  }
  if (mission.accepted_checkpoints?.length) {
    actions.push(`Review checkpoint ${mission.accepted_checkpoints.at(-1)?.label ?? "latest"} before creating a PR.`);
  }
  return actions.length ? actions : ["Open the validation report and review the final diff before promotion."];
}

function validationStats(report, outcomeSummary) {
  const commandResults = report?.command_results ?? [];
  if (commandResults.length) {
    const passed = commandResults.filter((result) => Number(result.exit_code) === 0).length;
    return `${passed}/${commandResults.length} checks passed`;
  }
  if (outcomeSummary?.validation_status) {
    return humanizeMissionStage(outcomeSummary.validation_status);
  }
  return report?.passed ? "Validation passed" : "Validation pending";
}

function outcomeTone(mission) {
  if (mission.outcome === "success") {
    return "success";
  }
  if (mission.outcome === "partial_success") {
    return "warning";
  }
  if (mission.run_state === "finalized") {
    return "danger";
  }
  return "neutral";
}

function civicImpactSummary(mission) {
  const skills = mission.available_skills ?? [];
  const outputs = mission.skill_outputs ?? {};
  const envelopes = mission.governed_bid_envelopes ?? [];
  const connection = mission.civic_connection ?? {};
  const connectionStatus = String(connection.status ?? connection.state ?? "idle").replace(/[_-]/g, " ");
  const skillsText = skills.length ? skills.slice(0, 3).join(", ") : "no active skills";
  const outputCount = Object.keys(outputs).length;
  const envelopeCount = envelopes.length;
  return {
    headline: `${connectionStatus} | ${skills.length} skills`,
    detail: `${skillsText}${outputCount ? ` | ${outputCount} evidence packet${outputCount === 1 ? "" : "s"}` : ""}${envelopeCount ? ` | ${envelopeCount} envelope${envelopeCount === 1 ? "" : "s"}` : ""}`
  };
}

function capabilityInfluenceRows(mission) {
  const rows = Object.entries(mission.skill_outputs ?? {}).map(([skill, value]) => ({
    id: `skill-${skill}`,
    title: skill,
    detail: value?.ci_summary ?? value?.summary ?? value?.detail ?? "Evidence packet captured.",
    meta:
      value?.confidence !== undefined
        ? `confidence ${formatNumber(Number(value.confidence) * 100, 0)}%`
        : "governed evidence"
  }));
  const actionRows = (mission.recent_civic_actions ?? []).slice(0, 3).map((action, index) => ({
    id: action.audit_id ?? `action-${index}`,
    title: humanizeEventType(action.event_type ?? action.action_type ?? "civic.action.executed"),
    detail:
      action.reason ??
      action.output_payload?.error ??
      action.output_payload?.message ??
      "Governed Civic activity recorded.",
    meta: action.status ?? "captured"
  }));
  return [...rows, ...actionRows].slice(0, 6);
}

export default function MissionOutcomeView({
  mission,
  trace,
  diffState,
  usageSummary,
  selectedBid,
  latestProposalTrace,
  onOpenIntelligence,
  onOpenLiveMarket
}) {
  const outcomeSummary = mission.outcome_summary ?? {};
  const publicApiSurface = publicApiSurfaceFor(mission);
  const changedFiles = outcomeSummary.changed_files?.length
    ? outcomeSummary.changed_files
    : changedFilesFor(mission, diffState);
  const summary = outcomeSummary.plain_summary ?? summaryText(mission, selectedBid, latestProposalTrace);
  const validationPassed = Boolean(mission.validation_report?.passed);
  const confidence = Number(outcomeSummary.confidence ?? selectedBid?.confidence ?? 0);
  const totalCost = usageSummary?.mission?.total_cost ?? 0;
  const totalTokens = usageSummary?.mission?.total_tokens ?? 0;
  const risks = outcomeSummary.risks?.length ? outcomeSummary.risks : riskList(mission, selectedBid);
  const actions = outcomeSummary.next_actions?.length
    ? outcomeSummary.next_actions
    : nextActions(mission, changedFiles, validationPassed);
  const confidenceNarrative = outcomeSummary.confidence_reasons?.length
    ? outcomeSummary.confidence_reasons.join(" ")
    : confidenceSummary(selectedBid, mission);
  const civicImpact = civicImpactSummary(mission);
  const latestCheckpoint = mission.accepted_checkpoints?.at(-1) ?? null;
  const capabilityRows = capabilityInfluenceRows(mission);

  return (
    <div className="workspace-view workspace-outcome">
      <div className="outcome-layout">
        <section className={`panel outcome-hero outcome-hero-${outcomeTone(mission)}`}>
          <div className="outcome-hero-copy">
            <p className="eyebrow">Outcome</p>
            <h1>{summary}</h1>
            <p className="workspace-section-copy">
              Helix turns the mission into a review surface for the repo owner: what changed, how safe it looks, and what to do next.
            </p>
          </div>
          <div className="outcome-hero-meta">
            <article className="outcome-stat-card">
              <span>Mission outcome</span>
              <strong>{mission.outcome ?? humanizeMissionStage(mission.run_state)}</strong>
              <p>{repoLabel(mission.repo_path)}</p>
            </article>
            <article className="outcome-stat-card">
              <span>Confidence</span>
              <strong>{formatNumber(confidence * 100, 0)}%</strong>
              <p>{validationStats(mission.validation_report, outcomeSummary)}</p>
            </article>
            <article className="outcome-stat-card">
              <span>Spend</span>
              <strong>{formatCurrency(totalCost)}</strong>
              <p>{formatInteger(totalTokens)} tokens</p>
            </article>
            <article className="outcome-stat-card">
              <span>Branch</span>
              <strong>{mission.branch_name ?? "branch pending"}</strong>
              <p>{shortCommit(latestCheckpoint?.commit_sha ?? mission.head_commit)}</p>
            </article>
            <article className="outcome-stat-card">
              <span>Civic</span>
              <strong>{civicImpact.headline}</strong>
              <p>{civicImpact.detail || "No governed capability data surfaced yet."}</p>
            </article>
          </div>
          <div className="outcome-action-row">
            <button className="primary-button" type="button" onClick={() => onOpenIntelligence("diff")}>
              Review changes
            </button>
            <button className="ghost-button" type="button" onClick={() => onOpenIntelligence("validation")}>
              View validation report
            </button>
            <button className="ghost-button" type="button" onClick={() => onOpenIntelligence("history")}>
              Open mission history
            </button>
            <button className="ghost-button" type="button" onClick={onOpenLiveMarket}>
              Return to Live Market
            </button>
          </div>
        </section>

        <div className="outcome-grid">
          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>What Helix did</h2>
              <p>A plain-English path through the mission so the delivery flow is understandable without operator context.</p>
            </div>
            <div className="outcome-step-list">
              <article className="outcome-step">
                <strong>Scanned the repo and mission constraints</strong>
                <p>Initial repo understanding and mission framing established the governed boundary for work.</p>
              </article>
              <article className="outcome-step">
                <strong>Explored competing strategies</strong>
                <p>{selectedBid ? summarizeBidOrigin(selectedBid) : "Provider-backed strategy competition is still being established."}</p>
              </article>
              <article className="outcome-step">
                <strong>Checked governed capabilities</strong>
                <p>{civicImpact.detail || "Civic did not surface additional skills for this mission."}</p>
              </article>
              <article className="outcome-step">
                <strong>Selected and executed the bounded path</strong>
                <p>{latestProposalTrace?.payload?.summary ?? "The mission executed the strongest currently selected bounded work unit."}</p>
              </article>
              <article className="outcome-step">
                <strong>Validated and preserved the result</strong>
                <p>{validationStats(mission.validation_report, outcomeSummary)} with {mission.accepted_checkpoints?.length ?? 0} checkpoints available for review.</p>
              </article>
            </div>
          </section>

          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>Executive summary</h2>
              <p>The most important result signals stay visible here for fast decision-making.</p>
            </div>
            <div className="intelligence-card-grid">
              <article className="insight-card">
                <span>Files changed</span>
                <strong>{formatInteger(outcomeSummary.files_changed ?? changedFiles.length)}</strong>
                <p>{changedFiles.slice(0, 3).join(", ") || "No file changes captured yet."}</p>
              </article>
              <article className="insight-card">
                <span>Validation</span>
                <strong>{validationStats(mission.validation_report, outcomeSummary)}</strong>
                <p>{mission.validation_report?.notes?.join(" ") || outcomeSummary.validation_status || "Validation notes will appear here if captured."}</p>
              </article>
              <article className="insight-card">
                <span>Latest checkpoint</span>
                <strong>{latestCheckpoint?.label ?? "Pending"}</strong>
                <p>{latestCheckpoint ? shortCommit(latestCheckpoint.commit_sha) : "No accepted checkpoint yet."}</p>
              </article>
              <article className="insight-card">
                <span>Civic influence</span>
                <strong>{civicImpact.headline}</strong>
                <p>{mission.recent_civic_actions?.length ? `${mission.recent_civic_actions.length} governed actions recorded.` : "No governed actions were recorded in the mission snapshot."}</p>
              </article>
              <article className="insight-card">
                <span>Elapsed time</span>
                <strong>{formatRuntime(mission.runtime_seconds ?? 0)}</strong>
                <p>{mission.run_state === "finalized" ? "Mission finished." : "Mission still active."}</p>
              </article>
            </div>
          </section>

          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>Review-ready changes</h2>
              <p>Changed files and patch context stay easy to scan before dropping into a deeper diff view.</p>
            </div>
            <div className="review-file-list">
              {changedFiles.length ? (
                changedFiles.map((file) => (
                  <article key={file} className="review-file-card">
                    <strong>{file}</strong>
                    <p>{publicApiSurface.includes(file) ? "Touches declared public API surface." : "Internal implementation change."}</p>
                  </article>
                ))
              ) : (
                <div className="section-empty">No changed files available yet.</div>
              )}
            </div>
          </section>

          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>Risk and confidence</h2>
              <p>Trust signals, remaining uncertainty, and fallback context are kept explicit.</p>
            </div>
            <div className="risk-stack">
              <article className="confidence-card">
                <span>Confidence assessment</span>
                <strong>{formatNumber(confidence * 100, 0)}%</strong>
                <p>{confidenceNarrative}</p>
              </article>
              {risks.map((risk) => (
                <article key={risk} className="risk-item">
                  <strong>Review note</strong>
                  <p>{risk}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>Recommended next actions</h2>
              <p>Every mission should leave the user with a short, actionable follow-through list.</p>
            </div>
            <div className="next-action-list">
              {actions.map((action) => (
                <article key={action} className="next-action-item">
                  <strong>Next</strong>
                  <p>{action}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="panel outcome-panel">
            <div className="section-title">
              <h2>Governed capability influence</h2>
              <p>Only the external evidence that shaped the mission stays here, so the outcome view does not repeat the live timeline.</p>
            </div>
            <div className="ledger-list">
              {capabilityRows.length ? (
                capabilityRows.map((row) => (
                  <article key={row.id} className="ledger-row">
                    <div>
                      <strong>{row.title}</strong>
                      <p>{row.detail}</p>
                    </div>
                    <span>{row.meta}</span>
                  </article>
                ))
              ) : (
                <div className="section-empty">No governed capability influence has been recorded yet.</div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
