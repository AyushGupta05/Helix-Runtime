import { useEffect, useMemo, useState } from "react";
import { Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelMission,
  createMission,
  getMissionDiff,
  getMissions,
  getMissionTrace,
  getMissionUsage,
  pauseMission,
  resumeMission
} from "./lib/api";
import { useMissionStream } from "./lib/missionStream";
import { relativeTime } from "./lib/format";
import MissionComposer from "./components/MissionComposer";
import MissionHistoryList from "./components/MissionHistoryList";
import MissionHeader from "./components/MissionHeader";
import MissionIntelligenceView from "./components/MissionIntelligenceView";
import MissionOutcomeView from "./components/MissionOutcomeView";
import MissionLiveView from "./components/MissionLiveView";
import StrategyBiddingScreen from "./components/StrategyBiddingScreen";
import SimulationIntelligenceScreen from "./components/SimulationIntelligenceScreen";
import OutcomeResultsScreen from "./components/OutcomeResultsScreen";
import StatusBadge from "./components/StatusBadge";

const FEATURE_COLUMNS = [
  {
    title: "Understand",
    copy: "Repo scan, constraints, validators, and operating boundaries stay visible from the first minute."
  },
  {
    title: "Compete",
    copy: "Provider-backed bids, simulation, governance, and standby strategies all stay in one live workspace."
  },
  {
    title: "Deliver",
    copy: "Outcome summaries, checkpoints, diff review, and confidence notes are ready for the repo owner."
  }
];

function missionPullRequestUrl(mission) {
  const pullRequest = mission?.mission_output?.pull_request ?? mission?.pull_request ?? null;
  return pullRequest?.html_url ?? pullRequest?.url ?? null;
}

function repoLabel(repoPath) {
  const segments = String(repoPath || "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments[segments.length - 1] ?? repoPath ?? "repo";
}

function HomePanel({
  activeMission,
  missions,
  loading,
  busy,
  error,
  onSubmit,
  onOpenActiveMission,
  onSelectHistory
}) {
  const history = missions.filter(
    (mission) => !["running", "paused", "cancelling"].includes(mission.run_state)
  );

  return (
    <div className="home-stack">
      <section className="panel home-intro-bar">
        <div className="home-intro-copy">
          <div className="brand-lockup brand-lockup-compact">
            <div className="brand-mark brand-mark-small" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <div>
              <p className="eyebrow">Helix Runtime</p>
              <h1>Launch a mission only when you type a prompt.</h1>
            </div>
          </div>
          <p className="home-intro-note">
            The launcher stays in control. Existing missions remain available, but nothing opens or starts from this screen unless you explicitly choose it.
          </p>
        </div>
        <div className="home-intro-status">
          <div className="home-intro-status-copy">
            <span className="muted-chip">Workspace</span>
            <strong>{activeMission ? "Existing mission detected" : "Idle and ready"}</strong>
            <p>
              {activeMission
                ? "A live mission exists in this process, but it stays in the background until you open it."
                : "No mission is running. Submit a prompt when you are ready to begin."}
            </p>
          </div>
          {activeMission ? (
            <div className="home-intro-status-actions">
              <StatusBadge value={activeMission.outcome ?? activeMission.run_state} quiet />
              <button className="ghost-button" onClick={() => onOpenActiveMission(activeMission)}>
                Open Live Workspace
              </button>
            </div>
          ) : null}
        </div>
      </section>

      <section className="home-feature-grid">
        {FEATURE_COLUMNS.map((item) => (
          <article key={item.title} className="panel feature-card">
            <p className="eyebrow">How It Works</p>
            <h2>{item.title}</h2>
            <p>{item.copy}</p>
          </article>
        ))}
      </section>

      <div className="launcher-grid">
        <MissionComposer
          busy={busy}
          blocked={Boolean(activeMission)}
          error={error}
          onSubmit={onSubmit}
          onOpenActiveMission={() => activeMission && onOpenActiveMission(activeMission)}
        />

        <div className="launcher-secondary">
          <section className="panel launcher-active">
            <div className="section-title">
              <h2>{activeMission ? "Current Mission" : "Mission Queue"}</h2>
              <p>
                {activeMission
                  ? "Live work stays one click away while finished missions remain reviewable."
                  : "Finished missions stay here for replay, audit, and comparison."}
              </p>
            </div>
            {activeMission ? (
              <button className="active-mission-card" onClick={() => onOpenActiveMission(activeMission)}>
                <div className="active-mission-head">
                  <strong>{activeMission.objective}</strong>
                  <StatusBadge value={activeMission.outcome ?? activeMission.run_state} quiet />
                </div>
                <div className="active-mission-meta">
                  <span>{repoLabel(activeMission.repo_path)}</span>
                  <span>{activeMission.branch_name ?? "branch pending"}</span>
                  <span>{relativeTime(activeMission.updated_at)}</span>
                </div>
              </button>
            ) : (
              <div className="history-empty">No live mission in this process.</div>
            )}
          </section>

          <MissionHistoryList missions={history} loading={loading} onSelect={onSelectHistory} />
        </div>
      </div>
    </div>
  );
}

function MissionRoute() {
  const { missionId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const repo = searchParams.get("repo") ?? "";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const missionQuery = useMissionStream(missionId, repo);
  const [activeTab, setActiveTab] = useState("live");
  const [intelligenceSection, setIntelligenceSection] = useState("simulation");

  const controlMutation = useMutation({
    mutationFn: async (action) => {
      if (action === "pause") {
        return pauseMission(missionId, repo);
      }
      if (action === "resume") {
        return resumeMission(missionId, repo);
      }
      return cancelMission(missionId, repo);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mission", repo, missionId] });
      queryClient.invalidateQueries({ queryKey: ["mission-trace", repo, missionId] });
      queryClient.invalidateQueries({ queryKey: ["mission-diff", repo, missionId] });
      queryClient.invalidateQueries({ queryKey: ["mission-usage", repo, missionId] });
      queryClient.invalidateQueries({ queryKey: ["missions", repo] });
    }
  });

  const historyQuery = useQuery({
    queryKey: ["missions", repo],
    queryFn: () => getMissions(repo),
    enabled: Boolean(repo)
  });

  const traceQuery = useQuery({
    queryKey: ["mission-trace", repo, missionId],
    queryFn: () => getMissionTrace(missionId, repo),
    enabled: Boolean(missionId && repo)
  });

  const diffQuery = useQuery({
    queryKey: ["mission-diff", repo, missionId],
    queryFn: () => getMissionDiff(missionId, repo),
    enabled: Boolean(missionId && repo)
  });

  const usageQuery = useQuery({
    queryKey: ["mission-usage", repo, missionId],
    queryFn: () => getMissionUsage(missionId, repo),
    enabled: Boolean(missionId && repo)
  });

  useEffect(() => {
    if (missionQuery.data?.run_state === "finalized") {
      setActiveTab((current) => (current === "outcome" ? current : "outcome"));
    }
  }, [missionQuery.data?.run_state]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [activeTab]);

  const mission = missionQuery.data ?? null;
  const pullRequestUrl = missionPullRequestUrl(mission);

  useEffect(() => {
    if (!mission || mission.run_state !== "finalized" || !mission.mission_id || !pullRequestUrl) {
      return;
    }
    try {
      const storageKey = `helix:opened-pr:${mission.mission_id}`;
      if (window.sessionStorage.getItem(storageKey) === pullRequestUrl) {
        return;
      }
      window.sessionStorage.setItem(storageKey, pullRequestUrl);
      const nextWindow = window.open(pullRequestUrl, "_blank", "noopener,noreferrer");
      if (nextWindow && typeof nextWindow.focus === "function") {
        nextWindow.focus();
      }
    } catch {
      // Best-effort behavior only; the PR link is still exposed in the header.
    }
  }, [mission, pullRequestUrl]);

  if (missionQuery.isLoading) {
    return <div className="empty-panel">Hydrating the Helix mission workspace...</div>;
  }

  if (missionQuery.isError || !missionQuery.data) {
    return (
      <div className="empty-panel">
        <p>That mission could not be loaded.</p>
        <button className="ghost-button" onClick={() => navigate("/")}>
          Return to launcher
        </button>
      </div>
    );
  }

  const trace = traceQuery.data ?? mission.recent_trace ?? [];
  const diffState = diffQuery.data ?? { worktree_state: mission.worktree_state ?? {} };
  const usageSummary = usageQuery.data ?? mission.usage_summary ?? {};
  const selectedBid =
    mission.bids.find((bid) => bid.bid_id === mission.winner_bid_id) ??
    mission.bids.find((bid) => bid.selected) ??
    null;
  const latestProposalTrace = [...trace].reverse().find((entry) => entry.trace_type === "proposal.selected");
  const history = (historyQuery.data ?? []).filter((item) => item.mission_id !== mission.mission_id);

  const handleOutcomeJump = (section) => {
    setIntelligenceSection(section);
    setActiveTab("intelligence");
  };

  return (
    <div className="mission-room">
      <MissionHeader
        mission={mission}
        busy={controlMutation.isPending}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onResume={() => controlMutation.mutate("resume")}
        onCancel={() => controlMutation.mutate("cancel")}
      />

      {activeTab === "live" ? (
        <StrategyBiddingScreen
          mission={mission}
          winnerBidId={mission.winner_bid_id}
          standbyBidId={mission.standby_bid_id}
          activePhase={mission.active_phase}
          usageSummary={usageSummary}
        />
      ) : null}

      {activeTab === "intelligence" ? (
        <SimulationIntelligenceScreen
          mission={mission}
          winnerBidId={mission.winner_bid_id}
          standbyBidId={mission.standby_bid_id}
          activePhase={mission.active_phase}
          usageSummary={usageSummary}
        />
      ) : null}

      {activeTab === "outcome" ? (
        <OutcomeResultsScreen
          mission={mission}
          winnerBidId={mission.winner_bid_id}
          usageSummary={usageSummary}
        />
      ) : null}
    </div>
  );
}

function Shell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [repoScope, setRepoScope] = useState("");

  const missionsQuery = useQuery({
    queryKey: ["missions", repoScope],
    queryFn: () => getMissions(repoScope),
    enabled: Boolean(repoScope),
    refetchInterval: repoScope ? 3000 : false
  });

  const activeMission = useMemo(() => {
    const missions = missionsQuery.data ?? [];
    return missions.find((mission) =>
      ["running", "paused", "cancelling"].includes(mission.run_state)
    ) ?? null;
  }, [missionsQuery.data]);

  const openMission = (mission) => {
    navigate(`/missions/${mission.mission_id}?repo=${encodeURIComponent(repoScope || mission.repo_path || "")}`);
  };

  const createMutation = useMutation({
    mutationFn: createMission,
    onSuccess: (response, variables) => {
      const recent = JSON.parse(window.localStorage.getItem("arbiter:recent-repos") ?? "[]");
      const merged = [variables.repo, ...recent.filter((value) => value !== variables.repo)].slice(0, 6);
      window.localStorage.setItem("arbiter:recent-repos", JSON.stringify(merged));
      setRepoScope(response.repo_path ?? variables.repo);
      queryClient.invalidateQueries({ queryKey: ["missions", variables.repo] });
      navigate(`/missions/${response.mission_id}?repo=${encodeURIComponent(response.repo_path ?? variables.repo)}`);
    }
  });

  return (
    <div className="app-shell">
      <main className="main-stage">
        <Routes>
          <Route
            path="/"
            element={
              <HomePanel
                activeMission={activeMission}
                missions={missionsQuery.data ?? []}
                loading={missionsQuery.isLoading}
                busy={createMutation.isPending}
                error={createMutation.error?.message}
                onSubmit={(payload) => createMutation.mutateAsync(payload)}
                onOpenActiveMission={openMission}
                onSelectHistory={openMission}
              />
            }
          />
          <Route path="/missions/:missionId" element={<MissionRoute />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return <Shell />;
}
