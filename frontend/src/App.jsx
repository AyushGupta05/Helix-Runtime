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
import { relativeTime } from "./lib/format";
import MissionComposer from "./components/MissionComposer";
import MissionHistoryList from "./components/MissionHistoryList";
import MissionHeader from "./components/MissionHeader";
import BidBoard from "./components/BidBoard";
import ArtifactsPanel from "./components/ArtifactsPanel";
import EventStrip from "./components/EventStrip";
import StatusBadge from "./components/StatusBadge";

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
      <section className="panel launcher-header">
        <p className="eyebrow">Arbiter Mission Launcher</p>
        <div className="launcher-title-row">
          <h1>Launch with a prompt. Open a live room only when you choose to.</h1>
          {activeMission ? <StatusBadge value={activeMission.outcome ?? activeMission.run_state} /> : null}
        </div>
        <p className="launcher-copy">
          The default state is idle. Arbiter should only move once a user submits an
          objective, while any existing live mission stays visible but secondary.
        </p>
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
          <section className="panel-like launcher-active">
            <div className="section-title">
              <h2>{activeMission ? "Live Mission" : "Ready State"}</h2>
              <p>
                {activeMission
                  ? "A live run exists, but it does not take over the screen unless you open it."
                  : "No mission is active. Launch a new run from the left panel."}
              </p>
            </div>
            {activeMission ? (
              <div className="launcher-active-card">
                <div className="launcher-active-head">
                  <strong>{activeMission.objective}</strong>
                  <StatusBadge value={activeMission.outcome ?? activeMission.run_state} quiet />
                </div>
                <div className="launcher-active-meta">
                  <span>{activeMission.repo_path}</span>
                  <span>{activeMission.branch_name ?? "branch pending"}</span>
                  <span>{relativeTime(activeMission.updated_at)}</span>
                </div>
                <button className="primary-button" onClick={() => onOpenActiveMission(activeMission)}>
                  Open live room
                </button>
              </div>
            ) : (
              <div className="history-empty">No mission is running in this process.</div>
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
    <div className="mission-room">
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
            biddingState={mission.bidding_state}
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
        <EventStrip mission={mission} events={mission.events} trace={trace} />
      </section>
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
                onSubmit={(payload) => createMutation.mutate(payload)}
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
