const TONES = {
  running: "badge-running",
  paused: "badge-paused",
  cancelling: "badge-paused",
  finalized: "badge-neutral",
  success: "badge-success",
  partial_success: "badge-warning",
  failed_execution: "badge-danger",
  failed_safe_stop: "badge-warning",
  ready: "badge-ready",
  completed: "badge-success",
  failed: "badge-danger",
  pending: "badge-neutral",
  market: "badge-market",
  execute: "badge-running",
  validate: "badge-ready",
  recover: "badge-warning",
  finalize: "badge-neutral"
};

function prettify(value) {
  return value.replace(/_/g, " ");
}

export default function StatusBadge({ value, quiet = false }) {
  return (
    <span className={`status-badge ${TONES[value] ?? "badge-neutral"} ${quiet ? "status-badge-quiet" : ""}`}>
      {prettify(value)}
    </span>
  );
}
