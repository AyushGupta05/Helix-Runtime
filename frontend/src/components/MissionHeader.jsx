import StatusBadge from "./StatusBadge";
import { formatCurrency, formatInteger, summarizeProvider } from "../lib/format";

function Metric({ label, value, emphasis = false }) {
  return (
    <div className={`mission-metric ${emphasis ? "mission-metric-strong" : ""}`}>
      <span>{label}</span>
      <strong>{value || "n/a"}</strong>
    </div>
  );
}

export default function MissionHeader({
  mission,
  usageSummary,
  latestProposalTrace,
  latestCheckpoint,
  busy,
  onPause,
  onResume,
  onCancel
}) {
  const controls =
    mission.run_state === "running"
      ? [
          { label: "Pause", action: onPause, type: "ghost" },
          { label: "Cancel", action: onCancel, type: "danger" }
        ]
      : mission.run_state === "paused"
        ? [
            { label: "Resume", action: onResume, type: "primary" },
            { label: "Cancel", action: onCancel, type: "danger" }
          ]
        : mission.run_state === "cancelling"
          ? [{ label: "Cancelling...", action: null, type: "ghost", disabled: true }]
          : [];

  const missionUsage = usageSummary?.mission ?? { total_tokens: 0, total_cost: 0 };
  const latestValidation = mission.validation_report
    ? `${mission.validation_report.passed ? "Passed" : "Failed"} ${mission.validation_report.task_id}`
    : "No validation yet";
  const currentProvider =
    latestProposalTrace?.provider ||
    mission.bids.find((bid) => bid.bid_id === mission.winner_bid_id)?.provider ||
    "system";

  return (
    <header className="mission-ribbon panel">
      <div className="mission-ribbon-topline">
        <div className="mission-ribbon-main">
          <p className="eyebrow">Mission {mission.mission_id}</p>
          <h1>{mission.objective}</h1>
          <p className="mission-subtitle">{mission.repo_path}</p>
          <div className="mission-badges">
            <StatusBadge value={mission.run_state} />
            <StatusBadge value={mission.active_phase} />
            {mission.outcome ? <StatusBadge value={mission.outcome} /> : null}
          </div>
        </div>
        <div className="mission-controls">
          {controls.map((control) => (
            <button
              key={control.label}
              className={`${control.type}-button`}
              disabled={busy || control.disabled}
              onClick={control.action ?? undefined}
            >
              {control.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mission-ribbon-metrics">
        <Metric label="Active task" value={mission.active_task?.task_id ?? mission.active_task_id} emphasis />
        <Metric label="Winner provider" value={summarizeProvider(currentProvider)} />
        <Metric label="Latest validation" value={latestValidation} />
        <Metric label="Latest checkpoint" value={latestCheckpoint?.commit_sha?.slice(0, 12) ?? mission.head_commit?.slice(0, 12)} />
        <Metric label="Mission tokens" value={formatInteger(missionUsage.total_tokens)} emphasis />
        <Metric label="Mission cost" value={formatCurrency(missionUsage.total_cost)} />
        <Metric label="Branch" value={mission.branch_name} />
        <Metric label="Head" value={mission.head_commit?.slice(0, 12)} />
      </div>
      <div className="mission-ribbon-footer">
        <div className="mission-signal">
          <span>Selected provider</span>
          <strong>{summarizeProvider(currentProvider)}</strong>
        </div>
        <div className="mission-signal">
          <span>Latest validation</span>
          <strong>{latestValidation}</strong>
        </div>
        <div className="mission-signal">
          <span>Accepted checkpoint</span>
          <strong>{latestCheckpoint?.commit_sha?.slice(0, 12) ?? mission.head_commit?.slice(0, 12) ?? "pending"}</strong>
        </div>
      </div>
    </header>
  );
}
