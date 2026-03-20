import { formatNumber } from "../lib/format";

function KeyValue({ label, value }) {
  return (
    <div className="kv">
      <span>{label}</span>
      <strong>{value || "n/a"}</strong>
    </div>
  );
}

export default function ArtifactsPanel({ mission, selectedBid }) {
  return (
    <div className="artifacts-grid">
      <section className="artifact-card">
        <h3>Branch Output</h3>
        <KeyValue label="Branch" value={mission.branch_name} />
        <KeyValue label="Head commit" value={mission.head_commit?.slice(0, 12)} />
        <KeyValue label="Latest event" value={String(mission.latest_event_id)} />
      </section>
      <section className="artifact-card">
        <h3>Diff Scope</h3>
        <pre>{mission.latest_diff_summary || "No diff summary yet."}</pre>
      </section>
      <section className="artifact-card">
        <h3>Selected Bid</h3>
        {selectedBid ? (
          <>
            <KeyValue label="Role" value={selectedBid.role} />
            <KeyValue label="Strategy" value={selectedBid.strategy_family} />
            <KeyValue label="Score" value={formatNumber(selectedBid.score)} />
            <p>{selectedBid.strategy_summary}</p>
            <p className="artifact-list">{selectedBid.touched_files.join(", ") || "No declared file scope"}</p>
          </>
        ) : (
          <p>No bid selected yet.</p>
        )}
      </section>
      <section className="artifact-card">
        <h3>Decision History</h3>
        <ul className="artifact-bullets">
          {mission.decision_history.length ? (
            mission.decision_history.map((entry) => <li key={entry}>{entry}</li>)
          ) : (
            <li>Decision history will appear as Arbiter advances the mission.</li>
          )}
        </ul>
      </section>
      <section className="artifact-card">
        <h3>Failed Attempts</h3>
        <ul className="artifact-bullets">
          {mission.failed_attempt_history.length ? (
            mission.failed_attempt_history.map((entry) => <li key={entry}>{entry}</li>)
          ) : (
            <li>No failed attempts recorded.</li>
          )}
        </ul>
      </section>
      <section className="artifact-card">
        <h3>Validation Notes</h3>
        <ul className="artifact-bullets">
          {mission.validation_report?.notes?.length ? (
            mission.validation_report.notes.map((note) => <li key={note}>{note}</li>)
          ) : (
            <li>No validation notes yet.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
