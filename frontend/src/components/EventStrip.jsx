function formatTimestamp(value) {
  if (!value) {
    return "--:--:--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function normalize(events) {
  return [...(events ?? [])]
    .sort((left, right) => Number(left.id ?? 0) - Number(right.id ?? 0))
    .slice(-10);
}

export default function EventStrip({ events = [] }) {
  const entries = normalize(events);

  return (
    <div className="event-strip">
      <p className="eyebrow">Live Event Strip</p>
      <div className="event-strip-track">
        {entries.length ? (
          entries.map((entry) => (
            <span key={`${entry.event_type}-${entry.id}`} className="event-pill">
              [{formatTimestamp(entry.created_at)}] {entry.message}
            </span>
          ))
        ) : (
          <span className="event-pill">Waiting for live mission events.</span>
        )}
      </div>
    </div>
  );
}
