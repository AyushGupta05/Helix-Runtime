import { useMemo, useState } from "react";

import { humanizeToken, relativeTime, summarizeProvider } from "../lib/format";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "task", label: "Task" },
  { key: "market", label: "Market" },
  { key: "provider", label: "Provider" },
  { key: "tool", label: "Tool" },
  { key: "validation", label: "Validation" },
  { key: "recovery", label: "Recovery" },
  { key: "checkpoint", label: "Checkpoint" }
];

function categoryFor(trace) {
  const value = trace.trace_type || trace.event_type || "";
  if (value.startsWith("task.")) return "task";
  if (value.startsWith("bid.") || value.startsWith("market.") || value.startsWith("simulation.")) return "market";
  if (value.startsWith("provider.")) return "provider";
  if (value.startsWith("tool.") || value.startsWith("diff.")) return "tool";
  if (value.startsWith("validation.")) return "validation";
  if (value.startsWith("recovery.") || value.startsWith("standby.")) return "recovery";
  if (value.startsWith("checkpoint.")) return "checkpoint";
  return "task";
}

export default function TimelinePanel({ trace, validationReport }) {
  const [filter, setFilter] = useState("all");

  const filtered = useMemo(() => {
    const ordered = [...trace].sort((left, right) => right.id - left.id);
    if (filter === "all") {
      return ordered;
    }
    return ordered.filter((entry) => categoryFor(entry) === filter);
  }, [filter, trace]);

  return (
    <div className="trace-console">
      <div className="trace-console-head">
        <div className="trace-filters">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              className={`trace-filter ${filter === item.key ? "trace-filter-active" : ""}`}
              onClick={() => setFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {validationReport ? (
          <div className={`trace-validation ${validationReport.passed ? "is-pass" : "is-fail"}`}>
            Latest validation: {validationReport.passed ? "passed" : "failed"} for {validationReport.task_id}
          </div>
        ) : null}
      </div>
      <div className="trace-list">
        {filtered.map((entry) => (
          <article key={`${entry.trace_type}-${entry.id}`} className={`trace-item trace-item-${entry.status ?? "info"}`}>
            <div className="trace-item-head">
              <div>
                <strong>{entry.title || humanizeToken(entry.trace_type || entry.event_type)}</strong>
                <p>{entry.message}</p>
              </div>
              <span>{relativeTime(entry.created_at)}</span>
            </div>
            <div className="trace-chip-row">
              <span className="timeline-chip">{humanizeToken(entry.trace_type || entry.event_type)}</span>
              {entry.task_id ? <span className="timeline-chip">{entry.task_id}</span> : null}
              {entry.provider ? <span className="timeline-chip">{summarizeProvider(entry.provider)}</span> : null}
              {entry.lane ? <span className="timeline-chip">{entry.lane}</span> : null}
            </div>
            {entry.payload?.token_usage ? (
              <div className="trace-inline-stats">
                <span>tokens {Object.values(entry.payload.token_usage).reduce((total, value) => total + Number(value || 0), 0)}</span>
                <span>cost {Object.values(entry.payload.cost_usage ?? {}).reduce((total, value) => total + Number(value || 0), 0).toFixed(4)}</span>
              </div>
            ) : null}
            {entry.payload?.summary || entry.payload?.details || entry.payload?.reason || entry.payload?.error ? (
              <details className="trace-detail">
                <summary>Trace details</summary>
                <div className="trace-detail-grid">
                  {entry.payload.summary ? <p><strong>Summary:</strong> {entry.payload.summary}</p> : null}
                  {entry.payload.details ? <p><strong>Details:</strong> {entry.payload.details}</p> : null}
                  {entry.payload.reason ? <p><strong>Reason:</strong> {entry.payload.reason}</p> : null}
                  {entry.payload.error ? <p><strong>Error:</strong> {entry.payload.error}</p> : null}
                  {entry.payload.prompt_preview ? (
                    <div>
                      <strong>Prompt preview</strong>
                      <pre>{entry.payload.prompt_preview}</pre>
                    </div>
                  ) : null}
                  {entry.payload.response_preview ? (
                    <div>
                      <strong>Response preview</strong>
                      <pre>{entry.payload.response_preview}</pre>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}
