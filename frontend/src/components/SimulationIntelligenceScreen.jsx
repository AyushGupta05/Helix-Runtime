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
    { label: "Highest Upside", value: highestUpside?.role ?? highestUpside?.strategy_family },
    {
      label: "Lowest Failure Risk",
      value: `${safest?.role ?? safest?.strategy_family} (${Math.round(
        Number(safest?.search_diagnostics?.rollback_rate ?? safest?.risk ?? 0) * 100
      )}%)`
    },
    {
      label: "Cheapest Path",
      value: `${cheapest?.role ?? cheapest?.strategy_family} (${formatInteger(tokenTotal(cheapest))} tok)`
    },
    { label: "Most Fragile Strategy", value: fragile?.role ?? fragile?.strategy_family }
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
  const explanation =
    winner?.search_summary ??
    winner?.mission_rationale ??
    "Winning strategy has the best risk-adjusted expected value.";

  return (
    <section className="console-screen console-screen-simulation panel">
      <header className="console-topbar console-topbar-sim">
        <div className="console-topbar-group">
          <span>Repo: {repoLabel(mission?.repo_path)}</span>
          <span>Objective: {mission?.objective ?? "Mission objective pending"}</span>
          <span>Strategies Simulated: {formatInteger(rankedBids.length)}</span>
          <span>Monte Carlo Depth: {formatInteger(totalSamples)}</span>
          <span>Current Winner: {winner?.role ?? winner?.strategy_family ?? "Pending"}</span>
          <span>Current Expected Mission: {formatInteger(expectedMission)}</span>
          <span>Spend: {formatUsageCost(missionUsage)}</span>
          {spendDetail ? <span>{spendDetail}</span> : null}
        </div>
      </header>

      <div className="console-simulation-grid">
        <aside className="console-sim-sidebar">
          <div className="console-sim-sidebar-head">
            <h2>Strategy Simulation</h2>
            <span>{humanizePhase(activePhase)}</span>
          </div>
          <div className="console-sim-strategy-list">
            {rankedBids.length ? (
              rankedBids.map((bid, index) => {
                const mean = scoreMean(bid);
                const spread = spreadRange(bid);
                const markerWidth = `${Math.max(12, Math.min(100, mean))}%`;
                return (
                  <article key={bid.bid_id} className="console-sim-strategy-row">
                    <div className="console-sim-strategy-head">
                      <strong>{strategyLabel(index, bid)}</strong>
                      <span>{bid.bid_id === winnerBidId ? "WINNER" : "SIMULATED"}</span>
                    </div>
                    <div className="console-sim-barline">
                      <span style={{ width: markerWidth }} />
                    </div>
                    <div className="console-sim-strategy-meta">
                      <span>Mean outcome {formatInteger(mean)}</span>
                      <span>+/- {formatInteger(spread)}</span>
                    </div>
                  </article>
                );
              })
            ) : (
              <div className="console-empty">No simulation candidates yet.</div>
            )}
          </div>
        </aside>

        <div className="console-sim-center">
          <MonteCarloPanel mission={mission} bids={rankedBids} winnerBidId={winnerBidId} />
        </div>

        <aside className="console-sim-insights">
          <div className="console-panel-header">
            <h2>Simulation Insights</h2>
          </div>
          <div className="console-sim-winner">
            <span>Winner</span>
            <strong>{winner?.role ?? winner?.strategy_family ?? "Pending"}</strong>
            <p>Highest risk-adjusted reward</p>
          </div>
          <div className="console-sim-insight-list">
            {insightRows.length ? (
              insightRows.map((row) => (
                <article key={row.label} className="console-sim-insight-item">
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                </article>
              ))
            ) : (
              <div className="console-empty">Insights will populate after simulation.</div>
            )}
          </div>
          <div className="console-sim-governance">
            <strong>Governance Influence</strong>
            <p>
              Civic constraints clipped {formatInteger(governanceImpact)} candidate
              {governanceImpact === 1 ? "" : "s"} in this cycle.
            </p>
          </div>
        </aside>
      </div>

      <footer className="console-explanation-strip">
        <h3>Simulation Explanation Strip</h3>
        <p>{explanation}</p>
        <p>
          Frontier gap {formatNumber(simulationSummary.frontier_gap ?? 0, 3)} with search mode{" "}
          {String(simulationSummary.search_mode ?? "bounded_monte_carlo").replace(/_/g, " ")}.
        </p>
      </footer>
    </section>
  );
});
