import React, { useMemo } from "react";

import { useBidRanking } from "../hooks/useBidRanking";
import {
  formatInteger,
  formatNumber,
  formatRuntime,
  formatUsageCost,
  humanizeEventType,
  humanizePhase,
  relativeTime,
  usageCostStatusDetail
} from "../lib/format";
import { useMissionElapsedSeconds } from "../lib/useMissionElapsed";
import "../styles/screens.css";

const TIMELINE_EVENT_TYPES = new Set([
  "repo.scan.completed",
  "strategy.landscape_generated",
  "strategy.market_opened",
  "bid.generated",
  "bid.rejected",
  "bid.won",
  "standby.selected",
  "simulation.completed",
  "proposal.selected",
  "validation.passed",
  "validation.failed",
  "mission.finalized"
]);

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? "Repo";
}

function planLabel(index, bid) {
  const letter = String.fromCharCode(65 + (index % 26));
  return `Plan ${letter}: ${bid.role ?? bid.strategy_family ?? "Untitled Plan"}`;
}

function scoreValue(bid) {
  const score = Number(bid.score ?? bid.search_diagnostics?.success_rate ?? bid.confidence ?? 0);
  return Math.round(score * 100);
}

function riskLabel(bid) {
  const risk = Number(bid.risk ?? bid.search_diagnostics?.rollback_rate ?? 0);
  if (risk >= 0.66) return "High";
  if (risk >= 0.33) return "Medium";
  return "Low";
}

function tokenUsageTotal(bid) {
  const usage = bid?.token_usage ?? {};
  if (usage.total_tokens !== undefined) {
    return Number(usage.total_tokens || 0);
  }
  return Object.values(usage).reduce((acc, value) => acc + Number(value || 0), 0);
}

function bidStatus(bid, winnerBidId, standbyBidId) {
  if (bid.bid_id === winnerBidId) {
    return { label: "Leading", tone: "leading" };
  }
  if (bid.bid_id === standbyBidId) {
    return { label: "Standby", tone: "standby" };
  }
  if (bid.rejection_reason) {
    return { label: "Blocked", tone: "blocked" };
  }
  return { label: "Competing", tone: "competing" };
}

function eventTone(eventType) {
  const value = String(eventType || "");
  if (value.includes("failed") || value.includes("blocked")) return "danger";
  if (value.includes("won") || value.includes("passed") || value.includes("completed")) return "success";
  if (value.includes("selected")) return "accent";
  return "neutral";
}

function timelineLabel(entry) {
  const payload = entry.payload ?? {};
  if (payload.summary) {
    return payload.summary;
  }
  if (entry.message) {
    return entry.message;
  }
  return humanizeEventType(entry.event_type);
}

function compactSkillRows(mission) {
  const skills = mission?.available_skills ?? [];
  const preferred = ["github_context", "knowledge_context"];
  const seen = new Set();
  const ordered = [...preferred, ...skills].filter((value) => {
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
  return ordered.slice(0, 4).map((skill) => ({
    name: skill.replace(/_/g, " "),
    available: skills.includes(skill)
  }));
}

function reviewedBidEnvelopes(mission, bids) {
  const byBid = new Map((mission?.governed_bid_envelopes ?? []).map((item) => [item.bid_id, item]));
  return bids
    .filter((bid) => byBid.has(bid.bid_id))
    .slice(0, 3)
    .map((bid) => ({ bid, envelope: byBid.get(bid.bid_id) }));
}

function researchContextSummary(mission) {
  const knowledge = mission?.skill_outputs?.knowledge_context;
  if (!knowledge || typeof knowledge !== "object") {
    return null;
  }
  const summary = String(knowledge.summary ?? "").trim();
  const queries = Array.isArray(knowledge.queries) ? knowledge.queries.filter(Boolean) : [];
  const sourceUrls = Array.isArray(knowledge.source_urls) ? knowledge.source_urls.filter(Boolean) : [];
  return {
    summary,
    queries,
    sourceUrls,
    trusted: Boolean(knowledge?.provenance?.trusted)
  };
}

function winnerEnvelopeRows(mission, winnerBidId, reviewedBids, research) {
  const envelope = (mission?.governed_bid_envelopes ?? []).find(
    (item) => item.bid_id === winnerBidId
  );
  const policyState = envelope?.status ?? envelope?.policy_decision ?? "approved";
  const constraints = Array.isArray(envelope?.constraints) ? envelope.constraints : [];
  const reviewTarget = Math.min(3, Math.max(reviewedBids.length, (mission?.bids ?? []).length));
  return [
    {
      label: "Top 3 reviewed",
      state: reviewedBids.length > 0 ? "ok" : "warn",
      detail:
        reviewTarget > 0
          ? `${formatInteger(reviewedBids.length)} of ${formatInteger(reviewTarget)} bids screened by Civic`
          : "Waiting for the first review set"
    },
    {
      label: "Winner status",
      state: !String(policyState).includes("block") ? "ok" : "danger",
      detail: String(policyState).replace(/[_-]/g, " ")
    },
    {
      label: "Winner constraints",
      state: !constraints.some((item) => String(item).includes("missing_") || String(item).includes("unavailable"))
        ? "ok"
        : "warn",
      detail: constraints.length ? constraints.join(", ") : "No extra constraints"
    },
    {
      label: "Research",
      state: research ? "ok" : "neutral",
      detail: research
        ? `${formatInteger(research.sourceUrls.length)} sources across ${formatInteger(research.queries.length)} queries`
        : "No governed research used"
    }
  ];
}

function policyImpactRows(bids, mission, reviewedBids, research) {
  const blocked = bids.filter((bid) => Boolean(bid.rejection_reason));
  const rows = blocked.slice(0, 3).map((bid) => ({
    tone: "danger",
    text: `${bid.role ?? bid.strategy_family} blocked: ${bid.rejection_reason}`
  }));
  if (reviewedBids.length) {
    rows.push({
      tone: "accent",
      text: `Civic reviewed ${reviewedBids.length} of the top ${Math.min(3, bids.length)} bids before selection`
    });
  }
  if (mission?.winner_bid_id) {
    rows.push({
      tone: "neutral",
      text: "Winner chosen with governed context"
    });
  }
  if (research?.summary) {
    rows.push({
      tone: research.trusted ? "success" : "neutral",
      text: `Governed research shaped the prompts: ${research.summary}`
    });
  }
  if (!rows.length) {
    rows.push({
      tone: "success",
      text: "No policy blocks detected in this round"
    });
  }
  return rows;
}

export default React.memo(function StrategyBiddingScreen({
  mission,
  winnerBidId,
  standbyBidId,
  activePhase,
  usageSummary
}) {
  const elapsedSeconds = useMissionElapsedSeconds(mission);
  const rankedBids = useBidRanking(mission);
  const missionUsage = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const missionTokens = Number(missionUsage.total_tokens ?? 0);
  const spendDetail = usageCostStatusDetail(missionUsage);
  const topEvents = useMemo(
    () =>
      [...(mission?.events ?? [])]
        .filter((entry) => TIMELINE_EVENT_TYPES.has(entry.event_type))
        .slice(-7),
    [mission?.events]
  );
  const civicStatus = String(mission?.civic_connection?.status ?? "idle").replace(/[_-]/g, " ");
  const skillRows = compactSkillRows(mission);
  const envelopeIndex = useMemo(
    () => new Map((mission?.governed_bid_envelopes ?? []).map((item) => [item.bid_id, item])),
    [mission?.governed_bid_envelopes]
  );
  const reviewedBids = useMemo(() => reviewedBidEnvelopes(mission, rankedBids), [mission, rankedBids]);
  const research = useMemo(() => researchContextSummary(mission), [mission]);
  const envelopeRows = winnerEnvelopeRows(mission, winnerBidId, reviewedBids, research);
  const policyRows = policyImpactRows(rankedBids, mission, reviewedBids, research);
  const topbarItems = [
    { label: "Repo", value: repoLabel(mission?.repo_path) },
    { label: "Objective", value: mission?.objective ?? "Mission objective pending" },
    { label: "Status", value: String(mission?.run_state ?? "idle").replace(/[_-]/g, " ") },
    { label: "Phase", value: humanizePhase(activePhase) },
    { label: "Elapsed", value: formatRuntime(elapsedSeconds) },
    { label: "Spend", value: formatUsageCost(missionUsage) },
    { label: "Tokens", value: formatInteger(missionTokens) },
    spendDetail ? { label: "Billing", value: spendDetail } : null
  ].filter(Boolean);

  return (
    <div className="workspace-view screen-ref screen-ref-one">
      <section className="panel screen-ref-topbar">
        <div className="screen-ref-topbar-items">
          {topbarItems.map((item) => (
            <div key={item.label} className="screen-ref-topbar-item">
              <span className="screen-ref-topbar-label">{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
        <div className="screen-ref-topbar-actions" aria-hidden="true">
          <span className="screen-ref-action-chip">Pause</span>
          <span className="screen-ref-action-chip screen-ref-action-chip-danger">Cancel</span>
        </div>
      </section>

      <div className="screen-ref-one-grid">
        <section className="panel screen-ref-main">
          <div className="screen-ref-main-head">
            <div className="section-title">
              <p className="eyebrow">Screen 1</p>
              <h2>Strategy Bidding Board</h2>
            </div>
            <div className="screen-ref-view-switch" aria-hidden="true">
              <span className="screen-ref-view-pill">List</span>
              <span className="screen-ref-view-pill is-active">Board</span>
            </div>
          </div>

          <div className="screen-ref-bid-list">
            {rankedBids.length ? (
              rankedBids.map((bid, index) => {
                const status = bidStatus(bid, winnerBidId, standbyBidId);
                const tokens = tokenUsageTotal(bid);
                const confidence = Math.round(Number(bid.confidence ?? 0) * 100);
                const envelope = envelopeIndex.get(bid.bid_id);
                const envelopeConstraints = Array.isArray(envelope?.constraints)
                  ? envelope.constraints.slice(0, 2)
                  : [];

                return (
                  <article
                    key={bid.bid_id}
                    className={`screen-ref-bid-card is-${status.tone}`}
                  >
                    <div className="screen-ref-bid-head">
                      <div className="screen-ref-bid-title">
                        <strong>{planLabel(index, bid)}</strong>
                        <span className={`screen-ref-status-pill is-${status.tone}`}>{status.label}</span>
                      </div>
                      <span className="screen-ref-bid-provider">
                        {bid.provider ?? "provider"} {bid.model_id ? `| ${bid.model_id}` : ""}
                      </span>
                    </div>

                    <div className="screen-ref-chip-row">
                      <span className="screen-ref-data-chip">Value {scoreValue(bid)}</span>
                      <span className="screen-ref-data-chip">Risk {riskLabel(bid)}</span>
                      <span className="screen-ref-data-chip">Cost {formatInteger(tokens)} tokens</span>
                      <span className="screen-ref-data-chip">Confidence {formatNumber(confidence, 0)}%</span>
                    </div>

                    <p className="screen-ref-bid-copy">
                      {bid.rejection_reason
                        ? bid.rejection_reason
                        : bid.search_summary ?? bid.mission_rationale ?? bid.strategy_summary ?? "Competing in the current round."}
                    </p>

                    {envelope ? (
                      <p className="screen-ref-bid-subcopy">
                        Civic review: {String(envelope.status ?? "approved").replace(/[_-]/g, " ")}
                        {envelopeConstraints.length ? ` | Constraints: ${envelopeConstraints.join(", ")}` : " | No extra constraints"}
                      </p>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <div className="section-empty">Bids are still being generated.</div>
            )}
          </div>
        </section>

        <aside className="screen-ref-side-stack">
          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Usage Signal</h2>
            </div>
            <p className="screen-ref-inline-copy">Spend: {formatUsageCost(missionUsage)}</p>
            {spendDetail ? <p className="screen-ref-inline-copy">{spendDetail}</p> : null}
            <div className="screen-ref-kv-list">
              <div className="screen-ref-kv-row">
                <span>Mission spend</span>
                <strong>{formatUsageCost(missionUsage)}</strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Total tokens</span>
                <strong>{formatInteger(missionTokens)}</strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Research signals</span>
                <strong>{research ? formatInteger(research.sourceUrls.length) : "0"}</strong>
              </div>
              {spendDetail ? (
                <div className="screen-ref-kv-row">
                  <span>Billing detail</span>
                  <strong>{spendDetail}</strong>
                </div>
              ) : null}
            </div>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Civic &amp; Policy Panel</h2>
            </div>
            <p className="screen-ref-inline-copy">
              Top 3 Civic Review: {formatInteger(reviewedBids.length)}
            </p>
            <div className="screen-ref-kv-list">
              <div className="screen-ref-kv-row">
                <span>Civic status</span>
                <strong>{civicStatus}</strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Toolkit</span>
                <strong>{mission?.civic_connection?.toolkit_id ?? "GitHub-Civic"}</strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Last check</span>
                <strong>
                  {mission?.civic_connection?.last_checked_at
                    ? relativeTime(mission.civic_connection.last_checked_at)
                    : "waiting"}
                </strong>
              </div>
              <div className="screen-ref-kv-row">
                <span>Reviewed this round</span>
                <strong>{formatInteger(reviewedBids.length)}</strong>
              </div>
            </div>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Active Skills</h2>
            </div>
            <div className="screen-ref-status-list">
              {skillRows.length ? (
                skillRows.map((skill) => (
                  <div key={skill.name} className={`screen-ref-status-row ${skill.available ? "is-ok" : "is-danger"}`}>
                    <span className="screen-ref-status-dot" />
                    <span>{skill.name}</span>
                    <strong>{skill.available ? "Active" : "Unavailable"}</strong>
                  </div>
                ))
              ) : (
                <div className="section-empty">No skills loaded yet.</div>
              )}
            </div>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Winner Envelope</h2>
            </div>
            <div className="screen-ref-status-list">
              {envelopeRows.map((item) => (
                <div key={item.label} className={`screen-ref-status-row ${item.state ? `is-${item.state}` : ""}`}>
                  <span className="screen-ref-status-dot" />
                  <span>{item.label}</span>
                  <strong>{item.detail}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="panel screen-ref-side-panel">
            <div className="section-title">
              <h2>Policy Impact</h2>
            </div>
            <div className="screen-ref-impact-list">
              {policyRows.map((row) => (
                <article key={row.text} className={`screen-ref-impact-row is-${row.tone}`}>
                  <span className="screen-ref-status-dot" />
                  <p>{row.text}</p>
                </article>
              ))}
            </div>
          </section>
        </aside>
      </div>

      <section className="panel screen-ref-timeline console-timeline-strip">
        <div className="section-title">
          <p className="eyebrow">Live Event Timeline</p>
          <h2>Realtime mission events</h2>
        </div>
        <div className="screen-ref-timeline-row">
          {topEvents.length ? (
            topEvents.map((entry) => (
              <article
                key={`${entry.event_type}-${entry.id}`}
                className={`screen-ref-timeline-chip is-${eventTone(entry.event_type)}`}
              >
                <strong>{timelineLabel(entry)}</strong>
                <span>{humanizeEventType(entry.event_type)}</span>
              </article>
            ))
          ) : (
            <div className="section-empty">Live timeline updates will appear here.</div>
          )}
        </div>
      </section>
    </div>
  );
});
