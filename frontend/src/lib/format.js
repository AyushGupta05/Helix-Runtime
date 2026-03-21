export function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return Number(value).toFixed(digits);
}

export function formatInteger(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "0";
  }
  return Number(value).toLocaleString();
}

export function formatCurrency(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "$0.00";
  }
  return `$${Number(value).toFixed(4)}`;
}

export function formatUsageCost(summary) {
  const costStatus = String(summary?.cost_status ?? "available");
  const totalTokens = Number(summary?.total_tokens ?? 0);
  if (costStatus === "unavailable" && totalTokens > 0) {
    return "Cost unavailable";
  }
  if (costStatus === "partial" && totalTokens > 0) {
    return `${formatCurrency(summary?.total_cost ?? 0)} + gaps`;
  }
  return formatCurrency(summary?.total_cost ?? 0);
}

export function usageCostStatusDetail(summary) {
  const costStatus = String(summary?.cost_status ?? "available");
  const missingCount = Number(summary?.cost_unavailable_invocation_count ?? 0);
  if (costStatus === "unavailable" && missingCount > 0) {
    return `${missingCount} provider call${missingCount === 1 ? "" : "s"} missing cost metadata`;
  }
  if (costStatus === "partial" && missingCount > 0) {
    return `${missingCount} provider call${missingCount === 1 ? "" : "s"} missing cost metadata`;
  }
  return "";
}

export function formatRuntime(seconds) {
  if (!seconds) {
    return "0s";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes}m ${remaining}s`;
}

function parseTimestamp(value) {
  if (!value) {
    return null;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}

export function getMissionElapsedSeconds(
  mission,
  { now = Date.now(), snapshotReceivedAt = Date.now() } = {}
) {
  if (!mission) {
    return 0;
  }
  const startedAt = parseTimestamp(
    mission.events?.[0]?.created_at ?? mission.created_at ?? mission.updated_at
  );
  const baseRuntime = Number(mission.runtime_seconds ?? 0);
  if (baseRuntime > 0) {
    if (["running", "cancelling"].includes(mission.run_state)) {
      const liveDelta = Math.max(0, (now - snapshotReceivedAt) / 1000);
      const streamRuntime = baseRuntime + liveDelta;
      if (startedAt === null) {
        return streamRuntime;
      }
      const wallClockRuntime = Math.max(0, (now - startedAt) / 1000);
      return Math.max(streamRuntime, wallClockRuntime);
    }
    return baseRuntime;
  }
  if (startedAt === null) {
    return 0;
  }
  const endedAt =
    mission.run_state === "finalized"
      ? parseTimestamp(mission.updated_at ?? mission.events?.at(-1)?.created_at) ?? now
      : now;
  return Math.max(0, (endedAt - startedAt) / 1000);
}

export function relativeTime(isoString) {
  if (!isoString) {
    return "just now";
  }
  const delta = Date.now() - new Date(isoString).getTime();
  const seconds = Math.max(1, Math.floor(delta / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.floor(hours / 24)}d ago`;
}

export function humanizeToken(value) {
  return value.replace(/[._]/g, " ");
}

export const MISSION_STAGE_ORDER = [
  "collect",
  "strategize",
  "simulate",
  "select",
  "execute",
  "validate",
  "recover",
  "finalize"
];

export function humanizeMissionStage(stage) {
  if (!stage) {
    return "Idle";
  }
  return String(stage)
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function humanizeEventType(eventType) {
  if (!eventType) {
    return "Event";
  }
  return String(eventType)
    .replace(/\./g, " ")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function shortCommit(commitSha, length = 8) {
  if (!commitSha) {
    return "n/a";
  }
  return String(commitSha).slice(0, length);
}

export function summarizeProvider(provider) {
  if (!provider) {
    return "Unknown";
  }
  return provider.replace(/(^\w)|-(\w)/g, (match) => match.replace("-", "").toUpperCase());
}

export function humanizeGenerationMode(mode) {
  if (!mode) {
    return "Unknown";
  }
  return String(mode)
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function isDeterministicFallbackBid(bid) {
  return bid?.generation_mode === "deterministic_fallback";
}

export function summarizeBidOrigin(bid) {
  if (!bid) {
    return "Unknown origin";
  }
  const role = bid.role ? String(bid.role) : "Unknown role";
  const providerLabel = isDeterministicFallbackBid(bid)
    ? "System"
    : bid.provider
      ? summarizeProvider(bid.provider)
      : "Unknown";
  const modelLabel = bid.model_id ? String(bid.model_id) : "model unavailable";
  const modeLabel = humanizeGenerationMode(bid.generation_mode);
  return `${role} | ${providerLabel} | ${modelLabel} | ${modeLabel}`;
}

export function summarizeInvocationMode(invocation) {
  if (!invocation) {
    return "Unknown";
  }
  if (invocation.generation_mode) {
    return humanizeGenerationMode(invocation.generation_mode);
  }
  return invocation.status ? humanizeGenerationMode(invocation.status) : "Unknown";
}

export function humanizePhase(phase) {
  const labels = {
    strategize: "Strategizing",
    simulate: "Simulating",
    select: "Selecting",
    execute: "Executing",
    validate: "Validating",
    recover: "Recovering",
    collect: "Scanning",
    finalize: "Finalizing",
    idle: "Idle"
  };
  return labels[phase] || humanizeToken(phase || "idle");
}
