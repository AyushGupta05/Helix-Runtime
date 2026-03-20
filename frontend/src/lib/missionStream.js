import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getMission, openMissionEvents } from "./api";

function replaceTask(tasks, taskId, nextStatus) {
  return tasks.map((task) =>
    task.task_id === taskId ? { ...task, status: nextStatus } : task
  );
}

function ensureBid(existingBids, event) {
  const payload = event.payload ?? {};
  if (existingBids.some((bid) => bid.bid_id === payload.bid_id)) {
    return existingBids;
  }
  return [
    ...existingBids,
    {
      bid_id: payload.bid_id,
      task_id: payload.task_id,
      role: payload.role ?? "Unknown",
      provider: payload.provider ?? "system",
      lane: payload.lane ?? null,
      model_id: payload.model_id ?? null,
      strategy_family: payload.strategy_family ?? "pending",
      strategy_summary: payload.strategy_family ?? "Live contender",
      score: payload.score ?? null,
      confidence: payload.confidence ?? null,
      risk: payload.risk ?? 0,
      cost: payload.cost ?? 0,
      estimated_runtime_seconds: payload.estimated_runtime_seconds ?? 0,
      touched_files: payload.touched_files ?? [],
      validator_plan: payload.validator_plan ?? [],
      rollback_plan: payload.rollback_plan ?? null,
      rollout_level: payload.rollout_level ?? null,
      search_summary: payload.search_summary ?? null,
      policy_state: payload.policy_state ?? null,
      token_usage: payload.token_usage ?? {},
      cost_usage: payload.cost_usage ?? {},
      rejection_reason: payload.reason ?? null,
      selected: false,
      standby: false
    }
  ];
}

function mergeMissionEvent(snapshot, event) {
  if (!snapshot) {
    return snapshot;
  }
  const events = [...snapshot.events, event]
    .sort((left, right) => left.id - right.id)
    .slice(-200);
  let next = {
    ...snapshot,
    latest_event_id: Math.max(snapshot.latest_event_id ?? 0, event.id ?? 0),
    events,
    recent_trace: [
      ...(snapshot.recent_trace ?? []),
      {
        id: event.id,
        trace_type: event.event_type,
        title: event.payload?.title ?? event.event_type,
        message: event.message,
        status: event.payload?.status ?? "info",
        task_id: event.payload?.task_id ?? null,
        bid_id: event.payload?.bid_id ?? null,
        provider: event.payload?.provider ?? null,
        lane: event.payload?.lane ?? null,
        payload: event.payload ?? {},
        created_at: event.created_at
      }
    ]
      .sort((left, right) => left.id - right.id)
      .slice(-200)
  };
  switch (event.event_type) {
    case "mission.paused":
      next = { ...next, run_state: "paused" };
      break;
    case "mission.resumed":
    case "mission.started":
      next = { ...next, run_state: "running" };
      break;
    case "mission.cancelled":
      next = { ...next, run_state: "finalized", outcome: "failed_safe_stop" };
      break;
    case "mission.finalized":
      next = {
        ...next,
        run_state: "finalized",
        outcome: event.payload?.outcome ?? next.outcome
      };
      break;
    case "task.ready":
      next = {
        ...next,
        tasks: replaceTask(next.tasks, event.payload?.task_id, "ready")
      };
      break;
    case "task.running":
      next = {
        ...next,
        active_phase: "execute",
        tasks: replaceTask(next.tasks, event.payload?.task_id, "running")
      };
      break;
    case "task.completed":
      next = {
        ...next,
        tasks: replaceTask(next.tasks, event.payload?.task_id, "complete")
      };
      break;
    case "task.failed":
      next = {
        ...next,
        active_phase: "recover",
        tasks: replaceTask(next.tasks, event.payload?.task_id, "failed")
      };
      break;
    case "bid.submitted":
    case "bid.generated":
      next = { ...next, active_phase: "market", bids: ensureBid(next.bids, event) };
      break;
    case "task.selected":
      next = {
        ...next,
        active_task_id: event.payload?.task_id ?? next.active_task_id
      };
      break;
    case "diff.updated":
      next = {
        ...next,
        worktree_state: event.payload?.worktree_state ?? next.worktree_state
      };
      break;
    case "bid.won":
      next = {
        ...next,
        winner_bid_id: event.payload?.bid_id ?? next.winner_bid_id,
        bids: ensureBid(next.bids, event).map((bid) =>
          bid.bid_id === event.payload?.bid_id ? { ...bid, selected: true } : bid
        )
      };
      break;
    case "standby.selected":
      next = {
        ...next,
        standby_bid_id: event.payload?.bid_id ?? next.standby_bid_id,
        bids: ensureBid(next.bids, event).map((bid) =>
          bid.bid_id === event.payload?.bid_id ? { ...bid, standby: true } : bid
        )
      };
      break;
    case "standby.promoted":
      next = {
        ...next,
        winner_bid_id: event.payload?.bid_id ?? next.winner_bid_id,
        standby_bid_id: null,
        bids: next.bids.map((bid) => ({
          ...bid,
          selected: bid.bid_id === event.payload?.bid_id,
          standby: false
        }))
      };
      break;
    case "validation.passed":
      next = { ...next, active_phase: "validate" };
      break;
    case "validation.failed":
      next = { ...next, active_phase: "recover" };
      break;
    case "checkpoint.accepted":
      next = {
        ...next,
        head_commit: event.payload?.commit_sha ?? next.head_commit
      };
      break;
    default:
      break;
  }
  return next;
}

export function useMissionStream(missionId, repo) {
  const queryClient = useQueryClient();
  const reconnectRef = useRef(null);
  const lastSeenRef = useRef(0);
  const invalidateRef = useRef(null);

  const missionQuery = useQuery({
    queryKey: ["mission", repo, missionId],
    queryFn: () => getMission(missionId, repo),
    enabled: Boolean(missionId && repo),
    refetchInterval: (query) =>
      ["running", "paused", "cancelling"].includes(query.state.data?.run_state)
        ? 4000
        : false
  });

  useEffect(() => {
    if (missionQuery.data?.latest_event_id) {
      lastSeenRef.current = missionQuery.data.latest_event_id;
    }
  }, [missionQuery.data?.latest_event_id]);

  useEffect(() => {
    if (!missionId || !repo || !missionQuery.isSuccess) {
      return undefined;
    }
    let disposed = false;
    let source = null;

    const scheduleRefresh = () => {
      if (invalidateRef.current) {
        window.clearTimeout(invalidateRef.current);
      }
      invalidateRef.current = window.setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["mission", repo, missionId] });
        queryClient.invalidateQueries({ queryKey: ["mission-trace", repo, missionId] });
        queryClient.invalidateQueries({ queryKey: ["mission-diff", repo, missionId] });
        queryClient.invalidateQueries({ queryKey: ["mission-usage", repo, missionId] });
        queryClient.invalidateQueries({ queryKey: ["missions", repo] });
      }, 350);
    };

    const connect = () => {
      source = openMissionEvents(missionId, repo, lastSeenRef.current, {
        onEvent: (event) => {
          lastSeenRef.current = Math.max(lastSeenRef.current, event.id ?? 0);
          queryClient.setQueryData(["mission", repo, missionId], (current) =>
            mergeMissionEvent(current, event)
          );
          scheduleRefresh();
        },
        onError: () => {
          source?.close();
          if (disposed) {
            return;
          }
          reconnectRef.current = window.setTimeout(() => {
            queryClient.invalidateQueries({ queryKey: ["mission", repo, missionId] });
            queryClient.invalidateQueries({ queryKey: ["mission-trace", repo, missionId] });
            queryClient.invalidateQueries({ queryKey: ["mission-diff", repo, missionId] });
            queryClient.invalidateQueries({ queryKey: ["mission-usage", repo, missionId] });
            connect();
          }, 1200);
        }
      });
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      if (reconnectRef.current) {
        window.clearTimeout(reconnectRef.current);
      }
      if (invalidateRef.current) {
        window.clearTimeout(invalidateRef.current);
      }
    };
  }, [missionId, repo, missionQuery.isSuccess, queryClient]);

  return missionQuery;
}
