import { formatCurrency, formatInteger, formatNumber, summarizeProvider } from "../lib/format";

function KeyValue({ label, value }) {
  return (
    <div className="kv">
      <span>{label}</span>
      <strong>{value || "n/a"}</strong>
    </div>
  );
}

export default function ArtifactsPanel({ mission, diffState, usageSummary, selectedBid }) {
  const worktree = diffState?.worktree_state ?? mission.worktree_state ?? {};
  const checkpoints = mission.accepted_checkpoints ?? [];
  const latestTrace = mission.recent_trace ?? [];
  const latestProposal = [...latestTrace].reverse().find((entry) => entry.trace_type === "proposal.selected");
  const usageRows = usageSummary?.invocations ?? [];

  return (
    <div className="inspector-grid">
      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Repo Changes</h3>
          <span>{worktree.has_changes ? "Isolated worktree dirty" : "No changes"}</span>
        </div>
        <p>{worktree.reason || "No repo changes yet."}</p>
        <KeyValue label="Worktree" value={worktree.worktree_path} />
        <KeyValue label="Changed files" value={String(worktree.changed_files?.length ?? 0)} />
        {worktree.changed_files?.length ? (
          <ul className="artifact-bullets">
            {worktree.changed_files.map((file) => <li key={file}>{file}</li>)}
          </ul>
        ) : null}
        <details className="artifact-detail">
          <summary>Diff</summary>
          <pre>{worktree.diff_patch || worktree.diff_stat || "No diff yet."}</pre>
        </details>
      </section>

      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Accepted Checkpoints</h3>
          <span>{checkpoints.length}</span>
        </div>
        <div className="checkpoint-list">
          {checkpoints.length ? checkpoints.slice().reverse().map((checkpoint) => (
            <article key={checkpoint.checkpoint_id} className="checkpoint-item">
              <strong>{checkpoint.label}</strong>
              <p>{checkpoint.summary || "Accepted checkpoint"}</p>
              <div className="trace-chip-row">
                <span className="timeline-chip">{checkpoint.commit_sha?.slice(0, 12)}</span>
                {checkpoint.strategy_family ? <span className="timeline-chip">{checkpoint.strategy_family}</span> : null}
              </div>
            </article>
          )) : <p>No checkpoints yet.</p>}
        </div>
      </section>

      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Selected Proposal</h3>
          <span>{latestProposal?.provider ? summarizeProvider(latestProposal.provider) : "Pending"}</span>
        </div>
        {latestProposal ? (
          <>
            <KeyValue label="Provider" value={summarizeProvider(latestProposal.provider)} />
            <KeyValue label="Lane" value={latestProposal.lane} />
            <KeyValue label="Model" value={latestProposal.payload?.model_id} />
            <p>{latestProposal.payload?.summary || latestProposal.message}</p>
          </>
        ) : selectedBid ? (
          <>
            <KeyValue label="Winning bid" value={selectedBid.strategy_family} />
            <KeyValue label="Provider" value={summarizeProvider(selectedBid.provider)} />
            <KeyValue label="Score" value={formatNumber(selectedBid.score)} />
            <p>{selectedBid.strategy_summary}</p>
          </>
        ) : (
          <p>No provider proposal selected yet.</p>
        )}
      </section>

      <section className="artifact-card">
        <div className="artifact-card-head">
          <h3>Usage Ledger</h3>
          <span>{formatInteger(usageSummary?.mission?.total_tokens ?? 0)} tok</span>
        </div>
        <div className="usage-summary-grid">
          <KeyValue label="Mission tokens" value={formatInteger(usageSummary?.mission?.total_tokens ?? 0)} />
          <KeyValue label="Mission cost" value={formatCurrency(usageSummary?.mission?.total_cost ?? 0)} />
          <KeyValue label="Active task tokens" value={formatInteger(usageSummary?.active_task?.total_tokens ?? 0)} />
          <KeyValue label="Active task cost" value={formatCurrency(usageSummary?.active_task?.total_cost ?? 0)} />
        </div>
        <div className="usage-ledger">
          {usageRows.slice().reverse().slice(0, 10).map((row) => (
            <article key={row.invocation_id} className="usage-item">
              <div className="usage-item-head">
                <strong>{summarizeProvider(row.provider)}</strong>
                <span>{row.invocation_kind}</span>
              </div>
              <p>{row.model_id || row.lane}</p>
              <div className="trace-chip-row">
                <span className="timeline-chip">{formatInteger(row.total_tokens)} tok</span>
                <span className="timeline-chip">{formatCurrency(row.total_cost)}</span>
                {row.task_id ? <span className="timeline-chip">{row.task_id}</span> : null}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
