import { useEffect, useMemo, useState } from "react";

import { formatInteger, formatNumber } from "../lib/format";

const CURVE_COLORS = ["#f3cb62", "#79c8ff", "#9ce89f", "#f58b76"];
const VIEWBOX_WIDTH = 660;
const VIEWBOX_HEIGHT = 320;

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, Number(value ?? 0)));
}

function seededRandom(seed) {
  let current = seed % 2147483647;
  if (current <= 0) {
    current += 2147483646;
  }
  return () => {
    current = (current * 16807) % 2147483647;
    return (current - 1) / 2147483646;
  };
}

function estimateSamples(bid, summary) {
  const explicit = Number(bid?.search_diagnostics?.sample_count ?? 0);
  if (explicit > 0) {
    return explicit;
  }
  const missionDepth = Number(summary?.monte_carlo_samples ?? 0);
  if (missionDepth > 0) {
    return missionDepth;
  }
  const runtime = Number(bid?.estimated_runtime_seconds ?? 90);
  return Math.max(12, Math.round(220 / Math.max(35, runtime)));
}

function diagnosticsFor(bid, summary) {
  const diagnostics = bid?.search_diagnostics ?? {};
  const success = clamp(diagnostics.success_rate ?? bid?.score ?? bid?.confidence ?? 0.4);
  const rollback = clamp(diagnostics.rollback_rate ?? bid?.risk ?? 0.2);
  const policy = clamp(
    diagnostics.policy_friction_cost ??
      bid?.policy_friction_score ??
      bid?.capability_reliance_score ??
      0.14
  );
  const capability = clamp(
    diagnostics.capability_availability_probability ??
      (1 - (bid?.policy_friction_score ?? 0.18) * 0.5),
    0.15,
    1
  );
  const spread = clamp(rollback * 0.55 + policy * 0.38 + (1 - capability) * 0.25, 0.08, 0.42);
  return {
    success,
    rollback,
    policy,
    capability,
    spread,
    sampleCount: estimateSamples(bid, summary)
  };
}

function curvePath(mean, spread, phaseShift) {
  const baseline = VIEWBOX_HEIGHT - 26;
  const amplitude = VIEWBOX_HEIGHT - 78;
  const points = [];
  const steps = 38;
  const drift = Math.sin(phaseShift) * 0.012;
  const center = clamp(mean + drift, 0.06, 0.96);
  const sigma = Math.max(0.07, spread);

  for (let index = 0; index <= steps; index += 1) {
    const ratio = index / steps;
    const x = ratio * VIEWBOX_WIDTH;
    const exponent = -((ratio - center) ** 2) / (2 * sigma ** 2);
    const y = baseline - Math.exp(exponent) * amplitude;
    points.push(`${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return `${points.join(" ")} L ${VIEWBOX_WIDTH} ${baseline} L 0 ${baseline} Z`;
}

function linePath(mean, spread, phaseShift) {
  const baseline = VIEWBOX_HEIGHT - 26;
  const amplitude = VIEWBOX_HEIGHT - 78;
  const points = [];
  const steps = 48;
  const drift = Math.sin(phaseShift) * 0.012;
  const center = clamp(mean + drift, 0.06, 0.96);
  const sigma = Math.max(0.07, spread);

  for (let index = 0; index <= steps; index += 1) {
    const ratio = index / steps;
    const x = ratio * VIEWBOX_WIDTH;
    const exponent = -((ratio - center) ** 2) / (2 * sigma ** 2);
    const y = baseline - Math.exp(exponent) * amplitude;
    points.push(`${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return points.join(" ");
}

function particleField(seed, count, bidCount) {
  const rand = seededRandom(seed + bidCount * 97);
  return Array.from({ length: count }, (_, index) => ({
    id: `particle-${index}`,
    x: Math.round(rand() * VIEWBOX_WIDTH),
    y: Math.round(rand() * (VIEWBOX_HEIGHT - 36) + 14),
    r: Number((rand() * 1.8 + 0.35).toFixed(2)),
    color: CURVE_COLORS[index % CURVE_COLORS.length],
    opacity: Number((rand() * 0.4 + 0.1).toFixed(2)),
    delay: Number((rand() * 2.4).toFixed(2)),
    duration: Number((rand() * 2.6 + 2.2).toFixed(2))
  }));
}

export default function MonteCarloPanel({ mission, bids, winnerBidId }) {
  const [clock, setClock] = useState(Date.now());
  const simulationSummary = mission?.simulation_summary ?? {};

  useEffect(() => {
    const interval = window.setInterval(() => {
      setClock(Date.now());
    }, 950);
    return () => window.clearInterval(interval);
  }, []);

  const ranked = useMemo(() => {
    const next = Array.isArray(bids) ? bids : mission?.bids ?? [];
    const winner = winnerBidId ?? mission?.winner_bid_id ?? null;
    return [...next]
      .sort((left, right) => {
        if (left.bid_id === winner) return -1;
        if (right.bid_id === winner) return 1;
        return (
          Number(right.search_diagnostics?.success_rate ?? right.score ?? right.confidence ?? 0) -
          Number(left.search_diagnostics?.success_rate ?? left.score ?? left.confidence ?? 0)
        );
      })
      .slice(0, 4);
  }, [bids, mission?.bids, mission?.winner_bid_id, winnerBidId]);

  const activeWinner =
    ranked.find((bid) => bid.bid_id === (winnerBidId ?? mission?.winner_bid_id)) ?? ranked[0] ?? null;

  const rows = useMemo(
    () =>
      ranked.map((bid, index) => {
        const diagnostics = diagnosticsFor(bid, simulationSummary);
        const phaseShift = clock / 1200 + index * 0.9 + Number(mission?.latest_event_id ?? 0) * 0.012;
        return {
          bid,
          diagnostics,
          areaPath: curvePath(diagnostics.success, diagnostics.spread, phaseShift),
          linePath: linePath(diagnostics.success, diagnostics.spread, phaseShift),
          color: CURVE_COLORS[index % CURVE_COLORS.length],
          gradientId: `screen-ref-gradient-${bid.bid_id}`,
          winner: bid.bid_id === activeWinner?.bid_id
        };
      }),
    [ranked, simulationSummary, clock, mission?.latest_event_id, activeWinner?.bid_id]
  );

  const particles = useMemo(
    () =>
      particleField(
        Number(mission?.latest_event_id ?? 1) * 13 + Number(simulationSummary?.monte_carlo_samples ?? 0),
        120,
        rows.length
      ),
    [mission?.latest_event_id, simulationSummary?.monte_carlo_samples, rows.length]
  );

  const winnerDiagnostics = activeWinner ? diagnosticsFor(activeWinner, simulationSummary) : null;
  const totalSamples =
    Number(simulationSummary?.monte_carlo_samples ?? 0) ||
    rows.reduce((acc, row) => acc + row.diagnostics.sampleCount, 0);
  const winnerX = winnerDiagnostics ? Math.round(winnerDiagnostics.success * VIEWBOX_WIDTH) : null;
  const activeStep =
    Math.max(
      1,
      Math.min(
        10,
        Number(mission?.simulation_round ?? 0) || ((Number(mission?.latest_event_id ?? 0) % 10) + 1)
      )
    );

  return (
    <section className="panel screen-ref-chart-panel">
      <div className="section-title">
        <h2>Dynamic Simulation Graph</h2>
        <p>The chart stays live and animated, but it now occupies the reference’s central focus area.</p>
      </div>

      <div className="screen-ref-chip-row">
        <span className="screen-ref-data-chip">Governed simulation active</span>
        <span className="screen-ref-data-chip">
          Frontier gap {formatNumber(simulationSummary.frontier_gap ?? 0, 3)}
        </span>
        <span className="screen-ref-data-chip">
          Search mode {String(simulationSummary.search_mode ?? "bounded_monte_carlo").replace(/_/g, " ")}
        </span>
      </div>

      <div className="screen-ref-chart-shell">
        <svg
          className="screen-ref-chart-svg"
          viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
          role="img"
          aria-label="Real-time Monte Carlo distribution plot"
        >
          <defs>
            {rows.map((row) => (
              <linearGradient key={row.gradientId} id={row.gradientId} x1="0%" x2="100%" y1="0%" y2="100%">
                <stop offset="0%" stopColor={row.color} stopOpacity="0.72" />
                <stop offset="100%" stopColor={row.color} stopOpacity="0.08" />
              </linearGradient>
            ))}
          </defs>

          <g className="screen-ref-chart-grid">
            {[0, 0.2, 0.4, 0.6, 0.8, 1].map((ratio) => (
              <line key={`x-${ratio}`} x1={ratio * VIEWBOX_WIDTH} x2={ratio * VIEWBOX_WIDTH} y1="8" y2={VIEWBOX_HEIGHT - 26} />
            ))}
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
              <line key={`y-${ratio}`} x1="0" x2={VIEWBOX_WIDTH} y1={24 + ratio * (VIEWBOX_HEIGHT - 64)} y2={24 + ratio * (VIEWBOX_HEIGHT - 64)} />
            ))}
          </g>

          <g className="screen-ref-chart-particles">
            {particles.map((particle) => (
              <circle
                key={particle.id}
                className="screen-ref-chart-particle"
                cx={particle.x}
                cy={particle.y}
                r={particle.r}
                fill={particle.color}
                opacity={particle.opacity}
                style={{
                  "--mc-delay": `${particle.delay}s`,
                  "--mc-duration": `${particle.duration}s`
                }}
              />
            ))}
          </g>

          <g className="screen-ref-chart-curves">
            {rows.map((row) => (
              <g key={row.bid.bid_id}>
                <path d={row.areaPath} fill={`url(#${row.gradientId})`} />
                <path
                  d={row.linePath}
                  fill="none"
                  stroke={row.color}
                  strokeWidth={row.winner ? 3.2 : 2}
                  opacity={row.winner ? 1 : 0.82}
                />
              </g>
            ))}
          </g>

          {winnerX !== null ? (
            <line
              className="screen-ref-chart-crosshair"
              x1={winnerX}
              x2={winnerX}
              y1="12"
              y2={VIEWBOX_HEIGHT - 20}
            />
          ) : null}
        </svg>
      </div>

      <div className="screen-ref-chart-axis" aria-hidden="true">
        <span>20</span>
        <span>40</span>
        <span>60</span>
        <span>76</span>
        <span>90</span>
        <span>110</span>
        <span>120</span>
      </div>

      <div className="screen-ref-chart-replay">
        <span className="screen-ref-action-chip is-active">Replay simulation</span>
        <div className="screen-ref-chart-stepper" aria-label="Simulation rounds">
          {Array.from({ length: 10 }, (_, index) => index + 1).map((step) => (
            <span key={step} className={step === activeStep ? "is-active" : ""}>
              {step}
            </span>
          ))}
        </div>
      </div>

      <div className="screen-ref-chart-metrics">
        {winnerDiagnostics ? (
          <>
            <span>Winner {activeWinner?.role ?? activeWinner?.strategy_family}</span>
            <span>Success {formatNumber(winnerDiagnostics.success * 100, 0)}%</span>
            <span>Rollback {formatNumber(winnerDiagnostics.rollback * 100, 0)}%</span>
            <span>Policy {formatNumber(winnerDiagnostics.policy, 2)}</span>
            <span>Capability {formatNumber(winnerDiagnostics.capability * 100, 0)}%</span>
            <span>Samples {formatInteger(totalSamples)}</span>
          </>
        ) : (
          <span>Simulation diagnostics will appear once contender scoring begins.</span>
        )}
      </div>
    </section>
  );
}
