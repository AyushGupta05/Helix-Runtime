import StatusBadge from "./StatusBadge";
import { MISSION_STAGE_ORDER, formatCurrency, formatInteger, humanizeMissionStage, shortCommit } from "../lib/format";

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
  const acceptedCheckpoint = mission.accepted_checkpoints?.at(-1) ?? null;
  const activeStageIndex = Math.max(MISSION_STAGE_ORDER.indexOf(mission.active_phase), 0);

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
            <span className="mission-bar-meta-item">Phase: {humanizeMissionStage(mission.active_phase)}</span>
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
      <div className="mission-stage-rail" aria-label="Mission stage progression">
        {MISSION_STAGE_ORDER.map((stage, index) => (
          <span
            key={stage}
            className={`mission-stage-pill ${index === activeStageIndex ? "is-active" : ""} ${index < activeStageIndex ? "is-complete" : ""}`}
          >
            {humanizeMissionStage(stage)}
          </span>
        ))}
      </div>
      <div className="mission-ribbon-summary">
        <div className="mission-summary-card">
          <span>Branch</span>
          <strong>{mission.branch_name ?? "branch pending"}</strong>
        </div>
        <div className="mission-summary-card">
          <span>Head commit</span>
          <strong>{shortCommit(mission.head_commit ?? acceptedCheckpoint?.commit_sha)}</strong>
        </div>
        <div className="mission-summary-card">
          <span>Checkpoint</span>
          <strong>{acceptedCheckpoint ? acceptedCheckpoint.label : "Awaiting acceptance"}</strong>
        </div>
      </div>
    </header>
  );
}
