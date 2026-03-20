import { formatCurrency, formatInteger, formatNumber, summarizeProvider } from "../lib/format";
import StatusBadge from "./StatusBadge";

function humanizeStrategy(value) {
  return String(value || "untitled strategy")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function statusLabelForBid(bid, winnerBidId, standbyBidId) {
  if (bid.bid_id === winnerBidId) return "WIN";
  if (bid.bid_id === standbyBidId) return "STBY";
  if (bid.rejection_reason) return "FAIL";
  return "LIVE";
}

function riskLabel(value) {
  const numeric = Number(value ?? 0);
  if (numeric >= 0.66) return "high";
  if (numeric >= 0.33) return "med";
  return "low";
}

function BidCard({ bid, winnerBidId, standbyBidId }) {
  const tone =
    bid.bid_id === winnerBidId
      ? "winner"
      : bid.bid_id === standbyBidId
        ? "standby"
        : bid.rejection_reason
          ? "failed"
          : "live";
  const statusLabel = statusLabelForBid(bid, winnerBidId, standbyBidId);
  const totalTokens = Object.values(bid.token_usage ?? {}).reduce(
    (total, value) => total + Number(value || 0),
    0
  );
  const totalCost = Object.values(bid.cost_usage ?? {}).reduce(
    (total, value) => total + Number(value || 0),
    0
  );

  return (
    <article className={`market-card market-card-${tone}`}>
      <div className="market-card-head">
        <div>
          <span className="market-card-kicker">Strategy</span>
          <strong>{humanizeStrategy(bid.strategy_family || bid.role)}</strong>
        </div>
        <span className={`market-status market-status-${tone}`}>{statusLabel}</span>
      </div>
      <p className="market-card-provider">
        {summarizeProvider(bid.provider)} {bid.model_id ? `- ${bid.model_id}` : ""}
      </p>
      <div className="market-card-meta">
        <span className={`risk-pill risk-pill-${riskLabel(bid.risk)}`}>risk {riskLabel(bid.risk)}</span>
        <StatusBadge
          value={
            bid.bid_id === winnerBidId
              ? "running"
              : bid.bid_id === standbyBidId
                ? "ready"
                : bid.rejection_reason
                  ? "failed"
                  : "pending"
          }
          quiet
        />
      </div>
      <div className="market-card-stats">
        <div>
          <span>Score</span>
          <strong>{formatNumber(bid.score)}</strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>{formatNumber(bid.confidence)}</strong>
        </div>
        <div>
          <span>Tokens</span>
          <strong>{formatInteger(totalTokens)}</strong>
        </div>
        <div>
          <span>Cost</span>
          <strong>{formatCurrency(totalCost)}</strong>
        </div>
      </div>
      <p className="market-card-summary">{bid.strategy_summary}</p>
      {bid.search_summary ? <p className="market-card-note">Plan: {bid.search_summary}</p> : null}
      {bid.rejection_reason ? <p className="inline-warning">{bid.rejection_reason}</p> : null}
    </article>
  );
}

export default function BidBoard({
  bids,
  winnerBidId,
  standbyBidId,
  activeTaskId,
  providerMarketSummary
}) {
  const activeBids = bids.filter((bid) => bid.task_id === activeTaskId);
  const winner = activeBids.find((bid) => bid.bid_id === winnerBidId) ?? null;
  const standby = activeBids.find((bid) => bid.bid_id === standbyBidId) ?? null;
  const contenderCount =
    Object.values(providerMarketSummary?.families ?? {}).filter((familyBids) =>
      familyBids.some((bid) => bid.task_id === activeTaskId)
    ).length || activeBids.length;
  const orderedBids = [...activeBids].sort((left, right) => {
    const leftRank =
      left.bid_id === winnerBidId
        ? 3
        : left.bid_id === standbyBidId
          ? 2
          : left.rejection_reason
            ? 0
            : 1;
    const rightRank =
      right.bid_id === winnerBidId
        ? 3
        : right.bid_id === standbyBidId
          ? 2
          : right.rejection_reason
            ? 0
            : 1;
    if (leftRank !== rightRank) {
      return rightRank - leftRank;
    }
    return Number(right.score ?? -1) - Number(left.score ?? -1);
  });

  return (
    <div className="provider-market">
      <div className="provider-market-overview">
        <div className="bid-spotlight">
          <span>Winner</span>
          <strong>{winner ? humanizeStrategy(winner.strategy_family) : "Waiting"}</strong>
          <p>{winner ? summarizeProvider(winner.provider) : "No selection yet"}</p>
        </div>
        <div className="bid-spotlight">
          <span>Standby</span>
          <strong>{standby ? humanizeStrategy(standby.strategy_family) : "Waiting"}</strong>
          <p>{standby ? summarizeProvider(standby.provider) : "No standby yet"}</p>
        </div>
        <div className="bid-spotlight">
          <span>Active task</span>
          <strong>{activeTaskId || "Waiting"}</strong>
          <p>{contenderCount} strategies in play</p>
        </div>
      </div>
      <div className="market-card-grid">
        {orderedBids.length ? (
          orderedBids.map((bid) => (
            <BidCard
              key={bid.bid_id}
              bid={bid}
              winnerBidId={winnerBidId}
              standbyBidId={standbyBidId}
            />
          ))
        ) : (
          <div className="market-card market-card-empty">Waiting for bids on the active task.</div>
        )}
      </div>
    </div>
  );
}
