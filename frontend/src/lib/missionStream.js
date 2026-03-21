import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getMission, openMissionEvents } from "./api";

const STREAM_LIMIT = 200;

const EVENT_PHASE_MAP = {
  "mission.started": "collect",
  "repo.scan.completed": "strategize",
  "strategy.landscape_generated": "strategize",
  "strategy.market_opened": "strategize",
  "strategy.objective_met": "strategize",
  "task.created": "strategize",
  "task.ready": "strategize",
  "task.selected": "strategize",
  "bid.generated": "strategize",
  "bid.submitted": "strategize",
  "bid.rejected": "strategize",
  "bid.won": "select",
  "standby.selected": "select",
  "simulation.rollout": "simulate",
  "simulation.completed": "select",
  "proposal.selected": "execute",
  "task.running": "execute",
  "tool.executed": "execute",
  "validation.started": "validate",
  "validation.completed": "validate",
  "validation.passed": "validate",
  "validation.failed": "recover",
  "recovery.started": "recover",
  "recovery.round_opened": "recover",
  "recovery.completed": "recover",
  "checkpoint.accepted": "strategize",
  "checkpoint.reverted": "recover",
  "mission.finalized": "finalize",
  "mission.cancelled": "finalize",
  "bidding.architecture_violation": "finalize",
  "bidding.degraded_mode_entered": "strategize"
};

function appendBounded(items, nextItem, limit = STREAM_LIMIT, key = "id") {
  const existingIndex = items.findIndex((item) => item?.[key] === nextItem?.[key]);
  const merged = existingIndex === -1 ? [...items, nextItem] : items.map((item, index) => (index === existingIndex ? { ...item, ...nextItem } : item));
  return merged.slice(-limit);
}

function replaceTaskStatus(tasks, taskId, nextStatus, extra = {}) {
  const nextTasks = tasks.map((task) =>
    task.task_id === taskId ? { ...task, status: nextStatus, ...extra } : task
  );
  if (nextTasks.some((task) => task.task_id === taskId)) {
    return nextTasks;
  }
  if (!taskId) {
    return tasks;
  }
  return [
    ...tasks,
    {
      task_id: taskId,
      title: taskId,
      task_type: extra.task_type ?? "validate",
      requirement_level: extra.requirement_level ?? "required",
      dependencies: extra.dependencies ?? [],
      status: nextStatus,
      ...extra
    }
  ];
}

function upsertTask(tasks, payload, nextStatus) {
  const taskId = payload.task_id;
  if (!taskId) {
    return tasks;
  }
  return replaceTaskStatus(tasks, taskId, nextStatus, {
    title: payload.title,
    task_type: payload.task_type,
    requirement_level: payload.requirement_level,
    dependencies: payload.dependencies ?? []
  });
}

export function normalizeIncomingBid(event) {
  const payload = event.payload ?? {};
  return {
    bid_id: payload.bid_id,
    task_id: payload.task_id,
    role: payload.role ?? "Unknown",
    provider: payload.provider ?? null,
    lane: payload.lane ?? null,
    model_id: payload.model_id ?? null,
    invocation_id: payload.invocation_id ?? null,
    invocation_kind: payload.invocation_kind ?? null,
    generation_mode: payload.generation_mode ?? (payload.provider === "system" ? "deterministic_fallback" : null),
    strategy_family: payload.strategy_family ?? "pending",
    strategy_summary: payload.strategy_summary ?? "Live contender",
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
    token_usage: payload.token_usage ?? null,
    cost_usage: payload.cost_usage ?? null,
    usage_unavailable_reason: payload.usage_unavailable_reason ?? null,
    rejection_reason: payload.rejection_reason ?? payload.reason ?? null,
    selected: false,
    standby: false
  };
}

function ensureBid(existingBids, event) {
  const nextBid = normalizeIncomingBid(event);
  if (!nextBid.bid_id) {
    return existingBids;
  }
  return appendBounded(existingBids, nextBid, STREAM_LIMIT, "bid_id");
}

function mergeBiddingState(snapshot, event) {
  const payload = event.payload ?? {};
  return {
    ...(snapshot ?? {}),
    ...(payload.bidding_state ?? {}),
    generation_mode: payload.generation_mode ?? payload.bidding_state?.generation_mode ?? snapshot?.generation_mode ?? null,
    warning: payload.reason ?? payload.warning ?? payload.bidding_state?.warning ?? snapshot?.warning ?? null,
    architecture_violation:
      payload.reason ?? payload.architecture_violation ?? payload.bidding_state?.architecture_violation ?? snapshot?.architecture_violation ?? null,
    degraded: Boolean(
      payload.degraded ??
        payload.bidding_state?.degraded ??
        (payload.generation_mode === "deterministic_fallback" ||
          snapshot?.generation_mode === "deterministic_fallback")
    ),
    round_provider_invocations:
      payload.round_provider_invocations ?? snapshot?.round_provider_invocations ?? 0,
    active_provider_bids:
      payload.active_provider_bids ?? snapshot?.active_provider_bids ?? 0,
    active_fallback_bids:
      payload.active_fallback_bids ?? snapshot?.active_fallback_bids ?? 0
  };
}

function normalizeCheckpoint(event, snapshot) {
  const payload = event.payload ?? {};
  const rollbackPointer = payload.rollback_pointer ?? snapshot?.accepted_checkpoints?.at(-1)?.checkpoint_id ?? null;
  return {
    checkpoint_id: payload.checkpoint_id ?? payload.commit_sha ?? `${event.event_type}-${event.id ?? Date.now()}`,
    label: payload.label ?? payload.task_id ?? "accepted",
    commit_sha: payload.commit_sha ?? snapshot?.head_commit ?? null,
    created_at: payload.created_at ?? event.created_at ?? null,
    summary: payload.summary ?? payload.message ?? null,
    diff_summary: payload.diff_summary ?? snapshot?.latest_diff_summary ?? "",
    affected_files: payload.affected_files ?? [],
    validator_results: payload.validator_results ?? [],
    strategy_family: payload.strategy_family ?? null,
    civic_audit_ids: payload.civic_audit_ids ?? [],
    rollback_pointer: rollbackPointer
  };
}

function updateWorktreeFromCheckpoint(snapshot, checkpoint) {
  return {
    ...(snapshot ?? {}),
    has_changes: false,
    changed_files: [],
    diff_stat: snapshot?.diff_stat ?? "",
    diff_patch: snapshot?.diff_patch ?? "",
    accepted_commit: checkpoint.commit_sha,
      accepted_checkpoint_id: checkpoint.checkpoint_id,
      reason: checkpoint.summary
        ? `Accepted checkpoint ${checkpoint.label} is anchored at ${checkpoint.commit_sha?.slice(0, 8) ?? "n/a"}.`
      : "Accepted checkpoint is anchored on the Helix-managed branch."
  };
}

function updateExecutionSteps(steps, event) {
  const payload = event.payload ?? {};
  if (!payload.task_id && !payload.action_type && !payload.tool_name) {
    return steps;
  }
  return appendBounded(
    steps,
    {
      step_id: payload.step_id ?? `${event.event_type}-${event.id ?? Date.now()}`,
      task_id: payload.task_id ?? null,
      bid_id: payload.bid_id ?? null,
      action_type: payload.action_type ?? event.event_type,
      description: payload.message ?? payload.title ?? event.message,
      tool_name: payload.tool_name ?? payload.action_type ?? event.event_type,
      input_payload: payload.input_payload ?? payload,
      output_payload: payload.output_payload ?? payload,
      created_at: payload.created_at ?? event.created_at
    },
    STREAM_LIMIT,
    "step_id"
  );
}

function updateRecentTrace(trace, event) {
  const payload = event.payload ?? {};
  return appendBounded(
    trace,
    {
      id: event.id,
      trace_type: event.event_type,
      title: payload.title ?? event.event_type,
      message: event.message,
      status: payload.status ?? "info",
      task_id: payload.task_id ?? null,
      bid_id: payload.bid_id ?? null,
      provider: payload.provider ?? null,
      lane: payload.lane ?? null,
      model_id: payload.model_id ?? null,
      invocation_id: payload.invocation_id ?? null,
      generation_mode: payload.generation_mode ?? null,
      payload,
      created_at: event.created_at
    },
    STREAM_LIMIT,
    "id"
  );
}

export function deriveMissionPhase(eventType, currentPhase) {
  return EVENT_PHASE_MAP[eventType] ?? currentPhase ?? "idle";
}

export function mergeMissionEvent(snapshot, event) {
  if (!snapshot) {
    return snapshot;
  }
  const payload = event.payload ?? {};
  const nextEvents = appendBounded(snapshot.events ?? [], event, STREAM_LIMIT);
  const nextTrace = updateRecentTrace(snapshot.recent_trace ?? [], event);
  const next = {
    ...snapshot,
    latest_event_id: Math.max(snapshot.latest_event_id ?? 0, event.id ?? 0),
    events: nextEvents,
    recent_trace: nextTrace,
    active_phase: deriveMissionPhase(event.event_type, snapshot.active_phase),
    latest_diff_summary: payload.diff_summary ?? snapshot.latest_diff_summary ?? "",
    simulation_round:
      payload.round ?? payload.simulation_round ?? snapshot.simulation_round ?? 0,
    recovery_round:
      payload.round ?? payload.recovery_round ?? snapshot.recovery_round ?? 0,
    stop_reason: payload.reason ?? snapshot.stop_reason ?? null,
    outcome: payload.outcome ?? snapshot.outcome ?? null
  };

  switch (event.event_type) {
    case "mission.paused":
      return { ...next, run_state: "paused" };
    case "mission.resumed":
    case "mission.started":
      return { ...next, run_state: "running" };
    case "mission.cancelled":
      return { ...next, run_state: "finalized", outcome: "failed_safe_stop" };
    case "mission.finalized":
      return {
        ...next,
        run_state: "finalized",
        active_phase: "finalize",
        outcome: payload.outcome ?? next.outcome
      };
    case "repo.scan.completed":
      return {
        ...next,
        repo_snapshot: {
          ...(snapshot.repo_snapshot ?? {}),
          capabilities: {
            ...(snapshot.repo_snapshot?.capabilities ?? {}),
            runtime: payload.runtime ?? snapshot.repo_snapshot?.capabilities?.runtime ?? "unknown",
            risky_paths: payload.risky_paths ?? snapshot.repo_snapshot?.capabilities?.risky_paths ?? []
          }
        }
      };
    case "phase.changed":
      return {
        ...next,
        active_phase: payload.phase ?? payload.next_phase ?? payload.stage ?? next.active_phase
      };
    case "task.created":
      return { ...next, tasks: upsertTask(snapshot.tasks ?? [], payload, payload.status ?? "pending") };
    case "task.ready":
      return { ...next, tasks: replaceTaskStatus(snapshot.tasks ?? [], payload.task_id, "ready") };
    case "task.running":
      return { ...next, tasks: replaceTaskStatus(snapshot.tasks ?? [], payload.task_id, "running") };
    case "task.completed":
      return { ...next, tasks: replaceTaskStatus(snapshot.tasks ?? [], payload.task_id, "complete") };
    case "task.failed":
      return { ...next, tasks: replaceTaskStatus(snapshot.tasks ?? [], payload.task_id, "failed") };
    case "task.selected":
      return {
        ...next,
        active_task_id: payload.task_id ?? snapshot.active_task_id ?? null,
        tasks: replaceTaskStatus(snapshot.tasks ?? [], payload.task_id ?? snapshot.active_task_id, "ready")
      };
    case "market.opened":
      return {
        ...next,
        active_bid_round: payload.round ?? snapshot.active_bid_round ?? 0,
        active_task_id: payload.task_id ?? snapshot.active_task_id ?? null
      };
    case "bid.generated":
    case "bid.submitted":
      return {
        ...next,
        bids: ensureBid(snapshot.bids ?? [], event)
      };
    case "bid.retired":
      return {
        ...next,
        bids: ensureBid(snapshot.bids ?? [], event).map((bid) =>
          bid.bid_id === payload.bid_id
            ? {
                ...bid,
                status: "retired",
                selected: false,
                standby: false,
                retirement_reason: payload.reason ?? bid.retirement_reason ?? null
              }
            : bid
        )
      };
    case "bid.rejected":
      return {
        ...next,
        bids: ensureBid(snapshot.bids ?? [], event).map((bid) =>
          bid.bid_id === payload.bid_id
            ? {
                ...bid,
                rejection_reason: payload.reason ?? bid.rejection_reason,
                selected: false,
                standby: false,
                status: "rejected"
              }
            : bid
        )
      };
    case "bid.won":
      return {
        ...next,
        winner_bid_id: payload.bid_id ?? snapshot.winner_bid_id ?? null,
        bids: ensureBid(snapshot.bids ?? [], event).map((bid) =>
          bid.bid_id === payload.bid_id ? { ...bid, selected: true, status: "winner" } : { ...bid, selected: false }
        )
      };
    case "standby.selected":
      return {
        ...next,
        standby_bid_id: payload.bid_id ?? snapshot.standby_bid_id ?? null,
        bids: ensureBid(snapshot.bids ?? [], event).map((bid) =>
          bid.bid_id === payload.bid_id ? { ...bid, standby: true, status: "standby" } : bid
        )
      };
    case "standby.promoted":
      return {
        ...next,
        winner_bid_id: payload.bid_id ?? snapshot.winner_bid_id ?? null,
        standby_bid_id: null,
        bids: (snapshot.bids ?? []).map((bid) => ({
          ...bid,
          selected: bid.bid_id === payload.bid_id,
          standby: false,
          status: bid.bid_id === payload.bid_id ? "winner" : bid.status
        }))
      };
    case "simulation.rollout":
      return {
        ...next,
        simulation_summary: {
          ...(snapshot.simulation_summary ?? {}),
          task_id: payload.task_id ?? snapshot.simulation_summary?.task_id ?? snapshot.active_task_id ?? null,
          summary: payload.summary ?? snapshot.simulation_summary?.summary ?? "",
          budget_used: payload.budget_used ?? snapshot.simulation_summary?.budget_used ?? 0,
          rollout_count: payload.rollout_count ?? snapshot.simulation_summary?.rollout_count ?? 0
        }
      };
    case "simulation.completed":
      return {
        ...next,
        simulation_summary: {
          ...(snapshot.simulation_summary ?? {}),
          summary: payload.summary ?? snapshot.simulation_summary?.summary ?? "",
          task_id: payload.task_id ?? snapshot.simulation_summary?.task_id ?? snapshot.active_task_id ?? null
        }
      };
    case "bidding.degraded_mode_entered":
      return {
        ...next,
        bidding_state: mergeBiddingState(snapshot.bidding_state ?? {}, {
          payload: {
            ...payload,
            generation_mode: "deterministic_fallback",
            degraded: true,
            warning: payload.reason ?? payload.warning ?? "Degraded bidding mode"
          }
        })
      };
    case "bidding.architecture_violation":
      return {
        ...next,
        bidding_state: mergeBiddingState(snapshot.bidding_state ?? {}, {
          payload: {
            ...payload,
            architecture_violation: payload.reason ?? payload.message ?? "Architecture violation"
          }
        })
      };
    case "proposal.selected":
      return {
        ...next,
        active_phase: "execute",
        latest_diff_summary: payload.summary ? `${payload.summary}` : next.latest_diff_summary,
        execution_steps: updateExecutionSteps(snapshot.execution_steps ?? [], event)
      };
    case "tool.executed":
      return {
        ...next,
        worktree_state: payload.worktree_state ?? snapshot.worktree_state ?? {},
        execution_steps: updateExecutionSteps(snapshot.execution_steps ?? [], event)
      };
    case "validation.started":
    case "validation.completed":
    case "validation.passed":
      return {
        ...next,
        validation_report: {
          ...(snapshot.validation_report ?? {}),
          task_id: payload.task_id ?? snapshot.validation_report?.task_id ?? snapshot.active_task_id ?? null,
          passed: payload.passed ?? snapshot.validation_report?.passed ?? event.event_type === "validation.passed",
          notes: payload.notes ?? snapshot.validation_report?.notes ?? []
        }
      };
    case "validation.failed":
      return {
        ...next,
        validation_report: {
          ...(snapshot.validation_report ?? {}),
          task_id: payload.task_id ?? snapshot.validation_report?.task_id ?? snapshot.active_task_id ?? null,
          passed: false,
          notes: payload.details ? [payload.details] : snapshot.validation_report?.notes ?? []
        }
      };
    case "recovery.started":
      return {
        ...next,
        failure_context: {
          ...(snapshot.failure_context ?? {}),
          task_id: payload.task_id ?? snapshot.failure_context?.task_id ?? snapshot.active_task_id ?? null,
          failure_type: payload.failure_type ?? snapshot.failure_context?.failure_type ?? "validation_failure",
          details: payload.details ?? snapshot.failure_context?.details ?? event.message,
          diff_summary: payload.diff_summary ?? snapshot.failure_context?.diff_summary ?? snapshot.latest_diff_summary ?? "",
          strategy_family: payload.strategy_family ?? snapshot.failure_context?.strategy_family ?? null,
          validator_deltas: payload.validator_deltas ?? snapshot.failure_context?.validator_deltas ?? [],
          recommended_recovery_scope: payload.recommended_recovery_scope ?? snapshot.failure_context?.recommended_recovery_scope ?? "rebid"
        },
        execution_steps: updateExecutionSteps(snapshot.execution_steps ?? [], event)
      };
    case "recovery.round_opened":
      return {
        ...next,
        active_phase: "recover",
        recovery_round: payload.round ?? snapshot.recovery_round ?? 0
      };
    case "recovery.completed":
      return {
        ...next,
        recovery_round: payload.round ?? snapshot.recovery_round ?? 0,
        active_phase: payload.next_phase ?? next.active_phase
      };
    case "checkpoint.accepted": {
      const checkpoint = normalizeCheckpoint(event, snapshot);
      return {
        ...next,
        head_commit: checkpoint.commit_sha ?? snapshot.head_commit ?? null,
        accepted_checkpoints: appendBounded(snapshot.accepted_checkpoints ?? [], checkpoint, STREAM_LIMIT, "checkpoint_id"),
        worktree_state: updateWorktreeFromCheckpoint(snapshot.worktree_state ?? {}, checkpoint),
        latest_diff_summary: checkpoint.diff_summary || next.latest_diff_summary,
        execution_steps: updateExecutionSteps(snapshot.execution_steps ?? [], event)
      };
    }
    case "checkpoint.reverted":
      return {
        ...next,
        worktree_state: {
          ...(snapshot.worktree_state ?? {}),
          accepted_commit: payload.commit_sha ?? snapshot.worktree_state?.accepted_commit ?? snapshot.head_commit ?? null,
          has_changes: false,
          changed_files: [],
          reason: payload.message ?? "Worktree reverted to the latest accepted checkpoint."
        },
        execution_steps: updateExecutionSteps(snapshot.execution_steps ?? [], event)
      };
    case "diff.updated":
      return {
        ...next,
        latest_diff_summary: payload.diff_summary ?? payload.reason ?? next.latest_diff_summary,
        worktree_state: payload.worktree_state ?? snapshot.worktree_state ?? {}
      };
    default:
      return next;
  }
}

export function reconcileMissionSnapshot(current, incoming) {
  if (!current) {
    return incoming;
  }
  if (!incoming) {
    return current;
  }

  const currentEventId = Number(current.latest_event_id ?? 0);
  const incomingEventId = Number(incoming.latest_event_id ?? 0);
  if (incomingEventId >= currentEventId) {
    return incoming;
  }

  return {
    ...incoming,
    ...current,
    mission_state_checkpoints: incoming.mission_state_checkpoints ?? current.mission_state_checkpoints ?? [],
    repo_state_checkpoints: incoming.repo_state_checkpoints ?? current.repo_state_checkpoints ?? [],
    mission_output: incoming.mission_output ?? current.mission_output ?? {},
    usage_summary: incoming.usage_summary ?? current.usage_summary ?? {},
    validation_report: incoming.validation_report ?? current.validation_report ?? null,
    failure_context: incoming.failure_context ?? current.failure_context ?? null,
    worktree_state: incoming.worktree_state ?? current.worktree_state ?? {}
  };
}

export function useMissionStream(missionId, repo) {
  const queryClient = useQueryClient();
  const reconnectRef = useRef(null);
  const lastSeenRef = useRef(0);
  const invalidateRef = useRef(null);

  const missionQuery = useQuery({
    queryKey: ["mission", repo, missionId],
    queryFn: async () =>
      reconcileMissionSnapshot(
        queryClient.getQueryData(["mission", repo, missionId]),
        await getMission(missionId, repo)
      ),
    enabled: Boolean(missionId && repo),
    refetchInterval: (query) =>
      ["running", "paused", "cancelling"].includes(query.state.data?.run_state)
        ? 2500
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
      }, 250);
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
