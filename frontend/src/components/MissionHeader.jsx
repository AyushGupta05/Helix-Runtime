import StatusBadge from "./StatusBadge";

export default function MissionHeader({ mission, busy, onPause, onResume, onCancel }) {
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

  return (
    <header className="mission-header panel">
      <div className="mission-header-copy">
        <p className="eyebrow">Mission {mission.mission_id}</p>
        <h1>{mission.objective}</h1>
        <p className="mission-subtitle">{mission.repo_path}</p>
        <div className="mission-badges">
          <StatusBadge value={mission.run_state} />
          <StatusBadge value={mission.active_phase} />
          {mission.outcome ? <StatusBadge value={mission.outcome} /> : null}
        </div>
      </div>
      <div className="mission-header-meta">
        <div className="artifact-pill">
          <span>Branch</span>
          <strong>{mission.branch_name ?? "pending"}</strong>
        </div>
        <div className="artifact-pill">
          <span>Head</span>
          <strong>{mission.head_commit?.slice(0, 10) ?? "n/a"}</strong>
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
