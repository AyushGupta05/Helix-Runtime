import {
  formatCurrency,
  formatInteger,
  formatNumber,
  humanizeGenerationMode,
  humanizeToken,
  isDeterministicFallbackBid,
  summarizeBidOrigin,
  summarizeProvider
} from "../lib/format";

function humanizeStrategy(value) {
  return String(value || "untitled strategy")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function metricTotal(values) {
  if (values?.total_tokens !== undefined && values?.total_tokens !== null) {
    return Number(values.total_tokens || 0);
  }
  if (values?.usd !== undefined && values?.usd !== null) {
    return Number(values.usd || 0);
  }
  if (values?.total_cost !== undefined && values?.total_cost !== null) {
    return Number(values.total_cost || 0);
  }
  return Object.values(values ?? {}).reduce((total, value) => total + Number(value || 0), 0);
}

function riskLabel(value) {
  const numeric = Number(value ?? 0);
  if (numeric >= 0.66) return "High";
  if (numeric >= 0.33) return "Med";
  return "Low";
}

function providerLabelForBid(bid) {
  if (isDeterministicFallbackBid(bid)) {
    return "System";
  }
  if (bid.generation_mode === "mock") {
    return "Mock";
  }
  if (bid.generation_mode === "replay") {
    return "Replay";
  }
  if (bid.provider) {
    return summarizeProvider(bid.provider);
  }
  return "Unknown";
}

function bidModeTone(generationMode) {
  switch (generationMode) {
    case "provider_model":
      return "provider";
    case "deterministic_fallback":
      return "fallback";
    case "mock":
      return "mock";
    case "replay":
      return "replay";
    default:
      return "unknown";
  }
}

function bidStatusFor(bid, winnerBidId, standbyBidId, activePhase) {
  if (bid.bid_id === winnerBidId) {
    return { label: "LEADING", tone: "winner", rank: 4 };
  }
  if (bid.bid_id === standbyBidId) {
    return { label: "STANDBY", tone: "standby", rank: 3 };
  }
  if (bid.rejection_reason) {
    return { label: "REJECTED", tone: "rejected", rank: 0 };
  }
  if (["strategize", "simulate", "select"].includes(activePhase)) {
    return { label: "COMPETING", tone: "bidding", rank: 2 };
  }
  return { label: "CONTENDER", tone: "shortlisted", rank: 1 };
}

function StrategyCard({ bid, winnerBidId, standbyBidId, activePhase, index }) {
  const status = bidStatusFor(bid, winnerBidId, standbyBidId, activePhase);
  const totalTokens = metricTotal(bid.token_usage);
  const totalCost = metricTotal(bid.cost_usage);
  const modeTone = bidModeTone(bid.generation_mode);

  return (
    <article
      className={`leaderboard-card leaderboard-card-${status.tone} leaderboard-card-${modeTone}`}
      style={{ animationDelay: `${Math.min(index * 45, 240)}ms` }}
    >
      <div className="leaderboard-card-head">
        <div>
          <strong>{bid.role || humanizeStrategy(bid.strategy_family)}</strong>
          <p className="leaderboard-card-provider">{providerLabelForBid(bid)}</p>
        </div>
        <span className={`leaderboard-status leaderboard-status-${status.tone}`}>{status.label}</span>
      </div>
      {bid.mission_rationale ? (
        <p className="leaderboard-card-rationale">{bid.mission_rationale}</p>
      ) : null}
      <p className="leaderboard-card-origin">{summarizeBidOrigin(bid)}</p>
      <div className="leaderboard-line">
        <span>Score {formatNumber(bid.score)}</span>
        <span>Conf {formatNumber(bid.confidence)}</span>
        <span>Risk {riskLabel(bid.risk)}</span>
      </div>
      <div className="leaderboard-line">
        <span>Model {bid.model_id || "n/a"}</span>
        <span>Mode {humanizeGenerationMode(bid.generation_mode || "unknown")}</span>
      </div>
      <div className="leaderboard-line leaderboard-line-strong">
        <span>{formatInteger(totalTokens)} tok</span>
        <span>{formatCurrency(totalCost)}</span>
      </div>
      {bid.rejection_reason ? (
        <p className="leaderboard-note">{bid.rejection_reason}</p>
      ) : bid.usage_unavailable_reason ? (
        <p className="leaderboard-note">{bid.usage_unavailable_reason}</p>
      ) : bid.model_id ? (
        <p className="leaderboard-note">{bid.model_id}</p>
      ) : null}
    </article>
  );
}

function humanizePhase(phase) {
  const labels = {
    strategize: "Strategizing",
    simulate: "Simulating",
    select: "Selecting",
    execute: "Executing",
    validate: "Validating",
    recover: "Recovering",
    collect: "Scanning",
    finalize: "Finalizing",
    idle: "Idle",
  };
  return labels[phase] || humanizeToken(phase || "idle");
}

export default function BidBoard({
  bids,
  winnerBidId,
  standbyBidId,
  activeTaskId,
  activePhase,
  activeBidRound,
  simulationRound,
  biddingState,
  usageSummary
}) {
  const activeBids = bids.filter((bid) => bid.task_id === activeTaskId);
  const winner =
    bids.find((bid) => bid.bid_id === winnerBidId) ?? activeBids.find((bid) => bid.bid_id === winnerBidId) ?? null;
  const standby =
    bids.find((bid) => bid.bid_id === standbyBidId) ?? activeBids.find((bid) => bid.bid_id === standbyBidId) ?? null;
  const missionTotals = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const taskTotals = usageSummary?.active_task ?? { total_tokens: 0, total_cost: 0 };
  const providerSpend = Object.values(
    activeBids.reduce((accumulator, bid) => {
      const provider = providerLabelForBid(bid);
      const next = accumulator[provider] ?? { provider, tokens: 0, cost: 0 };
      next.tokens += metricTotal(bid.token_usage);
      next.cost += metricTotal(bid.cost_usage);
      accumulator[provider] = next;
      return accumulator;
    }, {})
  ).sort((left, right) => right.tokens - left.tokens);
  const fallbackTokens = activeBids.reduce((total, bid) => total + metricTotal(bid.token_usage), 0);
  const fallbackCost = activeBids.reduce((total, bid) => total + metricTotal(bid.cost_usage), 0);
  const orderedBids = [...activeBids].sort((left, right) => {
    const leftStatus = bidStatusFor(left, winnerBidId, standbyBidId, activePhase);
    const rightStatus = bidStatusFor(right, winnerBidId, standbyBidId, activePhase);
    if (leftStatus.rank !== rightStatus.rank) {
      return rightStatus.rank - leftStatus.rank;
    }
    return Number(right.score ?? -1) - Number(left.score ?? -1);
  });
  const biddingMode = biddingState?.generation_mode ?? activeBids[0]?.generation_mode ?? null;
  const biddingBanner =
    biddingState?.architecture_violation ||
    biddingState?.warning ||
    biddingMode === "deterministic_fallback" ||
    biddingState?.degraded
      ? {
          label:
            biddingState?.architecture_violation
              ? "Architecture violation"
              : biddingMode === "deterministic_fallback" || biddingState?.degraded
                ? "Degraded strategy mode"
                : "Strategy notice",
          message:
            biddingState?.architecture_violation ??
            biddingState?.warning ??
            "This round is running without provider-backed strategy competition.",
          tone:
            biddingState?.architecture_violation
              ? "rejected"
              : biddingMode === "deterministic_fallback" || biddingState?.degraded
                ? "standby"
                : "shortlisted"
        }
    : null;
  const roundTokens = taskTotals.total_tokens && taskTotals.total_tokens > 0 ? taskTotals.total_tokens : fallbackTokens;
  const roundCost = taskTotals.total_cost && taskTotals.total_cost > 0 ? taskTotals.total_cost : fallbackCost;

  return (
    <div className="arena-shell">
      <div className="arena-topline">
        <div>
          <p className="eyebrow">Strategy Market</p>
          <h2>Round {Math.max(activeBidRound || 0, 1)}</h2>
          <p className="arena-topline-copy">
            Competing strategies propose the next best move for the mission. The market continuously governs how the objective progresses.
          </p>
        </div>
        <span className="arena-phase">{humanizePhase(activePhase)}</span>
      </div>
      <div className="arena-quickstats">
        <div className="arena-stat">
          <span>Market phase</span>
          <strong>{humanizePhase(activePhase)}</strong>
        </div>
        <div className="arena-stat">
          <span>Simulation depth</span>
          <strong>{simulationRound ?? 0}</strong>
        </div>
        <div className="arena-stat arena-stat-spend">
          <span>Round spend</span>
          <strong>{formatInteger(roundTokens)} tok</strong>
          <p>{formatCurrency(roundCost)}</p>
        </div>
        <div className="arena-stat arena-stat-spend">
          <span>Mission spend</span>
          <strong>{formatInteger(missionTotals.total_tokens ?? 0)} tok</strong>
          <p>{formatCurrency(missionTotals.total_cost ?? 0)}</p>
        </div>
      </div>
      {biddingBanner ? (
        <div className={`arena-bidding-banner arena-bidding-banner-${biddingBanner.tone}`}>
          <strong>{biddingBanner.label}</strong>
          <p>{biddingBanner.message}</p>
        </div>
      ) : null}
      <div className="arena-leadership">
        <div className="arena-leadership-card arena-leadership-card-winner">
          <span>Leading Strategy</span>
          <strong>{winner ? humanizeStrategy(winner.strategy_family) : "Competing"}</strong>
          <p>{winner ? (winner.mission_rationale || summarizeBidOrigin(winner)) : "Strategies are competing to propose the next move."}</p>
        </div>
        <div className="arena-leadership-card arena-leadership-card-standby">
          <span>Standby Strategy</span>
          <strong>{standby ? humanizeStrategy(standby.strategy_family) : "No standby"}</strong>
          <p>{standby ? (standby.mission_rationale || summarizeBidOrigin(standby)) : "An alternate strategy will appear here once selected."}</p>
        </div>
      </div>
      <div className="arena-provider-strip">
        {providerSpend.map((entry) => (
          <span key={entry.provider} className="provider-spend-chip">
            {entry.provider}: {formatInteger(entry.tokens)} tok | {formatCurrency(entry.cost)}
          </span>
        ))}
      </div>
      <div className="bid-leaderboard">
        {orderedBids.length ? (
          orderedBids.map((bid, index) => (
            <StrategyCard
              key={bid.bid_id}
              bid={bid}
              winnerBidId={winnerBidId}
              standbyBidId={standbyBidId}
              activePhase={activePhase}
              index={index}
            />
          ))
        ) : (
          <div className="leaderboard-empty">
            Strategies are forming to compete for the next mission move.
          </div>
        )}
      </div>
      <div className="arena-pinned">
        <span className="arena-pinned-item">
          Current move: {activeTaskId || "awaiting strategy market"}
        </span>
        {winner ? (
          <span className="arena-pinned-item">
            Leading: {summarizeBidOrigin(winner)}
          </span>
        ) : null}
        {standby ? (
          <span className="arena-pinned-item">
            Standby: {summarizeBidOrigin(standby)}
          </span>
        ) : null}
      </div>
    </div>
  );
}
