import { useMemo, useState } from "react";

function parseList(value) {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function MissionComposer({
  busy,
  blocked,
  error,
  onSubmit,
  onOpenActiveMission
}) {
  const [repo, setRepo] = useState("");
  const [objective, setObjective] = useState("");
  const [constraints, setConstraints] = useState("");
  const [preferences, setPreferences] = useState("");
  const [protectedPaths, setProtectedPaths] = useState("");
  const [publicApiSurface, setPublicApiSurface] = useState("");
  const [benchmarkRequirement, setBenchmarkRequirement] = useState("");
  const [maxRuntime, setMaxRuntime] = useState(10);

  const recentRepos = useMemo(() => {
    try {
      return JSON.parse(window.localStorage.getItem("arbiter:recent-repos") ?? "[]");
    } catch {
      return [];
    }
  }, []);

  const disabled = busy || blocked || !repo.trim() || !objective.trim();

  const handleSubmit = async (event) => {
    event.preventDefault();
    try {
      await onSubmit({
        repo: repo.trim(),
        objective: objective.trim(),
        constraints: parseList(constraints),
        preferences: parseList(preferences),
        protected_paths: parseList(protectedPaths),
        public_api_surface: parseList(publicApiSurface),
        benchmark_requirement: benchmarkRequirement.trim() || null,
        max_runtime: Number(maxRuntime) || 10
      });
    } catch {
      // The launcher surfaces mutation failures through the shared error prop.
    }
  };

  return (
    <section className="composer panel-like">
      <div className="section-title">
        <h2>New Mission</h2>
        <p>Enter the repo and user prompt. Nothing starts until you submit this form.</p>
      </div>
      <form onSubmit={handleSubmit} className="composer-form">
        <label>
          Repo Path
          <input
            list="recent-repos"
            placeholder="C:\\path\\to\\target-repo"
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
          />
          <datalist id="recent-repos">
            {recentRepos.map((entry) => (
              <option key={entry} value={entry} />
            ))}
          </datalist>
        </label>
        <label>
          Objective
          <textarea
            rows={4}
            placeholder="Describe the user request Helix Runtime should execute."
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
          />
        </label>
        <details className="advanced-drawer">
          <summary>Advanced mission settings</summary>
          <div className="advanced-grid">
            <label>
              Constraints
              <textarea
                rows={3}
                placeholder="no breaking api, keep file churn low"
                value={constraints}
                onChange={(event) => setConstraints(event.target.value)}
              />
            </label>
            <label>
              Preferences
              <textarea
                rows={3}
                placeholder="prefer regression tests, safe rollback bias"
                value={preferences}
                onChange={(event) => setPreferences(event.target.value)}
              />
            </label>
            <label>
              Protected Paths
              <textarea
                rows={2}
                placeholder="src/public_api.py"
                value={protectedPaths}
                onChange={(event) => setProtectedPaths(event.target.value)}
              />
            </label>
            <label>
              Public API Surface
              <textarea
                rows={2}
                placeholder="src/sdk.py"
                value={publicApiSurface}
                onChange={(event) => setPublicApiSurface(event.target.value)}
              />
            </label>
            <label>
              Benchmark Requirement
              <input
                placeholder="npm run bench"
                value={benchmarkRequirement}
                onChange={(event) => setBenchmarkRequirement(event.target.value)}
              />
            </label>
            <label>
              Max Runtime (minutes)
              <input
                type="number"
                min={1}
                max={120}
                value={maxRuntime}
                onChange={(event) => setMaxRuntime(event.target.value)}
              />
            </label>
          </div>
        </details>
        {blocked ? (
          <div className="mission-lockout">
            <div>
              <strong>Live mission already active</strong>
              <p className="form-note warning-note">
                Open the live room to inspect, pause, or cancel the current run before starting a new mission.
              </p>
            </div>
            {onOpenActiveMission ? (
              <button type="button" className="ghost-button" onClick={onOpenActiveMission}>
                Open live room
              </button>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <div className="form-note error-note launch-error" role="alert" aria-live="polite">
            <strong>Mission launch failed</strong>
            <p>{error}</p>
          </div>
        ) : null}
        <button type="submit" className="primary-button" disabled={disabled}>
          {busy ? "Starting mission..." : "Launch mission"}
        </button>
      </form>
    </section>
  );
}
