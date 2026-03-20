import { useMemo } from "react";
import { Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelMission,
  createMission,
  getMission,
  getMissions,
  pauseMission,
  resumeMission
} from "./lib/api";
import { useMissionStream } from "./lib/missionStream";
import MissionComposer from "./components/MissionComposer";
import MissionHistoryList from "./components/MissionHistoryList";
import MissionHeader from "./components/MissionHeader";
import MissionGraph from "./components/MissionGraph";
import BidBoard from "./components/BidBoard";
import TimelinePanel from "./components/TimelinePanel";
import ArtifactsPanel from "./components/ArtifactsPanel";

function HomePanel({ activeMissionId }) {
  return (
    <section className="hero-panel">
      <p className="eyebrow">Arbiter Mission Control</p>
      <h1>Launch a repo mission, watch the market form, and follow every recovery live.</h1>
      <p className="hero-copy">
        Arbiter decomposes the objective into tasks, opens a bounded strategy market,
        selects a winner and standby, validates each result, and keeps recovering
        until it lands a safe branch-ready change.
      </p>
      {activeMissionId ? (
        <p className="hero-hint">
          An active mission is already in flight. Select it from the left rail to step
          into the live control room.
        </p>
      ) : (
        <p className="hero-hint">
          Start with a local repo path and a concrete objective. The control room will
          hydrate as soon as the mission is created.
        </p>
      )}
    </section>
  );
}

function MissionRoute() {
  const { missionId = "" } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const missionQuery = useMissionStream(missionId);

  const controlMutation = useMutation({
    mutationFn: async (action) => {
      if (action === "pause") {
        return pauseMission(missionId);
      }
      if (action === "resume") {
        return resumeMission(missionId);
      }
      return cancelMission(missionId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mission", missionId] });
      queryClient.invalidateQueries({ queryKey: ["missions"] });
    }
  });

  if (missionQuery.isLoading) {
    return <div className="empty-panel">Hydrating mission snapshot...</div>;
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
  const selectedBid =
    mission.bids.find((bid) => bid.bid_id === mission.winner_bid_id) ??
    mission.bids.find((bid) => bid.selected) ??
    mission.bids[0] ??
    null;

  return (
    <div className="mission-room">
      <MissionHeader
        mission={mission}
        busy={controlMutation.isPending}
        onPause={() => controlMutation.mutate("pause")}
        onResume={() => controlMutation.mutate("resume")}
        onCancel={() => controlMutation.mutate("cancel")}
      />
      <div className="mission-grid">
        <section className="panel panel-graph">
          <div className="panel-heading">
            <h2>Task Graph</h2>
            <span className="panel-meta">Dependency-aware mission DAG</span>
          </div>
          <MissionGraph tasks={mission.tasks} />
        </section>
        <section className="panel panel-bids">
          <div className="panel-heading">
            <h2>Bid Market</h2>
            <span className="panel-meta">Winner + standby selection in real time</span>
          </div>
          <BidBoard
            bids={mission.bids}
            winnerBidId={mission.winner_bid_id}
            standbyBidId={mission.standby_bid_id}
          />
        </section>
        <section className="panel panel-timeline">
          <div className="panel-heading">
            <h2>Live Timeline</h2>
            <span className="panel-meta">Execution, validation, recovery, checkpoints</span>
          </div>
          <TimelinePanel
            events={mission.events}
            validationReport={mission.validation_report}
          />
        </section>
        <section className="panel panel-artifacts">
          <div className="panel-heading">
            <h2>Artifacts</h2>
            <span className="panel-meta">Diff scope, branch output, selected bid, reports</span>
          </div>
          <ArtifactsPanel mission={mission} selectedBid={selectedBid} />
        </section>
      </div>
    </div>
  );
}

function Shell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const missionsQuery = useQuery({
    queryKey: ["missions"],
    queryFn: getMissions,
    refetchInterval: 3000
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
      queryClient.invalidateQueries({ queryKey: ["missions"] });
      navigate(`/missions/${response.mission_id}`);
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
          onSelect={(missionId) => navigate(`/missions/${missionId}`)}
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
