import { formatCurrency, formatInteger, humanizeEventType, summarizeBidOrigin, summarizeProvider } from "../lib/format";

function feedTime(value) {
  if (!value) {
    return "--:--";
  }
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function metricsFor(mission, usageSummary) {
  const activeBids = mission.bids.filter((bid) => bid.task_id === mission.active_task_id);
  return [
    {
      label: "Strategies generated",
      value: String(activeBids.length),
      detail: `${mission.bidding_state?.active_provider_bids ?? 0} provider-backed`
    },
    {
      label: "Blocked by policy",
      value: String(activeBids.filter((bid) => Boolean(bid.rejection_reason)).length),
      detail: mission.bidding_state?.architecture_violation ? "architecture guard tripped" : "policy + budget screens"
    },
    {
      label: "Recovery count",
      value: String(mission.recovery_round ?? 0),
      detail: mission.failure_context?.failure_type ?? "steady state"
    },
    {
      label: "Mission spend",
      value: formatCurrency(usageSummary?.mission?.total_cost ?? 0),
      detail: `${formatInteger(usageSummary?.mission?.total_tokens ?? 0)} tokens`
    }
  ];
}

function providerLedger(usageSummary) {
  return Object.values(usageSummary?.by_provider ?? {})
    .sort((left, right) => right.total_tokens - left.total_tokens)
    .slice(0, 4);
}

function feedEntries(trace) {
  return [...(trace ?? [])]
    .reverse()
    .filter((entry) => entry.title || entry.message)
    .slice(0, 10);
}

export default function MissionLiveView({
  mission,
  trace,
  usageSummary,
  selectedBid,
  latestProposalTrace,
  children
}) {
  const metrics = metricsFor(mission, usageSummary);
  const ledger = providerLedger(usageSummary);
  const feed = feedEntries(trace);

  return (
    <div className="workspace-view workspace-live">
      <div className="live-market-grid">
        <div className="live-market-main">{children}</div>

        <aside className="live-market-side">
          <section className="panel live-side-card">
            <div className="section-title">
              <h2>Mission Health</h2>
              <p>Persistent awareness for operators while the market keeps moving.</p>
            </div>
            <div className="metric-grid">
              {metrics.map((metric) => (
                <article key={metric.label} className="metric-card">
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                  <p>{metric.detail}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="panel live-side-card">
            <div className="section-title">
              <h2>Current Leader</h2>
              <p>The active edge, why it is ahead, and what Helix is executing right now.</p>
            </div>
            <div className="leader-highlight">
              <div className="leader-highlight-top">
                <strong>{selectedBid ? summarizeBidOrigin(selectedBid) : "No leader selected yet"}</strong>
                <span>{selectedBid ? summarizeProvider(selectedBid.provider) : "Waiting"}</span>
              </div>
              <p>{latestProposalTrace?.payload?.summary ?? selectedBid?.mission_rationale ?? "The market is still evaluating bounded work units."}</p>
            </div>
            <div className="provider-ledger">
              {ledger.length ? (
                ledger.map((row) => (
                  <div key={row.provider} className="provider-ledger-row">
                    <strong>{summarizeProvider(row.provider)}</strong>
                    <span>
                      {formatInteger(row.total_tokens)} tok | {formatCurrency(row.total_cost)}
                    </span>
                  </div>
                ))
              ) : (
                <p className="muted-copy">Provider spend will appear here once bidding begins.</p>
              )}
            </div>
          </section>

          <section className="panel live-side-card">
            <div className="section-title">
              <h2>Live Stream</h2>
              <p>High-frequency mission motion without dropping you into raw terminal noise.</p>
            </div>
            <div className="feed-list">
              {feed.length ? (
                feed.map((entry) => (
                  <article key={`${entry.trace_type}-${entry.id}`} className="feed-item">
                    <div className="feed-item-top">
                      <strong>{entry.title ? humanizeEventType(entry.title) : humanizeEventType(entry.trace_type)}</strong>
                      <span>{feedTime(entry.created_at)}</span>
                    </div>
                    <p>{entry.message}</p>
                  </article>
                ))
              ) : (
                <div className="feed-empty">Live activity appears here as soon as the mission emits trace updates.</div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
