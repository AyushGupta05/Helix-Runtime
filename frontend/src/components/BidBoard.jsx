import { formatCurrency, formatInteger, formatNumber, summarizeProvider } from "../lib/format";
import StatusBadge from "./StatusBadge";

function BidCell({ bid, winnerBidId, standbyBidId }) {
  if (!bid) {
    return <div className="market-cell market-cell-empty">No contender</div>;
  }

  const tone =
    bid.bid_id === winnerBidId ? "winner" : bid.bid_id === standbyBidId ? "standby" : bid.rejection_reason ? "failed" : "idle";
  const totalTokens = Object.values(bid.token_usage ?? {}).reduce((total, value) => total + Number(value || 0), 0);
  const totalCost = Object.values(bid.cost_usage ?? {}).reduce((total, value) => total + Number(value || 0), 0);

  return (
    <article className={`market-cell market-cell-${tone}`}>
      <div className="market-cell-head">
        <strong>{summarizeProvider(bid.provider)}</strong>
        <StatusBadge value={bid.bid_id === winnerBidId ? "running" : bid.bid_id === standbyBidId ? "ready" : bid.rejection_reason ? "failed" : "pending"} quiet />
      </div>
      <p className="market-cell-model">{bid.model_id ?? bid.role}</p>
      <div className="market-cell-metrics">
        <span>score {formatNumber(bid.score)}</span>
        <span>risk {formatNumber(bid.risk)}</span>
        <span>conf {formatNumber(bid.confidence)}</span>
      </div>
      <div className="market-cell-metrics">
        <span>{formatInteger(totalTokens)} tok</span>
        <span>{formatCurrency(totalCost)}</span>
      </div>
      <p>{bid.strategy_summary}</p>
      {bid.search_summary ? <p className="market-cell-note">Rollout: {bid.search_summary}</p> : null}
      {bid.rejection_reason ? <p className="inline-warning">{bid.rejection_reason}</p> : null}
    </article>
  );
}

export default function BidBoard({ bids, winnerBidId, standbyBidId, activeTaskId, providerMarketSummary }) {
  const activeBids = bids.filter((bid) => bid.task_id === activeTaskId);
  const providerOrder = [...new Set(activeBids.map((bid) => bid.provider || "system"))];
  const families = Object.entries(providerMarketSummary?.families ?? {}).filter(([_, familyBids]) =>
    familyBids.some((bid) => bid.task_id === activeTaskId)
  );

  const winner = activeBids.find((bid) => bid.bid_id === winnerBidId) ?? null;
  const standby = activeBids.find((bid) => bid.bid_id === standbyBidId) ?? null;

  return (
    <div className="provider-market">
      <div className="provider-market-head">
        <div className="bid-spotlight">
          <span>Winner</span>
          <strong>{winner ? `${summarizeProvider(winner.provider)} · ${winner.strategy_family}` : "Waiting"}</strong>
          <p>{winner?.model_id ?? winner?.role ?? "No selection yet"}</p>
        </div>
        <div className="bid-spotlight">
          <span>Standby</span>
          <strong>{standby ? `${summarizeProvider(standby.provider)} · ${standby.strategy_family}` : "Waiting"}</strong>
          <p>{standby?.model_id ?? standby?.role ?? "No standby yet"}</p>
        </div>
      </div>
      <div className="provider-market-grid">
        <div className="provider-market-grid-head family-head">Strategy family</div>
        {providerOrder.map((provider) => (
          <div key={provider} className="provider-market-grid-head">
            {summarizeProvider(provider)}
          </div>
        ))}
        {families.map(([family, familyBids]) => (
          <div key={family} className="provider-market-row">
            <div className="provider-market-family">
              <strong>{family}</strong>
              <span>{familyBids.length} contenders</span>
            </div>
            {providerOrder.map((provider) => {
              const bid = familyBids.find((item) => (item.provider || "system") === provider);
              return (
                <BidCell
                  key={`${family}-${provider}`}
                  bid={bid}
                  winnerBidId={winnerBidId}
                  standbyBidId={standbyBidId}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
