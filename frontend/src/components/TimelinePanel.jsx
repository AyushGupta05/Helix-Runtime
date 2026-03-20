import { humanizeToken, relativeTime } from "../lib/format";

function payloadSummary(event) {
  const payload = event.payload ?? {};
  if (payload.task_id) {
    return payload.task_id;
  }
  if (payload.bid_id) {
    return payload.bid_id;
  }
  if (payload.commit_sha) {
    return payload.commit_sha.slice(0, 10);
  }
  if (payload.reason) {
    return payload.reason;
  }
  return "";
}

export default function TimelinePanel({ events, validationReport }) {
  const recent = [...events].sort((left, right) => right.id - left.id);
  return (
    <div className="timeline">
      {validationReport ? (
        <div className="timeline-validation">
          <strong>Latest validation</strong>
          <p>{validationReport.passed ? "Passed" : "Failed"} for {validationReport.task_id}</p>
          {validationReport.notes?.length ? (
            <ul>
              {validationReport.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <div className="timeline-list">
        {recent.map((event) => (
          <div key={event.id} className={`timeline-item timeline-${event.event_type.replace(/\./g, "-")}`}>
            <div className="timeline-item-head">
              <strong>{humanizeToken(event.event_type)}</strong>
              <span>{relativeTime(event.created_at)}</span>
            </div>
            <p>{event.message}</p>
            {payloadSummary(event) ? <span className="timeline-chip">{payloadSummary(event)}</span> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
