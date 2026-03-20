import { useMemo, useState } from "react";
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
import BidBoard from "./components/BidBoard";
import ArtifactsPanel from "./components/ArtifactsPanel";
import EventStrip from "./components/EventStrip";

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
            A mission is already running. It will stay secondary until you open it explicitly.
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
  const latestProposalTrace = [...trace].reverse().find((entry) => entry.trace_type === "proposal.selected");
  const selectedBid =
    mission.bids.find((bid) => bid.bid_id === mission.winner_bid_id) ??
    mission.bids.find((bid) => bid.selected) ??
    null;
  const latestCheckpoint = mission.accepted_checkpoints?.length
    ? mission.accepted_checkpoints[mission.accepted_checkpoints.length - 1]
    : null;

  return (
    <div className="mission-room operator-console">
      <MissionHeader
        mission={mission}
        usageSummary={usageSummary}
        busy={controlMutation.isPending}
        onPause={() => controlMutation.mutate("pause")}
        onResume={() => controlMutation.mutate("resume")}
        onCancel={() => controlMutation.mutate("cancel")}
      />

      <div className="market-layout">
        <section className="panel panel-arena">
          <BidBoard
            bids={mission.bids}
            winnerBidId={mission.winner_bid_id}
            standbyBidId={mission.standby_bid_id}
            activeTaskId={mission.active_task_id}
            activePhase={mission.active_phase}
            activeBidRound={mission.active_bid_round}
            simulationRound={mission.simulation_round}
            usageSummary={usageSummary}
          />
        </section>

        <aside className="side-rail">
          <ArtifactsPanel
            mission={mission}
            diffState={diffState}
            usageSummary={usageSummary}
            selectedBid={selectedBid}
            latestProposalTrace={latestProposalTrace}
            latestCheckpoint={latestCheckpoint}
          />
        </aside>
      </div>

      <section className="panel panel-event-strip">
        <EventStrip events={mission.events} />
      </section>
    </div>
  );
}

function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const isMissionView = location.pathname.startsWith("/missions/");
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
    <div className={`app-shell ${isMissionView ? "app-shell-mission" : ""}`}>
      {!isMissionView ? (
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
      ) : null}
      <main className={`main-stage ${isMissionView ? "main-stage-mission" : ""}`}>
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
