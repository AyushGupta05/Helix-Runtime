import { humanizeToken, relativeTime, summarizeProvider } from "../lib/format";

function categoryFor(trace) {
  const value = trace.trace_type || trace.event_type || "";
  if (value.startsWith("bid.") || value.startsWith("market.") || value.startsWith("simulation.")) {
    return "market";
  }
  if (value.startsWith("tool.") || value.startsWith("diff.")) {
    return "tool";
  }
  if (value.startsWith("validation.")) {
    return "validation";
  }
  if (value.startsWith("recovery.") || value.startsWith("standby.")) {
    return "recovery";
  }
  if (value.startsWith("checkpoint.")) {
    return "checkpoint";
  }
  if (value.startsWith("provider.")) {
    return "provider";
  }
  return "task";
}

function isReasoningTrace(entry) {
  const type = entry.trace_type || entry.event_type || "";
  return type.startsWith("provider.invocation.") || type === "proposal.selected";
}

function totalFrom(values) {
  return Object.values(values ?? {}).reduce((total, value) => total + Number(value || 0), 0);
}

function preview(value, fallback) {
  if (!value) {
    return fallback;
  }
  return value.length > 320 ? `${value.slice(0, 320)}...` : value;
}

function FeedCard({ entry }) {
  const type = entry.trace_type || entry.event_type || "";
  const category = categoryFor(entry);

  return (
    <article className={`pulse-card pulse-card-${entry.status ?? "info"}`}>
      <div className="pulse-card-head">
        <div>
          <strong>{entry.title || humanizeToken(type)}</strong>
          <p>{entry.message}</p>
        </div>
        <span>{relativeTime(entry.created_at)}</span>
      </div>
      <div className="trace-chip-row">
        <span className="timeline-chip">{humanizeToken(type)}</span>
        <span className="timeline-chip">{category}</span>
        {entry.task_id ? <span className="timeline-chip">{entry.task_id}</span> : null}
        {entry.provider ? <span className="timeline-chip">{summarizeProvider(entry.provider)}</span> : null}
      </div>
      {entry.payload?.details || entry.payload?.reason || entry.payload?.summary ? (
        <p className="pulse-card-detail">
          {entry.payload.summary || entry.payload.details || entry.payload.reason}
        </p>
      ) : null}
    </article>
  );
}

function ReasoningCard({ entry }) {
  const tokens = totalFrom(entry.payload?.token_usage);
  const cost = totalFrom(entry.payload?.cost_usage);
  const type = entry.trace_type || entry.event_type || "";

  return (
    <article className={`reasoning-card reasoning-card-${entry.status ?? "info"}`}>
      <div className="pulse-card-head">
        <div>
          <strong>{entry.title || humanizeToken(type)}</strong>
          <p>{entry.message}</p>
        </div>
        <span>{relativeTime(entry.created_at)}</span>
      </div>
      <div className="trace-chip-row">
        <span className="timeline-chip">{entry.provider ? summarizeProvider(entry.provider) : "system"}</span>
        {entry.payload?.model_id ? <span className="timeline-chip">{entry.payload.model_id}</span> : null}
        {entry.lane ? <span className="timeline-chip">{entry.lane}</span> : null}
        {entry.task_id ? <span className="timeline-chip">{entry.task_id}</span> : null}
      </div>
      {tokens || cost ? (
        <div className="trace-inline-stats">
          <span>{tokens} tokens</span>
          <span>${cost.toFixed(4)}</span>
        </div>
      ) : null}
      <div className="reasoning-body">
        {entry.payload?.summary ? (
          <div className="reasoning-block">
            <span>Summary</span>
            <p>{entry.payload.summary}</p>
          </div>
        ) : null}
        {entry.payload?.prompt_preview ? (
          <details className="trace-detail" open={type === "proposal.selected"}>
            <summary>Prompt preview</summary>
            <pre>{preview(entry.payload.prompt_preview, "No prompt preview.")}</pre>
          </details>
        ) : null}
        {entry.payload?.response_preview ? (
          <details className="trace-detail">
            <summary>Response preview</summary>
            <pre>{preview(entry.payload.response_preview, "No response preview.")}</pre>
          </details>
        ) : null}
        {entry.payload?.error ? (
          <div className="reasoning-block">
            <span>Error</span>
            <p>{entry.payload.error}</p>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export default function TimelinePanel({ trace, validationReport }) {
  const ordered = [...trace].sort((left, right) => right.id - left.id);
  const actionFeed = ordered.filter((entry) => !isReasoningTrace(entry)).slice(0, 16);
  const reasoningFeed = ordered.filter(isReasoningTrace).slice(0, 12);
  const providerCallCount = ordered.filter((entry) =>
    (entry.trace_type || entry.event_type || "").startsWith("provider.invocation.")
  ).length;
  const marketMoveCount = ordered.filter((entry) => categoryFor(entry) === "market").length;

  return (
    <div className="pulse-layout">
      <section className="pulse-column">
        <div className="pulse-column-head">
          <div>
            <p className="eyebrow">Live Action Feed</p>
            <h3>Operator tape</h3>
          </div>
          <div className="pulse-mini-stats">
            <span>{marketMoveCount} market moves</span>
            <span>{actionFeed.length} visible actions</span>
          </div>
        </div>
        {validationReport ? (
          <div className={`trace-validation ${validationReport.passed ? "is-pass" : "is-fail"}`}>
            Latest validation: {validationReport.passed ? "passed" : "failed"} for {validationReport.task_id}
          </div>
        ) : null}
        <div className="pulse-list">
          {actionFeed.length ? (
            actionFeed.map((entry) => <FeedCard key={`${entry.trace_type}-${entry.id}`} entry={entry} />)
          ) : (
            <div className="pulse-empty">Waiting for mission activity.</div>
          )}
        </div>
      </section>

      <section className="pulse-column">
        <div className="pulse-column-head">
          <div>
            <p className="eyebrow">Live Reasoning</p>
            <h3>Provider calls and proposal picks</h3>
          </div>
          <div className="pulse-mini-stats">
            <span>{providerCallCount} provider calls</span>
            <span>{reasoningFeed.length} visible traces</span>
          </div>
        </div>
        <div className="pulse-list">
          {reasoningFeed.length ? (
            reasoningFeed.map((entry) => (
              <ReasoningCard key={`${entry.trace_type}-${entry.id}`} entry={entry} />
            ))
          ) : (
            <div className="pulse-empty">Prompt and response previews will appear here once providers start reasoning.</div>
          )}
        </div>
      </section>
    </div>
  );
}
