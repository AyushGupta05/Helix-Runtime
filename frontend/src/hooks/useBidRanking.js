import { useMemo } from 'react';

/**
 * Ranks and filters bids by status (winner > standby > competing > rejected) and score
 * @param {Object} mission - Mission object with bids array
 * @param {number} limit - Max number of bids to return
 * @returns {Array} Sorted bids array
 */
export function useBidRanking(mission, limit = Infinity) {
  return useMemo(() => {
    const bids = mission?.bids ?? [];
    const winnerBidId = mission?.winner_bid_id;
    const standbyBidId = mission?.standby_bid_id;

    return [...bids]
      .sort((left, right) => {
        // Winner first
        if (left.bid_id === winnerBidId) return -1;
        if (right.bid_id === winnerBidId) return 1;

        // Standby second
        if (left.bid_id === standbyBidId) return -1;
        if (right.bid_id === standbyBidId) return 1;

        // Not rejected over rejected
        const leftRejected = Boolean(left.rejection_reason);
        const rightRejected = Boolean(right.rejection_reason);
        if (leftRejected !== rightRejected) return rightRejected ? -1 : 1;

        // Score/confidence descending
        const leftScore = left.search_diagnostics?.success_rate ?? left.score ?? left.confidence ?? -1;
        const rightScore = right.search_diagnostics?.success_rate ?? right.score ?? right.confidence ?? -1;
        return rightScore - leftScore;
      })
      .slice(0, limit);
  }, [mission?.bids, mission?.winner_bid_id, mission?.standby_bid_id, limit]);
}
