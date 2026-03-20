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
