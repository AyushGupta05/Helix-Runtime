import StatusBadge from "./StatusBadge";
import { formatRuntime, humanizeMissionStage } from "../lib/format";
import { useMissionElapsedSeconds } from "../lib/useMissionElapsed";

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

function pullRequestUrlForMission(mission) {
  const pullRequest = mission?.mission_output?.pull_request ?? mission?.pull_request ?? null;
  return pullRequest?.html_url ?? pullRequest?.url ?? null;
}

export default function MissionHeader({
  mission,
  busy,
  activeTab,
  onTabChange,
  onResume,
  onCancel
}) {
  const elapsedSeconds = useMissionElapsedSeconds(mission);
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
  const civicChallenge = findGithubAuthChallenge(mission);
  const civicAuthUrl =
    civicChallenge?.output_payload?.authorization_url ??
    civicChallenge?.payload?.output_payload?.authorization_url ??
    null;
  const pullRequestUrl = pullRequestUrlForMission(mission);
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
          {pullRequestUrl ? (
            <a
              className="primary-button"
              href={pullRequestUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open PR
            </a>
          ) : null}
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
