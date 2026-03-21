import { formatInteger, formatNumber } from "../lib/format";

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, Number(value ?? 0)));
}

function rankedBids(mission, selectedBid) {
  const bids = mission?.bids ?? [];
  const winnerId = mission?.winner_bid_id ?? selectedBid?.bid_id ?? null;
  return [...bids]
    .sort((left, right) => {
      const leftRank = left.bid_id === winnerId ? 1 : 0;
      const rightRank = right.bid_id === winnerId ? 1 : 0;
      if (leftRank !== rightRank) {
        return rightRank - leftRank;
      }
      return (
        clamp(right.search_diagnostics?.success_rate ?? right.score ?? right.confidence, -1, 2) -
        clamp(left.search_diagnostics?.success_rate ?? left.score ?? left.confidence, -1, 2)
      );
    })
    .slice(0, 4);
}

function diagnosticsFor(bid) {
  const diagnostics = bid?.search_diagnostics ?? {};
  const success = clamp(diagnostics.success_rate ?? bid?.score ?? bid?.confidence ?? 0.45);
  const rollback = clamp(diagnostics.rollback_rate ?? bid?.risk ?? 0.18);
  const policy = clamp(
    diagnostics.policy_friction_cost ??
      bid?.policy_friction_score ??
      bid?.capability_reliance_score ??
      0.12
  );
  const capability = clamp(
    diagnostics.capability_availability_probability ??
      1 - (bid?.policy_friction_score ?? 0.12) * 0.5,
    0.15,
    1
  );
  const spread = clamp(rollback * 0.65 + policy * 0.45 + (1 - capability) * 0.25, 0.08, 0.42);
  const sampleCount = Number(diagnostics.sample_count ?? missionlessSamples(bid));
  return {
    success,
    rollback,
    policy,
    capability,
    spread,
    sampleCount
  };
}

function missionlessSamples(bid) {
  const runtime = Number(bid?.estimated_runtime_seconds ?? 120);
  if (runtime <= 90) {
    return 24;
  }
  if (runtime <= 180) {
    return 16;
  }
  return 12;
}

function curvePath(width, height, mean, spread) {
  const steps = 26;
  const baseline = height - 16;
  const amplitude = height - 44;
  const sigma = Math.max(0.07, spread);
  const points = [];
  for (let index = 0; index <= steps; index += 1) {
    const ratio = index / steps;
    const x = ratio * width;
    const exponent = -((ratio - mean) ** 2) / (2 * sigma ** 2);
    const y = baseline - Math.exp(exponent) * amplitude;
    points.push(`${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return `${points.join(" ")} L ${width} ${baseline} L 0 ${baseline} Z`;
}

function winningNarrative(selectedBid, mission) {
  if (mission?.outcome_summary?.plain_summary) {
    return mission.outcome_summary.plain_summary;
  }
  if (selectedBid?.search_summary) {
    return selectedBid.search_summary;
  }
  if (selectedBid?.mission_rationale) {
    return selectedBid.mission_rationale;
  }
  return "The live market is still collecting enough evidence to explain the leading strategy cleanly.";
}

const CURVE_TONES = [
  "var(--accent)",
  "var(--mint)",
  "var(--sky)",
  "var(--amber)"
];

export default function MonteCarloPanel({ mission, selectedBid, latestProposalTrace }) {
  const bids = rankedBids(mission, selectedBid);
  const simulationSummary = mission?.simulation_summary ?? {};
  const activeWinner =
    selectedBid ?? bids.find((bid) => bid.bid_id === mission?.winner_bid_id) ?? bids[0] ?? null;
  const winnerDiagnostics = activeWinner ? diagnosticsFor(activeWinner) : null;
  const frontierGap = Number(simulationSummary.frontier_gap ?? 0);
  const totalSamples =
    Number(simulationSummary.monte_carlo_samples ?? 0) ||
    bids.reduce((total, bid) => total + diagnosticsFor(bid).sampleCount, 0);
  const decisionCopy =
    latestProposalTrace?.payload?.summary ??
    winningNarrative(activeWinner, mission);

  return (
    <section className="panel monte-carlo-panel">
      <div className="section-title">
        <div>
          <p className="eyebrow">Monte Carlo Engine</p>
          <h2>Simulation is the decision surface</h2>
          <p>
            Every contender is scored against success, rollback risk, policy friction, and
            capability availability before execution.
          </p>
        </div>
        <div className="monte-carlo-scorecard">
          <div className="monte-carlo-score">
            <span>Samples</span>
            <strong>{formatInteger(totalSamples)}</strong>
          </div>
          <div className="monte-carlo-score">
            <span>Frontier gap</span>
            <strong>{formatNumber(frontierGap, 2)}</strong>
          </div>
          <div className="monte-carlo-score">
            <span>Decision mode</span>
            <strong>{simulationSummary.search_mode ?? "bounded Monte Carlo"}</strong>
          </div>
        </div>
      </div>

      <div className="monte-carlo-chart-shell">
        <div className="monte-carlo-chart-copy">
          <span className="muted-chip">Why the leader wins</span>
          <strong>{activeWinner?.role ?? activeWinner?.strategy_family ?? "Awaiting contenders"}</strong>
          <p>{decisionCopy}</p>
          {winnerDiagnostics ? (
            <div className="monte-carlo-pill-row">
              <span>Success {formatNumber(winnerDiagnostics.success * 100, 0)}%</span>
              <span>Rollback {formatNumber(winnerDiagnostics.rollback * 100, 0)}%</span>
              <span>Capability {formatNumber(winnerDiagnostics.capability * 100, 0)}%</span>
              <span>Policy friction {formatNumber(winnerDiagnostics.policy, 2)}</span>
            </div>
          ) : null}
        </div>

        <div className="monte-carlo-chart-frame">
          <svg
            className="monte-carlo-chart"
            viewBox="0 0 560 280"
            role="img"
            aria-label="Monte Carlo contender distribution chart"
          >
            <defs>
              {bids.map((bid, index) => (
                <linearGradient
                  key={bid.bid_id}
                  id={`mc-gradient-${bid.bid_id}`}
                  x1="0%"
                  x2="100%"
                  y1="0%"
                  y2="100%"
                >
                  <stop offset="0%" stopColor={CURVE_TONES[index % CURVE_TONES.length]} stopOpacity="0.58" />
                  <stop offset="100%" stopColor={CURVE_TONES[index % CURVE_TONES.length]} stopOpacity="0.06" />
                </linearGradient>
              ))}
            </defs>
            <g className="monte-carlo-grid">
              {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                <line
                  key={`grid-y-${ratio}`}
                  x1="0"
                  x2="560"
                  y1={32 + ratio * 200}
                  y2={32 + ratio * 200}
                />
              ))}
              {[0, 0.2, 0.4, 0.6, 0.8, 1].map((ratio) => (
                <line
                  key={`grid-x-${ratio}`}
                  x1={ratio * 560}
                  x2={ratio * 560}
                  y1="24"
                  y2="248"
                />
              ))}
            </g>
            {bids.map((bid, index) => {
              const diagnostics = diagnosticsFor(bid);
              const path = curvePath(560, 260, diagnostics.success, diagnostics.spread);
              return (
                <path
                  key={bid.bid_id}
                  d={path}
                  fill={`url(#mc-gradient-${bid.bid_id})`}
                  stroke={CURVE_TONES[index % CURVE_TONES.length]}
                  strokeWidth={bid.bid_id === activeWinner?.bid_id ? 3 : 2}
                />
              );
            })}
          </svg>
          <div className="monte-carlo-axis">
            <span>0%</span>
            <span>20%</span>
            <span>40%</span>
            <span>60%</span>
            <span>80%</span>
            <span>100%</span>
          </div>
        </div>
      </div>

      <div className="monte-carlo-table">
        {bids.length ? (
          bids.map((bid, index) => {
            const diagnostics = diagnosticsFor(bid);
            return (
              <article key={bid.bid_id} className="monte-carlo-row">
                <div className="monte-carlo-row-title">
                  <span
                    className="monte-carlo-dot"
                    style={{ background: CURVE_TONES[index % CURVE_TONES.length] }}
                  />
                  <div>
                    <strong>{bid.role ?? bid.strategy_family}</strong>
                    <p>{bid.strategy_summary ?? "Simulation contender"}</p>
                  </div>
                </div>
                <div className="monte-carlo-row-metrics">
                  <span>Win {formatNumber(diagnostics.success * 100, 0)}%</span>
                  <span>Risk {formatNumber(diagnostics.rollback * 100, 0)}%</span>
                  <span>Samples {formatInteger(diagnostics.sampleCount)}</span>
                  <span>Capability {formatNumber(diagnostics.capability * 100, 0)}%</span>
                </div>
              </article>
            );
          })
        ) : (
          <div className="section-empty">
            Monte Carlo summaries will appear once the market starts scoring contenders.
          </div>
        )}
      </div>
    </section>
  );
}
