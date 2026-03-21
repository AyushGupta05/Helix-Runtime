import { useEffect, useMemo, useState } from "react";

import StatusBadge from "./StatusBadge";
import { formatRuntime, humanizeMissionStage } from "../lib/format";

const TABS = [
  { id: "live", label: "Live Market" },
  { id: "intelligence", label: "Mission Intelligence" },
  { id: "outcome", label: "Outcome" }
];

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? repoPath ?? "repo";
}

function findGithubAuthChallenge(mission) {
  const actions = [...(mission.recent_civic_actions ?? [])].reverse();
  return (
    actions.find((entry) => entry?.output_payload?.authorization_url) ??
    actions.find((entry) => entry?.payload?.output_payload?.authorization_url) ??
    null
  );
}

export default function MissionHeader({
  mission,
  busy,
  activeTab,
  onTabChange,
  onResume,
  onCancel
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const controls =
    mission.run_state === "paused"
        ? [
            { label: "Resume", action: onResume, type: "primary" },
            { label: "Cancel", action: onCancel, type: "danger" }
          ]
        : mission.run_state === "running"
          ? [{ label: "Cancel", action: onCancel, type: "danger" }]
        : mission.run_state === "cancelling"
          ? [{ label: "Cancelling...", action: null, type: "ghost", disabled: true }]
          : [];
  const fallbackStart = mission.created_at ?? mission.events?.[0]?.created_at ?? mission.updated_at ?? null;
  const runtimeAnchor = mission.updated_at ?? mission.events?.at(-1)?.created_at ?? fallbackStart;
  const elapsedSeconds = useMemo(() => {
    const baseRuntime = Number(mission.runtime_seconds ?? 0);
    if (mission.run_state === "running" && runtimeAnchor) {
      const delta = Math.max(0, (now - new Date(runtimeAnchor).getTime()) / 1000);
      return baseRuntime + delta;
    }
    if (baseRuntime > 0) {
      return baseRuntime;
    }
    const fallbackEnd =
      mission.run_state === "finalized"
        ? runtimeAnchor ?? fallbackStart
        : now;
    if (!fallbackStart || !fallbackEnd) {
      return 0;
    }
    return Math.max(0, (new Date(fallbackEnd).getTime() - new Date(fallbackStart).getTime()) / 1000);
  }, [fallbackStart, mission.run_state, mission.runtime_seconds, now, runtimeAnchor]);
  const civicChallenge = findGithubAuthChallenge(mission);
  const civicAuthUrl =
    civicChallenge?.output_payload?.authorization_url ??
    civicChallenge?.payload?.output_payload?.authorization_url ??
    null;
  const activeSkills = Array.isArray(mission.available_skills) ? mission.available_skills.length : 0;
  const metaItems = [
    repoLabel(mission.repo_path),
    humanizeMissionStage(mission.active_phase),
    `Elapsed ${formatRuntime(elapsedSeconds)}`,
    `${activeSkills} civic skill${activeSkills === 1 ? "" : "s"}`
  ];

  return (
    <header className="mission-topbar panel">
      <div className="mission-topbar-row">
        <div className="mission-topbar-copy">
          <p className="eyebrow">Live Prompt</p>
          <h1 className="mission-topbar-prompt">{mission.objective}</h1>
        </div>

        <div className="mission-topbar-actions">
          {civicAuthUrl ? (
            <a
              className="ghost-button"
              href={civicAuthUrl}
              target="_blank"
              rel="noreferrer"
            >
              Connect GitHub
            </a>
          ) : null}
          {controls.map((control) => (
            <button
              key={control.label}
              className={`${control.type}-button`}
              disabled={busy || control.disabled}
              onClick={control.action ?? undefined}
              type="button"
            >
              {control.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mission-topbar-lower">
        <div className="mission-topbar-meta">
          <StatusBadge value={mission.outcome ?? mission.run_state} quiet />
          {metaItems.map((item) => (
            <span key={item} className="mission-topbar-meta-item">
              {item}
            </span>
          ))}
        </div>

        <nav className="mission-tab-switcher mission-tab-switcher-compact" aria-label="Mission workspace">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`mission-tab ${activeTab === tab.id ? "is-active" : ""}`}
              onClick={() => onTabChange(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
