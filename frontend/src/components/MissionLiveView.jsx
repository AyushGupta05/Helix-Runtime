import BidBoard from "./BidBoard";
import CivicPanel from "./CivicPanel";
import EventStrip from "./EventStrip";
import MonteCarloPanel from "./MonteCarloPanel";
import { formatCurrency, formatInteger, formatNumber } from "../lib/format";

function liveSummaryCards(mission, selectedBid, usageSummary) {
  const missionUsage = usageSummary?.mission ?? {};
  const contenderCount = mission?.bids?.length ?? 0;
  const civicActions = mission?.recent_civic_actions?.length ?? 0;
  return [
    {
      label: "Mission mode",
      value: mission?.active_phase ?? "collect",
      detail: mission?.run_state ?? "idle"
    },
    {
      label: "Active leader",
      value: selectedBid?.role ?? selectedBid?.strategy_family ?? "Awaiting winner",
      detail: selectedBid?.search_summary ?? selectedBid?.mission_rationale ?? "Strategy board is still comparing contenders."
    },
    {
      label: "Visible contenders",
      value: formatInteger(contenderCount),
      detail: `${formatInteger(civicActions)} governed action${civicActions === 1 ? "" : "s"} recorded`
    },
    {
      label: "Mission spend",
      value: formatCurrency(missionUsage.total_cost ?? 0),
      detail: `${formatInteger(missionUsage.total_tokens ?? 0)} tokens`
    }
  ];
}

function autonomyStatus(mission) {
  const checkpoints = mission?.accepted_checkpoints?.length ?? 0;
  const recoveryRound = mission?.recovery_round ?? 0;
  const hasWinner = Boolean(mission?.winner_bid_id);
  const sampleCount =
    Number(mission?.simulation_summary?.monte_carlo_samples ?? 0) ||
    (mission?.bids ?? []).reduce(
      (total, bid) => total + Number(bid?.search_diagnostics?.sample_count ?? 0),
      0
    );
  return {
    headline: hasWinner
      ? "The market has selected a live path."
      : "The market is still scoring competing paths.",
    detail: `Monte Carlo samples ${formatInteger(sampleCount)} | ${formatInteger(checkpoints)} checkpoints | recovery round ${formatInteger(recoveryRound)}`,
    confidence:
      selectedAutonomyConfidence(mission)
  };
}

function selectedAutonomyConfidence(mission) {
  const bids = mission?.bids ?? [];
  const bestScore = Math.max(...bids.map((bid) => Number(bid.score ?? bid.confidence ?? 0)), 0);
  return formatNumber(bestScore * 100, 0);
}

export default function MissionLiveView({
  mission,
  trace,
  usageSummary,
  selectedBid,
  latestProposalTrace
}) {
  const summaryCards = liveSummaryCards(mission, selectedBid, usageSummary);
  const autonomy = autonomyStatus(mission);

  return (
    <div className="workspace-view workspace-live">
      <section className="panel mission-overview-strip">
        <div className="section-title">
          <p className="eyebrow">Live Control Room</p>
          <h2>Strategy market, simulation, and governance in one pass</h2>
          <p>
            Arbiter should feel autonomous here: it plans, compares, simulates, selects, and
            keeps recovery state visible without hiding the losing paths.
          </p>
        </div>

        <div className="mission-overview-grid">
          {summaryCards.map((card) => (
            <article key={card.label} className="overview-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>

        <div className="mission-overview-callout">
          <strong>{autonomy.headline}</strong>
          <p>{latestProposalTrace?.payload?.summary ?? autonomy.detail}</p>
          <span className="muted-chip">Live autonomy confidence {autonomy.confidence}%</span>
        </div>
      </section>

      <div className="live-mission-grid">
        <BidBoard
          bids={mission.bids}
          winnerBidId={mission.winner_bid_id}
          standbyBidId={mission.standby_bid_id}
          activeTaskId={mission.active_task_id}
          activePhase={mission.active_phase}
          activeBidRound={mission.active_bid_round}
          biddingState={mission.bidding_state}
          usageSummary={usageSummary}
          events={mission.events}
        />

        <MonteCarloPanel
          mission={mission}
          selectedBid={selectedBid}
          latestProposalTrace={latestProposalTrace}
        />

        <CivicPanel mission={mission} />
      </div>

      <EventStrip mission={mission} events={mission.events} trace={trace} />
    </div>
  );
}
