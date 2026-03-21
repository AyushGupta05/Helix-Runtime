import { useEffect, useMemo, useRef } from "react";

import {
  MISSION_STAGE_ORDER,
  formatInteger,
  humanizeEventType,
  humanizeMissionStage,
  shortCommit,
  summarizeProvider
} from "../lib/format";
import { deriveMissionPhase } from "../lib/missionStream";

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
    .slice(-18);
}

function toneForEvent(eventType) {
  if (eventType.includes("failed") || eventType.includes("cancelled") || eventType.includes("violation")) {
    return "danger";
  }
  if (eventType.includes("recovery") || eventType.includes("reverted")) {
    return "warning";
  }
  if (eventType.includes("accepted") || eventType.includes("passed") || eventType.includes("won")) {
    return "success";
  }
  if (eventType.includes("started") || eventType.includes("opened") || eventType.includes("selected")) {
    return "accent";
  }
  return "neutral";
}

function contextText(entry) {
  const payload = entry.payload ?? {};
  const segments = [];
  if (payload.task_id) {
    segments.push(`task ${payload.task_id}`);
  }
  if (payload.bid_id) {
    segments.push(`bid ${payload.bid_id.slice(0, 8)}`);
  }
  if (payload.provider) {
    segments.push(summarizeProvider(payload.provider));
  }
  if (payload.lane) {
    segments.push(payload.lane);
  }
  if (payload.commit_sha) {
    segments.push(shortCommit(payload.commit_sha));
  }
  if (payload.outcome) {
    segments.push(String(payload.outcome).replace(/_/g, " "));
  }
  return segments.join(" | ");
}

function stageLabel(stage) {
  return humanizeMissionStage(stage);
}

export default function EventStrip({ mission, events = [], trace = [] }) {
  const entries = normalize(events);
  const latestTrace = useMemo(() => [...(trace ?? [])].reverse()[0] ?? null, [trace]);
  const scrollerRef = useRef(null);

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [entries.length, latestTrace?.id]);

  return (
    <section className="timeline-shell">
      <div className="timeline-head">
        <div>
          <p className="eyebrow">Mission Timeline</p>
          <h2>Live governance feed</h2>
          <p className="timeline-copy">
            Every material transition is recorded here, with stage, task, bid, provider, and checkpoint context.
          </p>
        </div>
        <div className="timeline-headline">
          <span className="timeline-headline-label">Current phase</span>
          <strong>{stageLabel(mission?.active_phase ?? "idle")}</strong>
          <p>
            Run state: {humanizeMissionStage(mission?.run_state ?? "idle")}
          </p>
        </div>
      </div>

      <div className="timeline-stage-rail" aria-label="Mission stage progression">
        {MISSION_STAGE_ORDER.map((stage) => {
          const active = mission?.active_phase === stage;
          const complete = MISSION_STAGE_ORDER.indexOf(mission?.active_phase ?? "idle") > MISSION_STAGE_ORDER.indexOf(stage);
          return (
            <span
              key={stage}
              className={`timeline-stage ${active ? "is-active" : ""} ${complete ? "is-complete" : ""}`}
            >
              {stageLabel(stage)}
            </span>
          );
        })}
      </div>

      <div className="timeline-meta">
        <span className="timeline-meta-pill">Events: {formatInteger(entries.length)}</span>
        <span className="timeline-meta-pill">
          Latest event: {entries[entries.length - 1]?.event_type ? humanizeEventType(entries[entries.length - 1].event_type) : "Waiting"}
        </span>
        <span className="timeline-meta-pill">
          Latest trace: {latestTrace ? humanizeEventType(latestTrace.trace_type) : "Waiting"}
        </span>
        <span className="timeline-meta-pill">
          Checkpoints: {formatInteger(mission?.accepted_checkpoints?.length ?? 0)}
        </span>
      </div>

      <div className="timeline-feed" ref={scrollerRef}>
        {entries.length ? (
          entries.map((entry) => {
            const payload = entry.payload ?? {};
            const tone = toneForEvent(entry.event_type ?? "");
            const context = contextText(entry);
            const stage = stageLabel(
              deriveMissionPhase(
                entry.event_type,
                payload.active_phase ?? payload.phase ?? mission?.active_phase ?? "idle"
              )
            );
            return (
              <article key={`${entry.event_type}-${entry.id}`} className={`timeline-entry tone-${tone}`}>
                <div className="timeline-entry-top">
                  <div className="timeline-entry-title">
                    <span className="timeline-event-type">{humanizeEventType(entry.event_type)}</span>
                    <span className="timeline-stage-chip">{stage}</span>
                  </div>
                  <time className="timeline-time" dateTime={entry.created_at}>
                    {formatTimestamp(entry.created_at)}
                  </time>
                </div>
                <p className="timeline-message">{entry.message}</p>
                <div className="timeline-entry-context">
                  {context ? <span>{context}</span> : <span>Context not attached</span>}
                  {payload.summary ? <span>{payload.summary}</span> : null}
                </div>
              </article>
            );
          })
        ) : (
          <div className="timeline-empty">
            Waiting for the first mission event.
          </div>
        )}
      </div>
    </section>
  );
}
