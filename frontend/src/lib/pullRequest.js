const HTTP_URL_PATTERN = /^https?:\/\//i;

function isHttpUrl(value) {
  return typeof value === "string" && HTTP_URL_PATTERN.test(value.trim());
}

function parseCandidateString(value) {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  if (isHttpUrl(trimmed)) {
    return {
      html_url: trimmed,
      url: trimmed
    };
  }
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

export function normalizePullRequest(candidate) {
  if (!candidate) {
    return null;
  }
  if (Array.isArray(candidate)) {
    for (const item of candidate) {
      const match = normalizePullRequest(item);
      if (match) {
        return match;
      }
    }
    return null;
  }
  if (typeof candidate === "string") {
    const parsed = parseCandidateString(candidate);
    return parsed ? normalizePullRequest(parsed) : null;
  }
  if (typeof candidate !== "object") {
    return null;
  }

  const directUrl = [candidate.html_url, candidate.url, candidate.detail].find(isHttpUrl);
  if (directUrl) {
    return {
      ...candidate,
      html_url: isHttpUrl(candidate.html_url) ? candidate.html_url : directUrl,
      url: isHttpUrl(candidate.url) ? candidate.url : directUrl
    };
  }

  const nestedCandidates = [
    candidate.pull_request,
    candidate.skill_output,
    candidate.output_payload,
    candidate.result,
    candidate.results,
    candidate.text
  ];
  for (const nested of nestedCandidates) {
    const match = normalizePullRequest(nested);
    if (match) {
      return match;
    }
  }
  return null;
}

export function missionPullRequest(mission) {
  const actionCandidates = (mission?.recent_civic_actions ?? []).flatMap((action) => [
    action?.pull_request,
    action?.output_payload,
    action?.payload?.pull_request,
    action?.payload?.skill_output,
    action?.payload?.output_payload
  ]);
  const candidates = [
    mission?.mission_output?.pull_request,
    mission?.pull_request,
    mission?.skill_outputs?.github_publish?.pull_request,
    mission?.skill_outputs?.github_publish,
    ...actionCandidates
  ];
  for (const candidate of candidates) {
    const match = normalizePullRequest(candidate);
    if (match) {
      return match;
    }
  }
  return null;
}

export function missionPullRequestUrl(mission) {
  const pullRequest = missionPullRequest(mission);
  return pullRequest?.html_url ?? pullRequest?.url ?? null;
}
