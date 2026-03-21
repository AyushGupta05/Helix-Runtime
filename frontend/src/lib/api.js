const EVENT_TYPES = [
  "mission.started",
  "mission.paused",
  "mission.resumed",
  "mission.cancelled",
  "mission.finalized",
  "repo.scan.completed",
  "task.created",
  "task.ready",
  "task.running",
  "task.selected",
  "task.completed",
  "task.failed",
  "phase.changed",
  "market.opened",
  "bid.generated",
  "bid.retired",
  "bid.submitted",
  "bid.rejected",
  "bid.won",
  "standby.selected",
  "standby.promoted",
  "simulation.rollout",
  "simulation.completed",
  "model.invocation.started",
  "model.invocation.completed",
  "model.invocation.failed",
  "bidding.degraded_mode_entered",
  "bidding.architecture_violation",
  "proposal.selected",
  "diff.updated",
  "tool.executed",
  "validation.started",
  "validation.completed",
  "validation.passed",
  "validation.failed",
  "recovery.started",
  "recovery.completed",
  "recovery.round_opened",
  "checkpoint.accepted",
  "checkpoint.reverted"
];

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {})
    }
  });
  if (!response.ok) {
    let detail = "Request failed";
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function withRepo(path, repo) {
  const target = new URL(path, window.location.origin);
  if (repo) {
    target.searchParams.set("repo", repo);
  }
  return `${target.pathname}${target.search}`;
}

export function getMissions(repo) {
  return apiRequest(withRepo("/api/missions", repo));
}

export function getMission(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}`, repo));
}

export function createMission(payload) {
  return apiRequest("/api/missions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function pauseMission(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}/pause`, repo), { method: "POST" });
}

export function resumeMission(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}/resume`, repo), { method: "POST" });
}

export function cancelMission(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}/cancel`, repo), { method: "POST" });
}

export function openMissionEvents(missionId, repo, afterId, { onEvent, onError }) {
  const target = new URL(`/api/missions/${missionId}/events`, window.location.origin);
  target.searchParams.set("after_id", String(afterId));
  if (repo) {
    target.searchParams.set("repo", repo);
  }
  const source = new EventSource(`${target.pathname}${target.search}`);
  EVENT_TYPES.forEach((eventType) => {
    source.addEventListener(eventType, (event) => {
      const payload = JSON.parse(event.data);
      onEvent({
        id: Number(event.lastEventId || payload.id || 0),
        event_type: eventType,
        created_at: payload.created_at,
        message: payload.message,
        payload: payload.payload ?? {}
      });
    });
  });
  source.onerror = onError;
  return source;
}

export function getMissionTrace(missionId, repo, afterId = 0, limit = 200) {
  const target = new URL(`/api/missions/${missionId}/trace`, window.location.origin);
  target.searchParams.set("after_id", String(afterId));
  target.searchParams.set("limit", String(limit));
  if (repo) {
    target.searchParams.set("repo", repo);
  }
  return apiRequest(`${target.pathname}${target.search}`);
}

export function getMissionDiff(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}/diff`, repo));
}

export function getMissionUsage(missionId, repo) {
  return apiRequest(withRepo(`/api/missions/${missionId}/usage`, repo));
}

export { EVENT_TYPES };
