import {
  formatCurrency,
  formatInteger,
  formatNumber,
  formatRuntime,
  humanizeEventType,
  humanizeGenerationMode,
  humanizeToken,
  isDeterministicFallbackBid,
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

function scoreLabel(value) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return formatNumber(value, 2);
}

function skillList(bid, key) {
  const skills = Array.isArray(bid?.[key]) ? bid[key] : [];
  return skills.length ? skills.slice(0, 3).join(", ") : "none";
}

function envelopeLabel(bid) {
  const envelope = bid?.governed_envelope ?? bid?.civic_preflight ?? null;
  if (!envelope) {
    return null;
  }
  if (typeof envelope === "string") {
    return envelope;
  }
  if (envelope.status) {
    return String(envelope.status).replace(/[_-]/g, " ");
  }
  if (envelope.decision) {
    return String(envelope.decision).replace(/[_-]/g, " ");
  }
  return "governed";
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
  if (bid.status === "simulated" || activePhase === "simulate") {
    return { label: "SCORING", tone: "bidding", rank: 2 };
  }
  if (["strategize", "select"].includes(activePhase)) {
    return { label: "COMPETING", tone: "bidding", rank: 2 };
  }
  return { label: "CONTENDER", tone: "shortlisted", rank: 1 };
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
    idle: "Idle"
  };
  return labels[phase] || humanizeToken(phase || "idle");
}

function timeLabel(value) {
  if (!value) {
    return "--:--";
  }
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function bidTickerEntries(events, activeTaskId) {
  return [...(events ?? [])]
    .filter((event) => {
      if (activeTaskId && event.payload?.task_id && event.payload.task_id !== activeTaskId) {
        return false;
      }
      return ["bid.generated", "bid.submitted", "bid.rejected", "bid.won", "standby.selected", "simulation.bid_scored"].includes(event.event_type);
    })
    .slice(-10)
    .reverse();
}

function StrategyCard({ bid, winnerBidId, standbyBidId, activePhase, index }) {
  const status = bidStatusFor(bid, winnerBidId, standbyBidId, activePhase);
  const totalTokens = metricTotal(bid.token_usage);
  const totalCost = metricTotal(bid.cost_usage);
  const modeTone = bidModeTone(bid.generation_mode);
  const envelope = envelopeLabel(bid);
  const diagnostics = bid.search_diagnostics ?? {};

  return (
    <article
      className={`leaderboard-card leaderboard-card-${status.tone} leaderboard-card-${modeTone}`}
      style={{ animationDelay: `${Math.min(index * 45, 240)}ms` }}
    >
      <div className="leaderboard-card-head">
        <div>
          <strong>{bid.role || humanizeStrategy(bid.strategy_family)}</strong>
          <p className="leaderboard-card-provider">
            {providerLabelForBid(bid)} | {humanizeGenerationMode(bid.generation_mode || "unknown")}
          </p>
        </div>
        <span className={`leaderboard-status leaderboard-status-${status.tone}`}>{status.label}</span>
      </div>

      <p className="leaderboard-card-rationale">
        {bid.mission_rationale || bid.strategy_summary || "Live contender awaiting fuller rationale."}
      </p>

      <div className="leaderboard-line">
        <span>Score {formatNumber(bid.score)}</span>
        <span>Conf {formatNumber(bid.confidence)}</span>
        <span>Risk {riskLabel(bid.risk)}</span>
      </div>

      <div className="leaderboard-line">
        <span>Runtime {formatRuntime(bid.estimated_runtime_seconds ?? 0)}</span>
        <span>Friction {scoreLabel(bid.policy_friction_score)}</span>
        <span>Reliance {scoreLabel(bid.capability_reliance_score)}</span>
      </div>

      {bid.search_summary ? (
        <div className="leaderboard-sim">
          <strong>Monte Carlo</strong>
          <p>{bid.search_summary}</p>
          <div className="leaderboard-line">
            <span>Samples {formatInteger(diagnostics.sample_count ?? 0)}</span>
            <span>Success {scoreLabel(diagnostics.success_rate)}</span>
            <span>Rollback {scoreLabel(diagnostics.rollback_rate)}</span>
          </div>
        </div>
      ) : null}

      <div className="file-token-list">
        <span className="muted-chip">Required: {skillList(bid, "required_skills")}</span>
        <span className="muted-chip">Optional: {skillList(bid, "optional_skills")}</span>
        {envelope ? <span className="muted-chip">Envelope: {envelope}</span> : null}
      </div>

      <div className="leaderboard-line leaderboard-line-strong">
        <span>{formatInteger(totalTokens)} tok</span>
        <span>{formatCurrency(totalCost)}</span>
      </div>

      {bid.rejection_reason ? (
        <p className="leaderboard-note">{bid.rejection_reason}</p>
      ) : bid.usage_unavailable_reason ? (
        <p className="leaderboard-note">{bid.usage_unavailable_reason}</p>
      ) : bid.exact_action ? (
        <p className="leaderboard-note">{bid.exact_action}</p>
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
  biddingState,
  usageSummary,
  events = []
}) {
  const marketBids = bids.filter((bid) => {
    if (!activeTaskId) {
      return true;
    }
    return (
      bid.task_id === activeTaskId ||
      bid.bid_id === winnerBidId ||
      bid.bid_id === standbyBidId ||
      Boolean(bid.rejection_reason)
    );
  });
  const missionTotals = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const taskTotals = usageSummary?.active_task ?? { total_tokens: 0, total_cost: 0 };
  const orderedBids = [...marketBids].sort((left, right) => {
    const leftStatus = bidStatusFor(left, winnerBidId, standbyBidId, activePhase);
    const rightStatus = bidStatusFor(right, winnerBidId, standbyBidId, activePhase);
    if (leftStatus.rank !== rightStatus.rank) {
      return rightStatus.rank - leftStatus.rank;
    }
    return (
      Number(right.score ?? right.confidence ?? -1) - Number(left.score ?? left.confidence ?? -1)
    );
  });
  const biddingMode = biddingState?.generation_mode ?? marketBids[0]?.generation_mode ?? null;
  const roundTokens =
    taskTotals.total_tokens && taskTotals.total_tokens > 0
      ? taskTotals.total_tokens
      : marketBids.reduce((total, bid) => total + metricTotal(bid.token_usage), 0);
  const roundCost =
    taskTotals.total_cost && taskTotals.total_cost > 0
      ? taskTotals.total_cost
      : marketBids.reduce((total, bid) => total + metricTotal(bid.cost_usage), 0);
  const providerCount = new Set(marketBids.map((bid) => providerLabelForBid(bid))).size;
  const liveEntries = bidTickerEntries(events, activeTaskId);
  const rejectedCount = marketBids.filter((bid) => Boolean(bid.rejection_reason)).length;
  const leadingBid = orderedBids[0] ?? null;

  return (
    <section className="panel strategy-board">
      <div className="arena-topline">
        <div>
          <p className="eyebrow">Strategy Market</p>
          <h2>Competing plans stay visible</h2>
          <p className="arena-topline-copy">
            Winner, standby, and rejected contenders share one board so you can see what lost
            and why.
          </p>
        </div>
        <span className="arena-phase">{humanizePhase(activePhase)}</span>
      </div>

      <div className="arena-quickstats">
        <div className="arena-stat">
          <span>Round</span>
          <strong>{Math.max(activeBidRound || 0, 1)}</strong>
          <p>{activeTaskId || "market-wide bidding"}</p>
        </div>
        <div className="arena-stat">
          <span>Contenders</span>
          <strong>{orderedBids.length}</strong>
          <p>{providerCount} providers visible in the current market</p>
        </div>
        <div className="arena-stat">
          <span>Blocked</span>
          <strong>{formatInteger(rejectedCount)}</strong>
          <p>{leadingBid ? `${leadingBid.role ?? "Top"} is currently ahead` : "Awaiting first leader"}</p>
        </div>
        <div className="arena-stat arena-stat-spend">
          <span>Spend</span>
          <strong>{formatInteger(roundTokens || missionTotals.total_tokens || 0)} tok</strong>
          <p>{formatCurrency(roundCost || missionTotals.total_cost || 0)}</p>
        </div>
      </div>

      {biddingState?.warning || biddingState?.architecture_violation || biddingMode === "deterministic_fallback" ? (
        <div className={`arena-bidding-banner arena-bidding-banner-${biddingState?.architecture_violation ? "rejected" : "standby"}`}>
          <strong>{biddingState?.architecture_violation ? "Architecture violation" : "Strategy notice"}</strong>
          <p>
            {biddingState?.architecture_violation ??
              biddingState?.warning ??
              "This round is running with degraded bidding signals."}
          </p>
        </div>
      ) : null}

      <div className="arena-bid-ticker">
        {liveEntries.length ? (
          liveEntries.map((entry) => (
            <article key={`${entry.event_type}-${entry.id}`} className="arena-ticker-item">
              <strong>{humanizeEventType(entry.event_type)}</strong>
              <p>{entry.message}</p>
              <span>{timeLabel(entry.created_at)}</span>
            </article>
          ))
        ) : (
          <div className="leaderboard-empty">The strategy tape will populate as bids are generated.</div>
        )}
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
          <div className="leaderboard-empty">Strategies are forming for the next market move.</div>
        )}
      </div>
    </section>
  );
}
