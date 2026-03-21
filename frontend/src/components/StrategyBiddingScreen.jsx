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
    return { label: "LEADING", tone: "leading" };
  }
  if (bid.bid_id === standbyBidId) {
    return { label: "STANDBY", tone: "standby" };
  }
  if (bid.rejection_reason) {
    return { label: "BLOCKED", tone: "blocked" };
  }
  return { label: "COMPETING", tone: "competing" };
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

function winnerEnvelopeRows(mission, winnerBidId, totalTokens, budgetCap) {
  const envelope = (mission?.governed_bid_envelopes ?? []).find(
    (item) => item.bid_id === winnerBidId
  );
  const policyState = envelope?.status ?? envelope?.policy_decision ?? "approved";
  return [
    {
      label: "Read Access",
      state: (mission?.available_skills ?? []).includes("github_context"),
      detail: (mission?.available_skills ?? []).includes("github_context") ? "Granted" : "Not available"
    },
    {
      label: "Budget Cap",
      state: totalTokens <= budgetCap,
      detail: `${formatInteger(totalTokens)} / ${formatInteger(budgetCap)} tokens`
    },
    {
      label: "Policy",
      state: !String(policyState).includes("block"),
      detail: String(policyState).replace(/[_-]/g, " ")
    }
  ];
}

function policyImpactRows(bids, mission) {
  const blocked = bids.filter((bid) => Boolean(bid.rejection_reason));
  const rows = blocked.slice(0, 3).map((bid) => ({
    tone: "danger",
    text: `${bid.role ?? bid.strategy_family} blocked: ${bid.rejection_reason}`
  }));
  if (mission?.winner_bid_id) {
    rows.push({
      tone: "neutral",
      text: "Winner chosen with governed context"
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
  const budgetCap =
    Number(mission?.mission_meta?.token_budget ?? 0) || Math.max(500, missionTokens + 200);
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
  const envelopeRows = winnerEnvelopeRows(mission, winnerBidId, missionTokens, budgetCap);
  const policyRows = policyImpactRows(rankedBids, mission);

  return (
    <section className="console-screen console-screen-bidding panel">
      <header className="console-topbar">
        <div className="console-topbar-group">
          <span>Repo: {repoLabel(mission?.repo_path)}</span>
          <span>Objective: {mission?.objective ?? "Mission objective pending"}</span>
          <span>Status: {String(mission?.run_state ?? "idle")}</span>
          <span>Phase: {humanizePhase(activePhase)}</span>
          <span>Elapsed: {formatRuntime(elapsedSeconds)}</span>
          <span>
            Budget: {formatInteger(missionTokens)} / {formatInteger(budgetCap)} tokens
          </span>
          <span>Spend: {formatUsageCost(missionUsage)}</span>
          {spendDetail ? <span>{spendDetail}</span> : null}
        </div>
        <div className="console-topbar-controls" aria-hidden="true">
          <button type="button" className="console-control-button" disabled tabIndex={-1}>
            Pause
          </button>
          <button
            type="button"
            className="console-control-button console-control-danger"
            disabled
            tabIndex={-1}
          >
            Cancel
          </button>
        </div>
      </header>

      <div className="console-bidding-grid">
        <section className="console-panel-frame">
          <div className="console-panel-header console-panel-header-with-tools">
            <h2>Strategy Bidding Board</h2>
            <div className="console-header-tools" aria-hidden="true">
              <span>III</span>
              <span>II</span>
            </div>
          </div>
          <div className="console-bid-list">
            {rankedBids.length ? (
              rankedBids.map((bid, index) => {
                const status = bidStatus(bid, winnerBidId, standbyBidId);
                const tokens = tokenUsageTotal(bid);
                const confidence = Math.round(Number(bid.confidence ?? 0) * 100);
                return (
                  <article key={bid.bid_id} className={`console-bid-row tone-${status.tone}`}>
                    <div className="console-bid-head">
                      <div className="console-bid-title">
                        <strong>{planLabel(index, bid)}</strong>
                        <span className={`console-state-badge tone-${status.tone}`}>{status.label}</span>
                      </div>
                      <div className="console-bid-head-right">
                        <span className="console-bid-meta">
                          {bid.provider ?? "provider"} {bid.model_id ? `| ${bid.model_id}` : ""}
                        </span>
                        <span className="console-bid-menu">...</span>
                      </div>
                    </div>
                    <div className="console-bid-stats">
                      <span>
                        Value: <strong>{scoreValue(bid)}</strong>
                      </span>
                      <span>Risk: {riskLabel(bid)}</span>
                      <span>Cost: {formatInteger(tokens)} tokens</span>
                      <span>Confidence: {formatNumber(confidence, 0)}%</span>
                    </div>
                    {bid.rejection_reason ? (
                      <p className="console-bid-note">{bid.rejection_reason}</p>
                    ) : (
                      <p className="console-bid-note">
                        {bid.search_summary ?? bid.mission_rationale ?? bid.strategy_summary ?? "Competing in current round."}
                      </p>
                    )}
                    {status.tone === "blocked" ? (
                      <button type="button" className="console-inline-detail" disabled>
                        Details
                      </button>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <div className="console-empty">Bids are still being generated.</div>
            )}
          </div>
        </section>

        <aside className="console-side-stack">
          <section className="console-panel-frame">
            <div className="console-panel-header">
              <h2>Usage Signal</h2>
            </div>
            <div className="console-kv-list">
              <div className="console-kv-row">
                <span>Mission Spend</span>
                <strong>{formatUsageCost(missionUsage)}</strong>
              </div>
              <div className="console-kv-row">
                <span>Total Tokens</span>
                <strong>{formatInteger(missionTokens)}</strong>
              </div>
              {spendDetail ? (
                <div className="console-kv-row">
                  <span>Billing Detail</span>
                  <strong>{spendDetail}</strong>
                </div>
              ) : null}
            </div>
          </section>

          <section className="console-panel-frame">
            <div className="console-panel-header">
              <h2>Civic &amp; Policy Panel</h2>
            </div>
            <div className="console-kv-list">
              <div className="console-kv-row">
                <span>Civic Status</span>
                <strong>{civicStatus}</strong>
              </div>
              <div className="console-kv-row">
                <span>Toolkit</span>
                <strong>{mission?.civic_connection?.toolkit_id ?? "GitHub-Civic"}</strong>
              </div>
              <div className="console-kv-row">
                <span>Last Check</span>
                <strong>
                  {mission?.civic_connection?.last_checked_at
                    ? relativeTime(mission.civic_connection.last_checked_at)
                    : "waiting"}
                </strong>
              </div>
            </div>
          </section>

          <section className="console-panel-frame">
            <div className="console-panel-header">
              <h2>Active Skills</h2>
            </div>
            <div className="console-status-list">
              {skillRows.length ? (
                skillRows.map((skill) => (
                  <div key={skill.name} className={`console-status-row ${skill.available ? "ok" : "warn"}`}>
                    <span className="console-status-dot" />
                    <span>{skill.name}</span>
                    <strong>{skill.available ? "Active" : "Unavailable"}</strong>
                  </div>
                ))
              ) : (
                <div className="console-empty">No skills loaded yet.</div>
              )}
            </div>
          </section>

          <section className="console-panel-frame">
            <div className="console-panel-header">
              <h2>Winner Envelope</h2>
            </div>
            <div className="console-status-list">
              {envelopeRows.map((item) => (
                <div key={item.label} className={`console-status-row ${item.state ? "ok" : "warn"}`}>
                  <span className="console-status-dot" />
                  <span>{item.label}</span>
                  <strong>{item.detail}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="console-panel-frame">
            <div className="console-panel-header">
              <h2>Policy Impact</h2>
            </div>
            <div className="console-impact-list">
              {policyRows.map((row) => (
                <div key={row.text} className={`console-impact-row tone-${row.tone}`}>
                  <span className="console-status-dot" />
                  <p>{row.text}</p>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>

      <footer className="console-timeline-strip">
        <h3>
          <span className="console-timeline-prefix">- -</span> Live Event Timeline
        </h3>
        <div className="console-event-chip-row">
          {topEvents.length ? (
            topEvents.map((entry) => (
              <div key={`${entry.event_type}-${entry.id}`} className={`console-event-chip tone-${eventTone(entry.event_type)}`}>
                <span>{timelineLabel(entry)}</span>
              </div>
            ))
          ) : (
            <div className="console-empty">Live timeline updates will appear here.</div>
          )}
        </div>
      </footer>
    </section>
  );
});
