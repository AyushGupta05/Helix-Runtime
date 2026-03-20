import StatusBadge from "./StatusBadge";
import { formatCurrency, formatInteger } from "../lib/format";

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? repoPath ?? "repo";
}

export default function MissionHeader({
  mission,
  usageSummary,
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
  const activeTask = mission.active_task?.task_id ?? mission.active_task_id ?? "Waiting";

  return (
    <header className="mission-ribbon panel">
      <div className="mission-ribbon-topline">
        <div className="mission-bar-main">
          <div className="mission-bar-brand">
            <strong className="mission-bar-title">Arbiter</strong>
            <span className="mission-bar-divider">|</span>
            <span className="mission-bar-objective">{mission.objective}</span>
            <StatusBadge value={mission.outcome ?? mission.run_state} />
          </div>
          <div className="mission-bar-meta">
            <span className="mission-bar-meta-item" title={mission.repo_path}>
              Repo: {repoLabel(mission.repo_path)}
            </span>
            <span className="mission-bar-meta-item">Task: {activeTask}</span>
            <span className="mission-bar-meta-item">
              Tokens: {formatInteger(missionUsage.total_tokens)}
            </span>
            <span className="mission-bar-meta-item">
              Cost: {formatCurrency(missionUsage.total_cost)}
            </span>
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
    </header>
  );
}
