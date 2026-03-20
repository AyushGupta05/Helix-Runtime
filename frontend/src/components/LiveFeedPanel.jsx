import React from "react";

function humanize(value) {
  if (!value) {
    return "unknown";
  }
  return String(value)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[._-]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^./, (char) => char.toUpperCase());
}

function formatTime(value) {
  if (!value) {
    return "just now";
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "just now";
  }
  const delta = Date.now() - timestamp;
  const seconds = Math.max(1, Math.floor(delta / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function sortValue(value, fallback) {
  const timestamp = value ? new Date(value).getTime() : NaN;
  if (!Number.isNaN(timestamp)) {
    return timestamp;
  }
  return fallback;
}

function isActionEvent(entry) {
  const type = String(entry?.event_type || entry?.trace_type || "").toLowerCase();
  return (
    type.startsWith("mission.") ||
    type.startsWith("task.") ||
    type.startsWith("market.") ||
    type.startsWith("bid.") ||
    type.startsWith("tool.") ||
    type.startsWith("validation.") ||
    type.startsWith("recovery.") ||
    type.startsWith("checkpoint.") ||
    type.startsWith("simulation.")
  );
}

function isReasoningTrace(entry) {
  const type = String(entry?.trace_type || entry?.event_type || "").toLowerCase();
  return (
    type.startsWith("provider.invocation.") ||
    type === "proposal.selected" ||
    type.startsWith("validation.") ||
    type.startsWith("recovery.")
  );
}

function normalizeActionEntry(entry) {
  const id = Number(entry?.id ?? 0);
  return {
    id,
    sortKey: sortValue(entry?.created_at, id),
    title: entry?.title || humanize(entry?.event_type || entry?.trace_type),
    message: entry?.message || entry?.payload?.summary || entry?.payload?.details || "",
    type: entry?.event_type || entry?.trace_type || "event",
    created_at: entry?.created_at,
    task_id: entry?.payload?.task_id ?? entry?.task_id ?? null,
    bid_id: entry?.payload?.bid_id ?? entry?.bid_id ?? null,
    provider: entry?.payload?.provider ?? entry?.provider ?? null,
    lane: entry?.payload?.lane ?? entry?.lane ?? null,
    status: entry?.payload?.status ?? entry?.status ?? null,
    payload: entry?.payload ?? {}
  };
}

function normalizeReasoningEntry(entry) {
  const id = Number(entry?.id ?? 0);
  return {
    id,
    sortKey: sortValue(entry?.created_at, id),
    title: entry?.title || humanize(entry?.trace_type || entry?.event_type),
    message: entry?.message || entry?.payload?.summary || entry?.payload?.details || "",
    type: entry?.trace_type || entry?.event_type || "trace",
    created_at: entry?.created_at,
    task_id: entry?.task_id ?? entry?.payload?.task_id ?? null,
    bid_id: entry?.bid_id ?? entry?.payload?.bid_id ?? null,
    provider: entry?.provider ?? entry?.payload?.provider ?? null,
    lane: entry?.lane ?? entry?.payload?.lane ?? null,
    model_id: entry?.payload?.model_id ?? entry?.model_id ?? null,
    prompt_preview: entry?.payload?.prompt_preview ?? entry?.prompt_preview ?? null,
    response_preview: entry?.payload?.response_preview ?? entry?.response_preview ?? null,
    payload: entry?.payload ?? {}
  };
}

function deriveActionEntries(events, executionSteps) {
  const eventEntries = (events ?? []).filter(isActionEvent).map(normalizeActionEntry);
  const stepEntries = (executionSteps ?? []).map((step, index) => ({
    id: index + 1,
    sortKey: sortValue(step?.timestamp || step?.created_at, index + 1),
    title: step?.description || humanize(step?.action_type || step?.tool_name || "execution step"),
    message: step?.tool_name ? `${humanize(step.tool_name)} executed` : "Execution step recorded",
    type: "execution.step",
    created_at: step?.timestamp || step?.created_at || null,
    task_id: step?.task_id ?? null,
    bid_id: step?.bid_id ?? null,
    provider: step?.governance_state ?? null,
    lane: step?.tool_name ?? null,
    status: step?.governance_state ?? null,
    payload: step ?? {}
  }));

  return [...eventEntries, ...stepEntries].sort((left, right) => right.sortKey - left.sortKey);
}

function deriveReasoningEntries(trace, invocations) {
  const traceEntries = (trace ?? [])
    .filter(isReasoningTrace)
    .map(normalizeReasoningEntry);
  const invocationEntries = (invocations ?? []).map((invocation, index) => ({
    id: index + 1,
    sortKey: sortValue(invocation?.completed_at || invocation?.started_at, index + 1),
    title: `${humanize(invocation?.provider || "provider")} ${humanize(invocation?.invocation_kind || "invocation")}`,
    message:
      invocation?.model_id ||
      invocation?.prompt_preview ||
      invocation?.response_preview ||
      `${humanize(invocation?.status || "completed")} invocation`,
    type: "provider.invocation",
    created_at: invocation?.completed_at || invocation?.started_at || null,
    task_id: invocation?.task_id ?? null,
    bid_id: invocation?.bid_id ?? null,
    provider: invocation?.provider ?? null,
    lane: invocation?.lane ?? null,
    model_id: invocation?.model_id ?? null,
    prompt_preview: invocation?.prompt_preview ?? null,
    response_preview: invocation?.response_preview ?? null,
    payload: invocation ?? {}
  }));

  return [...invocationEntries, ...traceEntries].sort((left, right) => right.sortKey - left.sortKey);
}

function EntryChips({ entry }) {
  return (
    <div className="live-feed-chips">
      {entry.task_id ? <span className="live-feed-chip">{entry.task_id}</span> : null}
      {entry.bid_id ? <span className="live-feed-chip">{entry.bid_id}</span> : null}
      {entry.provider ? <span className="live-feed-chip">{humanize(entry.provider)}</span> : null}
      {entry.lane ? <span className="live-feed-chip">{entry.lane}</span> : null}
      {entry.model_id ? <span className="live-feed-chip">{entry.model_id}</span> : null}
      {entry.status ? <span className="live-feed-chip">{humanize(entry.status)}</span> : null}
    </div>
  );
}

function LiveEntry({ entry, kind }) {
  return (
    <article className={`live-feed-entry live-feed-entry-${kind}`}>
      <div className="live-feed-entry-head">
        <div>
          <p className="live-feed-entry-title">{entry.title}</p>
          <p className="live-feed-entry-message">{entry.message || "No details provided."}</p>
        </div>
        <time className="live-feed-entry-time">{formatTime(entry.created_at)}</time>
      </div>
      <EntryChips entry={entry} />
      {kind === "reasoning" && entry.prompt_preview ? (
        <details className="live-feed-detail">
          <summary>Prompt preview</summary>
          <pre>{entry.prompt_preview}</pre>
        </details>
      ) : null}
      {kind === "reasoning" && entry.response_preview ? (
        <details className="live-feed-detail">
          <summary>Response preview</summary>
          <pre>{entry.response_preview}</pre>
        </details>
      ) : null}
      {kind === "action" && entry.payload?.reason ? (
        <p className="live-feed-entry-note">{entry.payload.reason}</p>
      ) : null}
    </article>
  );
}

export default function LiveFeedPanel({
  events = [],
  trace = [],
  invocations = [],
  validationReport = null,
  executionSteps = []
}) {
  const actionEntries = deriveActionEntries(events, executionSteps);
  const reasoningEntries = deriveReasoningEntries(trace, invocations);

  return (
    <section className="live-feed-panel">
      <div className="live-feed-header">
        <div>
          <p className="eyebrow">Live Feed</p>
          <h2>Action and model activity</h2>
        </div>
        {validationReport ? (
          <div className={`live-feed-validation ${validationReport.passed ? "is-pass" : "is-fail"}`}>
            Latest validation {validationReport.passed ? "passed" : "failed"} for {validationReport.task_id}
          </div>
        ) : null}
      </div>

      <div className="live-feed-grid">
        <article className="live-feed-column">
          <div className="live-feed-column-head">
            <h3>Live action feed</h3>
            <span>{actionEntries.length} items</span>
          </div>
          <div className="live-feed-list">
            {actionEntries.length ? (
              actionEntries.map((entry) => <LiveEntry key={`${entry.type}-${entry.id}`} entry={entry} kind="action" />)
            ) : (
              <p className="live-feed-empty">No live actions yet.</p>
            )}
          </div>
        </article>

        <article className="live-feed-column">
          <div className="live-feed-column-head">
            <h3>Live model activity / reasoning</h3>
            <span>{reasoningEntries.length} items</span>
          </div>
          <div className="live-feed-list">
            {reasoningEntries.length ? (
              reasoningEntries.map((entry) => <LiveEntry key={`${entry.type}-${entry.id}`} entry={entry} kind="reasoning" />)
            ) : (
              <p className="live-feed-empty">No model activity yet.</p>
            )}
          </div>
        </article>
      </div>
    </section>
  );
}
