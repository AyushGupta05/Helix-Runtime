import StatusBadge from "./StatusBadge";
import { relativeTime } from "../lib/format";

export default function MissionHistoryList({ missions, loading, onSelect }) {
  return (
    <section className="history panel-like">
      <div className="section-title">
        <h2>Mission History</h2>
        <p>Single active mission per process, plus resumable local history.</p>
      </div>
      {loading ? <div className="history-empty">Loading missions...</div> : null}
      {!loading && missions.length === 0 ? (
        <div className="history-empty">No missions yet. Start one from the composer above.</div>
      ) : null}
      <div className="history-list">
        {missions.map((mission) => (
          <button
            key={mission.mission_id}
            className="history-item"
            onClick={() => onSelect(mission)}
          >
            <div className="history-item-head">
              <strong>{mission.objective}</strong>
              <StatusBadge value={mission.outcome ?? mission.run_state} quiet />
            </div>
            <p>{mission.repo_path}</p>
            <div className="history-item-meta">
              <span>{mission.branch_name ?? "branch pending"}</span>
              <span>{relativeTime(mission.updated_at)}</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
