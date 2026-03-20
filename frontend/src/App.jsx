import { useEffect, useMemo, useState } from "react";
import {
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams
} from "react-router-dom";
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
import MissionComposer from "./components/MissionComposer";
import MissionHistoryList from "./components/MissionHistoryList";
import MissionHeader from "./components/MissionHeader";
import TaskRail from "./components/TaskRail";
import BidBoard from "./components/BidBoard";
import ArtifactsPanel from "./components/ArtifactsPanel";
import LiveFeedPanel from "./components/LiveFeedPanel";

function HomePanel({ activeMission, onOpenActiveMission }) {
  return (
    <div className="home-stack">
      <section className="hero-panel">
        <p className="eyebrow">Arbiter Operator Console</p>
        <h1>Run a mission room that feels alive: market pressure, tool activity, reasoning, and recovery in one place.</h1>
        <p className="hero-copy">
          The new console is built around operator signals instead of bland panels. You can
          track the active task, see which strategy won, inspect live model calls, and follow
          validation or rollback events as they land.
        </p>
        <div className="hero-grid">
          <article className="hero-stat-card">
            <span>Strategy Market</span>
            <strong>Win / standby / fail cards</strong>
            <p>Clear contender status, provider identity, and risk at a glance.</p>
          </article>
          <article className="hero-stat-card">
            <span>Action Tape</span>
            <strong>Live operator feed</strong>
            <p>Bidding, execution, validation, checkpoints, and recovery stream in order.</p>
          </article>
          <article className="hero-stat-card">
            <span>Reasoning Stream</span>
            <strong>Prompt and response previews</strong>
            <p>Provider invocations and proposal selection stay visible instead of buried.</p>
          </article>
        </div>
      </section>

      {activeMission ? (
        <section className="hero-panel hero-panel-compact">
          <div className="hero-active-head">
            <div>
              <p className="eyebrow">Live Mission</p>
              <h2>{activeMission.objective}</h2>
            </div>
            <button className="primary-button" onClick={() => onOpenActiveMission(activeMission)}>
              Open live room
            </button>
          </div>
          <p className="hero-hint">
            A mission is already active in this process. Use the live room instead of launching a second run.
          </p>
        </section>
      ) : null}
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

  if (missionQuery.isLoading) {
    return <div className="empty-panel">Hydrating operator console...</div>;
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

  const mission = missionQuery.data;
  const trace = traceQuery.data ?? mission.recent_trace ?? [];
  const diffState = diffQuery.data ?? { worktree_state: mission.worktree_state ?? {} };
  const usageSummary = usageQuery.data ?? mission.usage_summary ?? {};
  const invocations = usageSummary.invocations ?? [];
  const latestProposalTrace = [...trace].reverse().find((entry) => entry.trace_type === "proposal.selected");
  const selectedBid =
    mission.bids.find((bid) => bid.bid_id === mission.winner_bid_id) ??
    mission.bids.find((bid) => bid.selected) ??
    mission.bids[0] ??
    null;
  const latestCheckpoint = mission.accepted_checkpoints?.length
    ? mission.accepted_checkpoints[mission.accepted_checkpoints.length - 1]
    : null;

  return (
    <div className="mission-room operator-console">
      <MissionHeader
        mission={mission}
        usageSummary={usageSummary}
        latestProposalTrace={latestProposalTrace}
        latestCheckpoint={latestCheckpoint}
        busy={controlMutation.isPending}
        onPause={() => controlMutation.mutate("pause")}
        onResume={() => controlMutation.mutate("resume")}
        onCancel={() => controlMutation.mutate("cancel")}
      />

      <div className="operator-grid">
        <section className="panel panel-task-rail">
          <TaskRail
            tasks={mission.tasks}
            activeTaskId={mission.active_task_id}
            bids={mission.bids}
            executionSteps={mission.execution_steps ?? []}
            validationReport={mission.validation_report}
            winnerBidId={mission.winner_bid_id}
            standbyBidId={mission.standby_bid_id}
          />
        </section>

        <section className="panel panel-trace">
          <div className="panel-heading">
            <h2>Mission Pulse</h2>
            <span className="panel-meta">
              {mission.events.length} events, {trace.length} trace entries, {invocations.length} model calls
            </span>
          </div>
          <LiveFeedPanel
            events={mission.events}
            trace={trace}
            invocations={invocations}
            validationReport={mission.validation_report}
            executionSteps={mission.execution_steps ?? []}
          />
        </section>

        <section className="panel panel-market">
          <div className="panel-heading">
            <h2>Strategy Market</h2>
            <span className="panel-meta">Contenders, outcomes, risk, and provider posture</span>
          </div>
          <BidBoard
            bids={mission.bids}
            winnerBidId={mission.winner_bid_id}
            standbyBidId={mission.standby_bid_id}
            activeTaskId={mission.active_task_id}
            providerMarketSummary={mission.provider_market_summary}
          />
        </section>

        <section className="panel panel-inspectors">
          <div className="panel-heading">
            <h2>Inspectors</h2>
            <span className="panel-meta">Repo changes, checkpoints, selected proposal, usage</span>
          </div>
          <ArtifactsPanel
            mission={mission}
            diffState={diffState}
            usageSummary={usageSummary}
            selectedBid={selectedBid}
          />
        </section>
      </div>
    </div>
  );
}

function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [repoScope, setRepoScope] = useState(() => {
    try {
      return JSON.parse(window.localStorage.getItem("arbiter:recent-repos") ?? "[]")[0] ?? "";
    } catch {
      return "";
    }
  });
  const missionsQuery = useQuery({
    queryKey: ["missions", repoScope],
    queryFn: () => getMissions(repoScope),
    refetchInterval: 3000
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

  useEffect(() => {
    if (!activeMission || location.pathname !== "/") {
      return;
    }
    navigate(
      `/missions/${activeMission.mission_id}?repo=${encodeURIComponent(repoScope || activeMission.repo_path || "")}`,
      { replace: true }
    );
  }, [activeMission, location.pathname, navigate, repoScope]);

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
      <aside className="sidebar">
        <div className="sidebar-brand">
          <p className="eyebrow">Autonomous Mission Runner</p>
          <h1>Arbiter</h1>
        </div>
        <MissionComposer
          busy={createMutation.isPending}
          blocked={Boolean(activeMission)}
          activeMission={activeMission}
          error={createMutation.error?.message}
          onSubmit={(payload) => createMutation.mutate(payload)}
          onOpenActiveMission={() => activeMission && openMission(activeMission)}
        />
        <MissionHistoryList
          missions={missionsQuery.data ?? []}
          loading={missionsQuery.isLoading}
          onSelect={(mission) =>
            navigate(
              `/missions/${mission.mission_id}?repo=${encodeURIComponent(repoScope || mission.repo_path || "")}`
            )
          }
        />
      </aside>
      <main className="main-stage">
        <Routes>
          <Route
            path="/"
            element={<HomePanel activeMission={activeMission} onOpenActiveMission={openMission} />}
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
