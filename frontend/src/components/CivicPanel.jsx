import { formatInteger, humanizeEventType, relativeTime } from "../lib/format";

function capabilityLabel(capability) {
  return (
    capability?.label ??
    capability?.name ??
    capability?.capability_id ??
    capability?.id ??
    "governed capability"
  );
}

function connectionState(connection) {
  const raw = String(connection?.status ?? connection?.state ?? "idle");
  return raw.replace(/[_-]/g, " ");
}

function envelopeStatus(envelope) {
  return String(envelope?.status ?? envelope?.policy_decision ?? "captured").replace(/[_-]/g, " ");
}

function authUrlFor(mission) {
  const challenge = [...(mission?.recent_civic_actions ?? [])].reverse().find(
    (entry) =>
      entry?.output_payload?.authorization_url ??
      entry?.payload?.output_payload?.authorization_url
  );
  return (
    challenge?.output_payload?.authorization_url ??
    challenge?.payload?.output_payload?.authorization_url ??
    null
  );
}

export default function CivicPanel({ mission }) {
  const capabilities = mission?.civic_capabilities ?? [];
  const skills = mission?.available_skills ?? [];
  const envelopes = mission?.governed_bid_envelopes ?? [];
  const recentActions = [...(mission?.recent_civic_actions ?? [])].slice(-4).reverse();
  const blockedBids = (mission?.bids ?? []).filter(
    (bid) =>
      bid?.rejection_reason ||
      bid?.civic_preflight?.decision === "blocked" ||
      bid?.governed_envelope?.status === "blocked"
  );
  const authUrl = authUrlFor(mission);
  const connection = mission?.civic_connection ?? {};

  return (
    <section className="panel civic-panel">
      <div className="section-title">
        <p className="eyebrow">Civic Runtime</p>
        <h2>Governed capability plane</h2>
        <p>
          Strategy admissibility changes when Civic policy, evidence freshness, or capability
          availability changes.
        </p>
      </div>

      <div className="civic-stat-grid">
        <article className="insight-card">
          <span>Connection</span>
          <strong>{connectionState(connection)}</strong>
          <p>{connection.last_checked_at ? relativeTime(connection.last_checked_at) : "waiting"}</p>
        </article>
        <article className="insight-card">
          <span>Capabilities</span>
          <strong>{formatInteger(capabilities.length)}</strong>
          <p>{capabilities.length ? "Active governed tools discovered." : "No capabilities loaded yet."}</p>
        </article>
        <article className="insight-card">
          <span>Skills</span>
          <strong>{formatInteger(skills.length)}</strong>
          <p>{skills.join(" | ") || "No derived skills yet."}</p>
        </article>
        <article className="insight-card">
          <span>Blocked strategies</span>
          <strong>{formatInteger(blockedBids.length)}</strong>
          <p>{blockedBids.length ? "Policy is actively narrowing the market." : "No strategy is blocked right now."}</p>
        </article>
      </div>

      {authUrl ? (
        <div className="civic-auth-banner">
          <div>
            <strong>GitHub evidence is waiting on approval</strong>
            <p>Authorize the governed GitHub lane so Civic can add external evidence to the market.</p>
          </div>
          <a className="primary-button" href={authUrl} target="_blank" rel="noreferrer">
            Connect GitHub
          </a>
        </div>
      ) : null}

      <div className="civic-section">
        <div className="section-title">
          <h2>Capability set</h2>
          <p>Active skills and capabilities currently shaping the strategy space.</p>
        </div>
        <div className="civic-chip-grid">
          {capabilities.length ? (
            capabilities.slice(0, 8).map((capability, index) => (
              <span key={`${capabilityLabel(capability)}-${index}`} className="file-chip">
                {capabilityLabel(capability)}
              </span>
            ))
          ) : (
            <div className="section-empty">Governed tools will appear here after Civic refreshes.</div>
          )}
          {skills.map((skill) => (
            <span key={skill} className="muted-chip">
              skill {skill}
            </span>
          ))}
        </div>
      </div>

      <div className="civic-section">
        <div className="section-title">
          <h2>Envelope status</h2>
          <p>Which strategies are currently admitted, governed, or blocked.</p>
        </div>
        <div className="ledger-list">
          {envelopes.length ? (
            envelopes.slice(-4).reverse().map((envelope, index) => (
              <article
                key={envelope.envelope_id ?? envelope.bid_id ?? index}
                className="ledger-row"
              >
                <div>
                  <strong>{envelope.role ?? envelope.strategy_family ?? envelope.bid_id ?? "Governed envelope"}</strong>
                  <p>{(Array.isArray(envelope.reasoning) ? envelope.reasoning : []).join(" ") || envelope.constraints?.join(", ") || "Governed constraints recorded."}</p>
                </div>
                <span>{envelopeStatus(envelope)}</span>
              </article>
            ))
          ) : blockedBids.length ? (
            blockedBids.slice(0, 3).map((bid) => (
              <article key={bid.bid_id} className="ledger-row">
                <div>
                  <strong>{bid.role ?? bid.strategy_family}</strong>
                  <p>{bid.rejection_reason ?? "Blocked during policy preflight."}</p>
                </div>
                <span>blocked</span>
              </article>
            ))
          ) : (
            <div className="section-empty">No envelope decisions have been recorded yet.</div>
          )}
        </div>
      </div>

      <div className="civic-section">
        <div className="section-title">
          <h2>Recent governed actions</h2>
          <p>External evidence and policy transitions land here in chronological order.</p>
        </div>
        <div className="ledger-list">
          {recentActions.length ? (
            recentActions.map((action, index) => (
              <article key={action.audit_id ?? index} className="ledger-row">
                <div>
                  <strong>{humanizeEventType(action.event_type ?? action.action_type ?? "civic.action.executed")}</strong>
                  <p>{action.reason ?? action.message ?? "Governed Civic action recorded."}</p>
                </div>
                <span>{action.created_at ? relativeTime(action.created_at) : "captured"}</span>
              </article>
            ))
          ) : (
            <div className="section-empty">No governed actions have been recorded yet.</div>
          )}
        </div>
      </div>
    </section>
  );
}
