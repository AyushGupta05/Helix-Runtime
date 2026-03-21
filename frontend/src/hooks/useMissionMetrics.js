import { useMemo } from 'react';

/**
 * Computes aggregated mission metrics for display
 * @param {Object} mission - Mission object
 * @param {Object} usageSummary - Cost and token summary
 * @returns {Object} Aggregated metrics
 */
export function useMissionMetrics(mission, usageSummary) {
  return useMemo(() => {
    const bids = mission?.bids ?? [];
    const recentActions = mission?.recent_civic_actions ?? [];
    const blockedBids = bids.filter(
      (bid) =>
        bid?.rejection_reason ||
        bid?.civic_preflight?.decision === 'blocked' ||
        bid?.governed_envelope?.status === 'blocked'
    );

    return {
      totalTokens: usageSummary?.mission?.total_tokens ?? 0,
      totalCost: usageSummary?.mission?.total_cost ?? 0,
      bidCount: bids.length,
      civicActionCount: recentActions.length,
      blockedCount: blockedBids.length,
      providerCount: new Set(bids.map((b) => b.provider)).size,
      missionPhase: mission?.active_phase ?? 'idle',
      missionStatus: mission?.run_state ?? 'idle',
      winnerBid: bids.find((b) => b.bid_id === mission?.winner_bid_id) ?? null,
      standbyBid: bids.find((b) => b.bid_id === mission?.standby_bid_id) ?? null,
      activeLeader: mission?.winner_bid_id
        ? bids.find((b) => b.bid_id === mission.winner_bid_id)?.role ?? 'Unknown'
        : 'Awaiting winner',
    };
  }, [mission, usageSummary]);
}
