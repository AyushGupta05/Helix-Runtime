import { render, screen } from "@testing-library/react";

import MissionOutcomeView from "../MissionOutcomeView";

describe("MissionOutcomeView", () => {
  it("summarizes civic influence and governed capabilities in the outcome view", () => {
    render(
      <MissionOutcomeView
        mission={{
          mission_id: "mission-1",
          repo_path: "C:\\repo",
          objective: "Fix tests",
          outcome: "success",
          run_state: "finalized",
          branch_name: "codex/helix-mission-1",
          head_commit: "abcdef1234567890",
          runtime_seconds: 120,
          accepted_checkpoints: [{ checkpoint_id: "chk-1", label: "checkpoint", commit_sha: "abcdef1234567890" }],
          civic_connection: { status: "healthy" },
          available_skills: ["github_context", "knowledge_context"],
          skill_outputs: {
            github_context: { summary: "CI status is green." }
          },
          governed_bid_envelopes: [{ bid_id: "bid-1" }],
          recent_civic_actions: [{ audit_id: "audit-1", event_type: "civic.action.executed", created_at: "2026-03-20T10:00:00Z" }],
          validation_report: { passed: true, notes: ["All checks passed."] },
          outcome_summary: {
            plain_summary: "Mission completed successfully."
          }
        }}
        trace={[
          {
            id: 1,
            trace_type: "proposal.selected",
            title: "Proposal selected",
            message: "Safe path chosen.",
            payload: { summary: "Safe path chosen." }
          }
        ]}
        diffState={{}}
        usageSummary={{ mission: { total_cost: 0.1234, total_tokens: 123 } }}
        selectedBid={{ confidence: 0.9, risk: 0.2, mission_rationale: "A safe path." }}
        latestProposalTrace={{ payload: { summary: "Safe path chosen." } }}
        onOpenIntelligence={vi.fn()}
        onOpenLiveMarket={vi.fn()}
      />
    );

    expect(screen.getByText(/Civic influence/i)).toBeInTheDocument();
    expect(screen.getAllByText(/healthy \| 2 skills/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/governed actions recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/Checked governed capabilities/i)).toBeInTheDocument();
  });
});
