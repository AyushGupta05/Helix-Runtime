import React, { useMemo } from "react";

import { useBidRanking } from "../hooks/useBidRanking";
import MonteCarloPanel from "./MonteCarloPanel";
import {
  formatInteger,
  formatNumber,
  formatUsageCost,
  humanizePhase,
  usageCostStatusDetail
} from "../lib/format";
import "../styles/screens.css";

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? "Repo";
}

function strategyLabel(index, bid) {
  const letter = String.fromCharCode(65 + (index % 26));
  return `Plan ${letter}: ${bid.role ?? bid.strategy_family ?? "Untitled"}`;
}

function scoreMean(bid) {
  const value = Number(bid.search_diagnostics?.success_rate ?? bid.score ?? bid.confidence ?? 0);
  return Math.round(value * 100);
}

function spreadRange(bid) {
  const rollback = Number(bid.search_diagnostics?.rollback_rate ?? bid.risk ?? 0.2);
  const policy = Number(bid.policy_friction_score ?? 0.12);
  const spread = (rollback * 0.7 + policy * 0.45) * 100;
  return Math.max(6, Math.round(spread));
}

function estimateSamples(bid, simulationSummary) {
  const explicit = Number(bid.search_diagnostics?.sample_count ?? 0);
  if (explicit > 0) return explicit;
  const missionSamples = Number(simulationSummary?.monte_carlo_samples ?? 0);
  if (missionSamples > 0) return missionSamples;
  return Math.max(10, Math.round(180 / Math.max(30, Number(bid.estimated_runtime_seconds ?? 90))));
}

function tokenTotal(bid) {
  const usage = bid?.token_usage ?? {};
  if (usage.total_tokens !== undefined) return Number(usage.total_tokens || 0);
  return Object.values(usage).reduce((acc, value) => acc + Number(value || 0), 0);
}

function winnerEnvelope(mission, winnerBidId) {
  return (mission?.governed_bid_envelopes ?? []).find((item) => item.bid_id === winnerBidId) ?? null;
}

function reviewedBidCount(mission, bids) {
  const reviewed = new Set((mission?.governed_bid_envelopes ?? []).map((item) => item.bid_id));
  return bids.filter((bid) => reviewed.has(bid.bid_id)).slice(0, 3).length;
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

function winnerInsights(bids, winnerBidId) {
  const active = bids.filter((bid) => !bid.rejection_reason);
  const winner = active.find((bid) => bid.bid_id === winnerBidId) ?? active[0] ?? null;
  if (!winner) {
    return [];
  }
  const highestUpside = [...active].sort((a, b) => scoreMean(b) - scoreMean(a))[0];
  const safest = [...active].sort(
    (a, b) =>
      Number(a.search_diagnostics?.rollback_rate ?? a.risk ?? 1) -
      Number(b.search_diagnostics?.rollback_rate ?? b.risk ?? 1)
  )[0];
  const cheapest = [...active].sort((a, b) => tokenTotal(a) - tokenTotal(b))[0];
  const fragile = [...active].sort(
    (a, b) =>
      Number(b.search_diagnostics?.rollback_rate ?? b.risk ?? 0) -
      Number(a.search_diagnostics?.rollback_rate ?? a.risk ?? 0)
  )[0];
  return [
    { label: "Highest upside", value: highestUpside?.role ?? highestUpside?.strategy_family },
    {
      label: "Lowest failure risk",
      value: `${safest?.role ?? safest?.strategy_family} (${Math.round(
        Number(safest?.search_diagnostics?.rollback_rate ?? safest?.risk ?? 0) * 100
      )}%)`
    },
    {
      label: "Cheapest path",
      value: `${cheapest?.role ?? cheapest?.strategy_family} (${formatInteger(tokenTotal(cheapest))} tok)`
    },
    { label: "Most fragile strategy", value: fragile?.role ?? fragile?.strategy_family }
  ];
}

export default React.memo(function SimulationIntelligenceScreen({
  mission,
  winnerBidId,
  activePhase,
  usageSummary
}) {
  const rankedBids = useBidRanking(mission, 4);
  const simulationSummary = mission?.simulation_summary ?? {};
  const missionUsage = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const spendDetail = usageCostStatusDetail(missionUsage);
  const totalSamples =
    Number(simulationSummary.monte_carlo_samples ?? 0) ||
    rankedBids.reduce((acc, bid) => acc + estimateSamples(bid, simulationSummary), 0);
  const expectedMission = Math.max(0, ...rankedBids.map((bid) => scoreMean(bid)));
  const winner =
    rankedBids.find((bid) => bid.bid_id === winnerBidId) ?? rankedBids[0] ?? null;
  const insightRows = useMemo(() => winnerInsights(rankedBids, winnerBidId), [rankedBids, winnerBidId]);
  const governanceImpact = rankedBids.filter((bid) => Boolean(bid.rejection_reason)).length;
  const civicReviewed = reviewedBidCount(mission, rankedBids);
  const envelope = winnerEnvelope(mission, winnerBidId);
  const research = researchContextSummary(mission);
  const explanation =
    winner?.search_summary ??
    winner?.mission_rationale ??
    "Winning strategy has the best risk-adjusted expected value.";
  const topbarItems = [
    { label: "Repo", value: repoLabel(mission?.repo_path) },
    { label: "Objective", value: mission?.objective ?? "Mission objective pending" },
    { label: "Strategies simulated", value: formatInteger(rankedBids.length) },
    { label: "Monte Carlo depth", value: formatInteger(totalSamples) },
    { label: "Current winner", value: winner?.role ?? winner?.strategy_family ?? "Pending" },
    { label: "Expected mission", value: formatInteger(expectedMission) },
    { label: "Phase", value: humanizePhase(activePhase) },
    { label: "Spend", value: formatUsageCost(missionUsage) },
    spendDetail ? { label: "Billing", value: spendDetail } : null
  ].filter(Boolean);

  return (
    <div className="workspace-view screen-ref screen-ref-two">
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

      <div className="screen-ref-two-grid">
        <aside className="panel screen-ref-sim-sidebar">
          <div className="section-title">
            <p className="eyebrow">Screen 2</p>
            <h2>Strategy Simulation</h2>
          </div>

          <div className="screen-ref-inline-toolbar">
            <span className="screen-ref-action-chip is-active">Dynamic graph</span>
            <span className="screen-ref-action-chip">Strategy list</span>
            <span className="screen-ref-action-chip">Reanalyze</span>
          </div>

          <div className="screen-ref-sim-list">
            {rankedBids.length ? (
              rankedBids.map((bid, index) => {
                const mean = scoreMean(bid);
                const spread = spreadRange(bid);
                const winnerLabel = bid.bid_id === winnerBidId ? "Winner" : "Simulated";
                return (
                  <article key={bid.bid_id} className="screen-ref-sim-card">
                    <div className="screen-ref-sim-card-head">
                      <strong>{strategyLabel(index, bid)}</strong>
                      <span>{winnerLabel}</span>
                    </div>
                    <div className="screen-ref-sim-bar">
                      <span style={{ width: `${Math.max(12, Math.min(100, mean))}%` }} />
                    </div>
                    <div className="screen-ref-chip-row">
                      <span className="screen-ref-data-chip">Mean {formatInteger(mean)}</span>
                      <span className="screen-ref-data-chip">+/- {formatInteger(spread)}</span>
                      <span className="screen-ref-data-chip">
                        Samples {formatInteger(estimateSamples(bid, simulationSummary))}
                      </span>
                    </div>
                  </article>
                );
              })
            ) : (
              <div className="section-empty">No simulation candidates yet.</div>
            )}
          </div>

          <div className="screen-ref-chip-row">
            <span className="screen-ref-data-chip">Pin</span>
            <span className="screen-ref-data-chip">Raw</span>
            <span className="screen-ref-data-chip">Compare</span>
          </div>
        </aside>

        <div className="screen-ref-sim-center-stack">
          <MonteCarloPanel mission={mission} bids={rankedBids} winnerBidId={winnerBidId} />

          <section className="panel screen-ref-explanation-panel">
            <div className="section-title">
              <h2>Simulation Explanation Strip</h2>
            </div>
            <div className="screen-ref-note-stack">
              <article className="screen-ref-note-card">
                <strong>Why the winner leads</strong>
                <p>{explanation}</p>
              </article>
              <article className="screen-ref-note-card">
                <strong>Frontier signal</strong>
                <p>
                  Frontier gap {formatNumber(simulationSummary.frontier_gap ?? 0, 3)} with search mode{" "}
                  {String(simulationSummary.search_mode ?? "bounded_monte_carlo").replace(/_/g, " ")}.
                </p>
              </article>
            </div>
          </section>
        </div>

        <aside className="panel screen-ref-sim-insights">
          <div className="section-title">
            <h2>Simulation Insights</h2>
          </div>

          <article className="screen-ref-highlight-card">
            <span className="screen-ref-topbar-label">Winner</span>
            <strong>{winner?.role ?? winner?.strategy_family ?? "Pending"}</strong>
            <p>Highest risk-adjusted reward</p>
          </article>

          <div className="screen-ref-insight-list">
            {insightRows.length ? (
              insightRows.map((row) => (
                <article key={row.label} className="screen-ref-insight-item">
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                </article>
              ))
            ) : (
              <div className="section-empty">Insights will populate after simulation.</div>
            )}
          </div>

          <article className="screen-ref-note-card">
            <strong>Best governed</strong>
            <p>
              {winner?.role ?? winner?.strategy_family ?? "Pending"}{" "}
              {envelope
                ? `advanced with envelope ${String(envelope.status ?? envelope.policy_decision ?? "approved").replace(/[_-]/g, " ")}.`
                : "is still awaiting a governed envelope."}
            </p>
          </article>

          <article className="screen-ref-note-card">
            <strong>Governance influence</strong>
            <p>
              Civic constraints clipped {formatInteger(governanceImpact)} candidate
              {governanceImpact === 1 ? "" : "s"} in this cycle.
            </p>
            <p>
              Civic reviewed {formatInteger(civicReviewed)} of the top {formatInteger(Math.min(3, rankedBids.length))} bids
              before the winner advanced.
            </p>
            <p>
              {research
                ? `${research.trusted ? "Trusted" : "Governed"} research informed the prompt set with ${formatInteger(
                    research.sourceUrls.length
                  )} sources.`
                : "No governed research was needed for this cycle."}
            </p>
          </article>
        </aside>
      </div>
    </div>
  );
});
