import StatusBadge from "./StatusBadge";
import { relativeTime } from "../lib/format";

export default function MissionHistoryList({ missions, loading, onSelect }) {
  return (
    <section className="history panel-like">
      <div className="section-title">
        <h2>Recent Missions</h2>
        <p>Manual open only. Finished runs stay available here for review or resume.</p>
      </div>
      {loading ? <div className="history-empty">Loading missions...</div> : null}
      {!loading && missions.length === 0 ? (
        <div className="history-empty">No finished missions yet.</div>
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
