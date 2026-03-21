import { useEffect, useMemo, useState } from "react";

import StatusBadge from "./StatusBadge";
import {
  formatCurrency,
  formatRuntime,
  humanizeMissionStage,
  shortCommit
} from "../lib/format";

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

function statusSummary(mission, usageSummary) {
  const missionUsage = usageSummary?.mission ?? { total_cost: 0 };
  const latestCheckpoint = mission.accepted_checkpoints?.at(-1) ?? null;
  const validation = mission.validation_report;
  const civicCount = Object.keys(mission.civic_audit_summary ?? {}).length;
  const leader =
    mission.bids?.find((bid) => bid.bid_id === mission.winner_bid_id) ??
    mission.bids?.find((bid) => bid.selected) ??
    null;
  const leaderLabel = leader?.role ?? leader?.strategy_family ?? "Awaiting winner";

  return [
    {
      label: "Mission health",
      value: mission.outcome ?? mission.run_state,
      detail: humanizeMissionStage(mission.active_phase)
    },
    {
      label: "Latest checkpoint",
      value: latestCheckpoint?.label ?? leaderLabel,
      detail: latestCheckpoint ? shortCommit(latestCheckpoint.commit_sha ?? mission.head_commit) : "Current leader"
    },
    {
      label: "Civic status",
      value: civicCount ? `${civicCount} audit${civicCount === 1 ? "" : "s"}` : "Idle",
      detail: civicCount ? "Governance evidence captured" : "No audit activity yet"
    },
    {
      label: "Validator status",
      value: validation ? (validation.passed ? "Passed" : "Needs review") : "Pending",
      detail: formatCurrency(missionUsage.total_cost ?? 0)
    }
  ];
}

export default function MissionHeader({
  mission,
  usageSummary,
  busy,
  activeTab,
  onTabChange,
  onPause,
  onResume,
  onCancel
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (mission.run_state !== "running") {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [mission.run_state]);

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
  const fallbackStart = mission.created_at ?? mission.events?.[0]?.created_at ?? null;
  const fallbackEnd =
    mission.run_state === "finalized"
      ? mission.updated_at ?? mission.events?.at(-1)?.created_at ?? fallbackStart
      : now;
  const elapsedSeconds = useMemo(() => {
    if (typeof mission.runtime_seconds === "number" && mission.runtime_seconds > 0) {
      return mission.runtime_seconds;
    }
    if (!fallbackStart || !fallbackEnd) {
      return 0;
    }
    return Math.max(0, (new Date(fallbackEnd).getTime() - new Date(fallbackStart).getTime()) / 1000);
  }, [fallbackEnd, fallbackStart, mission.runtime_seconds]);
  const statusCards = statusSummary(mission, usageSummary);

  return (
    <header className="mission-header-shell panel">
      <div className="mission-header-topline">
        <div className="mission-wordmark">
          <div className="brand-mark brand-mark-small" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <p className="eyebrow">Helix Runtime</p>
            <div className="mission-title-row">
              <strong className="mission-bar-title">{repoLabel(mission.repo_path)}</strong>
              <StatusBadge value={mission.outcome ?? mission.run_state} />
            </div>
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

      <div className="mission-header-body">
        <div className="mission-objective-card">
          <p className="eyebrow">Mission Objective</p>
          <h1>{mission.objective}</h1>
          <div className="mission-header-meta">
            <span>Repo: {repoLabel(mission.repo_path)}</span>
            <span>Task: {activeTask}</span>
            <span>Status: {humanizeMissionStage(mission.active_phase)}</span>
            <span>Branch: {mission.branch_name ?? "branch pending"}</span>
            <span>Spend: {formatCurrency(missionUsage.total_cost)}</span>
            <span>Time elapsed: {formatRuntime(elapsedSeconds)}</span>
          </div>
        </div>

        <div className="mission-status-cluster">
          {statusCards.map((card) => (
            <article key={card.label} className="mission-status-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
      </div>

      <div className="mission-header-lower">
        <div className="mission-checkpoint-strip">
          <span>
            Head commit <strong>{shortCommit(mission.head_commit ?? acceptedCheckpoint?.commit_sha)}</strong>
          </span>
          <span>
            Latest checkpoint <strong>{acceptedCheckpoint?.label ?? "Awaiting acceptance"}</strong>
          </span>
          <span>
            Total spend <strong>{formatCurrency(missionUsage.total_cost)}</strong>
          </span>
        </div>

        <nav className="mission-tab-switcher" aria-label="Mission workspace">
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
