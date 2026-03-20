import { formatNumber, formatRuntime } from "../lib/format";
import StatusBadge from "./StatusBadge";

function BidCard({ bid, label }) {
  if (!bid) {
    return (
      <div className="bid-spotlight empty-card">
        <span>{label}</span>
        <strong>Waiting for qualified contender</strong>
      </div>
    );
  }
  return (
    <div className="bid-spotlight">
      <span>{label}</span>
      <strong>{bid.role}</strong>
      <p>{bid.strategy_family}</p>
      <div className="bid-spotlight-metrics">
        <span>score {formatNumber(bid.score)}</span>
        <span>risk {formatNumber(bid.risk)}</span>
      </div>
    </div>
  );
}

export default function BidBoard({ bids, winnerBidId, standbyBidId }) {
  const ordered = [...bids].sort((left, right) => {
    const leftScore = left.score ?? -1;
    const rightScore = right.score ?? -1;
    return rightScore - leftScore;
  });
  const winner = ordered.find((bid) => bid.bid_id === winnerBidId) ?? null;
  const standby = ordered.find((bid) => bid.bid_id === standbyBidId) ?? null;

  return (
    <div className="bid-board">
      <div className="bid-spotlights">
        <BidCard bid={winner} label="Winner" />
        <BidCard bid={standby} label="Standby" />
      </div>
      <div className="bid-table-shell">
        <table className="bid-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Strategy</th>
              <th>Score</th>
              <th>Risk</th>
              <th>Cost</th>
              <th>Runtime</th>
              <th>Files</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((bid) => {
              const status = bid.bid_id === winnerBidId ? "running" : bid.bid_id === standbyBidId ? "ready" : bid.rejection_reason ? "failed" : "pending";
              return (
                <tr key={bid.bid_id} className={bid.bid_id === winnerBidId ? "row-winner" : ""}>
                  <td>{bid.role}</td>
                  <td>
                    <strong>{bid.strategy_family}</strong>
                    <p>{bid.strategy_summary}</p>
                    {bid.rejection_reason ? <span className="inline-warning">{bid.rejection_reason}</span> : null}
                  </td>
                  <td>{formatNumber(bid.score)}</td>
                  <td>{formatNumber(bid.risk)}</td>
                  <td>{formatNumber(bid.cost)}</td>
                  <td>{formatRuntime(bid.estimated_runtime_seconds)}</td>
                  <td>{bid.touched_files.join(", ") || "pending"}</td>
                  <td>
                    <StatusBadge value={status} quiet />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
