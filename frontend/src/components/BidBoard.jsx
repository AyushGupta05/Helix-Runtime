import { formatCurrency, formatInteger, formatNumber, humanizeToken, summarizeProvider } from "../lib/format";

function humanizeStrategy(value) {
  return String(value || "untitled strategy")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function metricTotal(values) {
  return Object.values(values ?? {}).reduce((total, value) => total + Number(value || 0), 0);
}

function riskLabel(value) {
  const numeric = Number(value ?? 0);
  if (numeric >= 0.66) return "High";
  if (numeric >= 0.33) return "Med";
  return "Low";
}

function bidStatusFor(bid, winnerBidId, standbyBidId, activePhase) {
  if (bid.bid_id === winnerBidId) {
    return { label: "WINNER", tone: "winner", rank: 4 };
  }
  if (bid.bid_id === standbyBidId) {
    return { label: "STANDBY", tone: "standby", rank: 3 };
  }
  if (bid.rejection_reason) {
    return { label: "REJECTED", tone: "rejected", rank: 0 };
  }
  if (["market", "simulate", "select"].includes(activePhase)) {
    return { label: "BIDDING", tone: "bidding", rank: 2 };
  }
  return { label: "SHORTLISTED", tone: "shortlisted", rank: 1 };
}

function BidCard({ bid, winnerBidId, standbyBidId, activePhase, index }) {
  const status = bidStatusFor(bid, winnerBidId, standbyBidId, activePhase);
  const totalTokens = metricTotal(bid.token_usage);
  const totalCost = metricTotal(bid.cost_usage);

  return (
    <article
      className={`leaderboard-card leaderboard-card-${status.tone}`}
      style={{ animationDelay: `${Math.min(index * 45, 240)}ms` }}
    >
      <div className="leaderboard-card-head">
        <div>
          <strong>{humanizeStrategy(bid.strategy_family || bid.role)}</strong>
          <p className="leaderboard-card-provider">{summarizeProvider(bid.provider)}</p>
        </div>
        <span className={`leaderboard-status leaderboard-status-${status.tone}`}>{status.label}</span>
      </div>
      <div className="leaderboard-line">
        <span>Score {formatNumber(bid.score)}</span>
        <span>Conf {formatNumber(bid.confidence)}</span>
        <span>Risk {riskLabel(bid.risk)}</span>
      </div>
      <div className="leaderboard-line leaderboard-line-strong">
        <span>{formatInteger(totalTokens)} tok</span>
        <span>{formatCurrency(totalCost)}</span>
      </div>
      {bid.rejection_reason ? (
        <p className="leaderboard-note">{bid.rejection_reason}</p>
      ) : bid.model_id ? (
        <p className="leaderboard-note">{bid.model_id}</p>
      ) : null}
    </article>
  );
}

export default function BidBoard({
  bids,
  winnerBidId,
  standbyBidId,
  activeTaskId,
  activePhase,
  activeBidRound,
  simulationRound,
  usageSummary
}) {
  const activeBids = bids.filter((bid) => bid.task_id === activeTaskId);
  const winner = activeBids.find((bid) => bid.bid_id === winnerBidId) ?? null;
  const standby = activeBids.find((bid) => bid.bid_id === standbyBidId) ?? null;
  const roundTokens =
    usageSummary?.active_task?.total_tokens ??
    activeBids.reduce((total, bid) => total + metricTotal(bid.token_usage), 0);
  const roundCost =
    usageSummary?.active_task?.total_cost ??
    activeBids.reduce((total, bid) => total + metricTotal(bid.cost_usage), 0);
  const providerSpend = Object.values(
    activeBids.reduce((accumulator, bid) => {
      const provider = summarizeProvider(bid.provider);
      const next = accumulator[provider] ?? { provider, tokens: 0, cost: 0 };
      next.tokens += metricTotal(bid.token_usage);
      next.cost += metricTotal(bid.cost_usage);
      accumulator[provider] = next;
      return accumulator;
    }, {})
  ).sort((left, right) => right.tokens - left.tokens);
  const orderedBids = [...activeBids].sort((left, right) => {
    const leftStatus = bidStatusFor(left, winnerBidId, standbyBidId, activePhase);
    const rightStatus = bidStatusFor(right, winnerBidId, standbyBidId, activePhase);
    if (leftStatus.rank !== rightStatus.rank) {
      return rightStatus.rank - leftStatus.rank;
    }
    return Number(right.score ?? -1) - Number(left.score ?? -1);
  });

  return (
    <div className="arena-shell">
      <div className="arena-topline">
        <div>
          <p className="eyebrow">Live Bidding Arena</p>
          <h2>Round {Math.max(activeBidRound || 0, 1)}</h2>
        </div>
        <span className="arena-phase">{humanizeToken(activePhase || "idle")}</span>
      </div>
      <div className="arena-quickstats">
        <div className="arena-stat">
          <span>Round status</span>
          <strong>{humanizeToken(activePhase || "idle")}</strong>
        </div>
        <div className="arena-stat">
          <span>Simulation count</span>
          <strong>{simulationRound ?? 0}</strong>
        </div>
        <div className="arena-stat arena-stat-spend">
          <span>Live round spend</span>
          <strong>{formatInteger(roundTokens)} tok</strong>
          <p>{formatCurrency(roundCost)}</p>
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
            <BidCard
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
            Waiting for bids on {activeTaskId || "the active task"}.
          </div>
        )}
      </div>
      {(winner || standby) && (
        <div className="arena-pinned">
          {winner ? (
            <span className="arena-pinned-item">
              Winner: {humanizeStrategy(winner.strategy_family)} ({summarizeProvider(winner.provider)})
            </span>
          ) : null}
          {standby ? (
            <span className="arena-pinned-item">
              Standby: {humanizeStrategy(standby.strategy_family)} ({summarizeProvider(standby.provider)})
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}
