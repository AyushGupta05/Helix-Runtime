import { useMemo, useState } from "react";
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
import MissionComposer from "./components/MissionComposer";
import MissionHistoryList from "./components/MissionHistoryList";
import MissionHeader from "./components/MissionHeader";
import TaskRail from "./components/TaskRail";
import BidBoard from "./components/BidBoard";
import TimelinePanel from "./components/TimelinePanel";
import ArtifactsPanel from "./components/ArtifactsPanel";

function HomePanel({ activeMissionId }) {
  return (
    <section className="hero-panel">
      <p className="eyebrow">Arbiter Operator Console</p>
      <h1>Launch a repo mission and watch every market, provider race, edit, and recovery live.</h1>
      <p className="hero-copy">
        Arbiter now exposes the active task rail, provider market, live trace, isolated
        worktree state, accepted checkpoints, and token burn in one dense control room.
      </p>
      <p className="hero-hint">
        {activeMissionId
          ? "An active mission is already running. Open it from the left rail to inspect the full operator console."
          : "Start with a local repo path and a concrete software objective. The console will hydrate as soon as the mission is created."}
      </p>
    </section>
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
            <h2>Live Trace</h2>
            <span className="panel-meta">Reasoning, provider calls, validation, recovery</span>
          </div>
          <TimelinePanel trace={trace} validationReport={mission.validation_report} />
        </section>

        <section className="panel panel-market">
          <div className="panel-heading">
            <h2>Provider Market</h2>
            <span className="panel-meta">Grouped by strategy family and provider</span>
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
    enabled: Boolean(repoScope),
    refetchInterval: repoScope ? 3000 : false
  });

  const activeMissionId = useMemo(() => {
    const missions = missionsQuery.data ?? [];
    return (
      missions.find((mission) =>
        ["running", "paused", "cancelling"].includes(mission.run_state)
      )?.mission_id ?? null
    );
  }, [missionsQuery.data]);

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
          blocked={Boolean(activeMissionId)}
          error={createMutation.error?.message}
          onSubmit={(payload) => createMutation.mutate(payload)}
        />
        <MissionHistoryList
          missions={missionsQuery.data ?? []}
          loading={missionsQuery.isLoading}
          onSelect={(missionId) => navigate(`/missions/${missionId}?repo=${encodeURIComponent(repoScope)}`)}
        />
      </aside>
      <main className="main-stage">
        <Routes>
          <Route path="/" element={<HomePanel activeMissionId={activeMissionId} />} />
          <Route path="/missions/:missionId" element={<MissionRoute />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return <Shell />;
}
