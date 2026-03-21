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
  winner: "badge-success",
  standby: "badge-ready",
  rejected: "badge-danger",
  simulated: "badge-running",
  generated: "badge-neutral",
  shortlisted: "badge-ready",
  blocked: "badge-danger",
  complete: "badge-success",
  completed: "badge-success",
  failed: "badge-danger",
  pending: "badge-neutral",
  idle: "badge-neutral",
  collect: "badge-market",
  strategize: "badge-market",
  simulate: "badge-market",
  select: "badge-ready",
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
