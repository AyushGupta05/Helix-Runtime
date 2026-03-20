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
  "task.completed",
  "task.failed",
  "bid.submitted",
  "bid.rejected",
  "bid.won",
  "standby.selected",
  "standby.promoted",
  "tool.executed",
  "validation.passed",
  "validation.failed",
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

export function getMissions() {
  return apiRequest("/api/missions");
}

export function getMission(missionId) {
  return apiRequest(`/api/missions/${missionId}`);
}

export function createMission(payload) {
  return apiRequest("/api/missions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function pauseMission(missionId) {
  return apiRequest(`/api/missions/${missionId}/pause`, { method: "POST" });
}

export function resumeMission(missionId) {
  return apiRequest(`/api/missions/${missionId}/resume`, { method: "POST" });
}

export function cancelMission(missionId) {
  return apiRequest(`/api/missions/${missionId}/cancel`, { method: "POST" });
}

export function openMissionEvents(missionId, afterId, { onEvent, onError }) {
  const source = new EventSource(`/api/missions/${missionId}/events?after_id=${afterId}`);
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

export { EVENT_TYPES };
