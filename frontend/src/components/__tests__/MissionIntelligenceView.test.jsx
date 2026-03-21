import { render, screen } from "@testing-library/react";

import MissionIntelligenceView from "../MissionIntelligenceView";

describe("MissionIntelligenceView", () => {
  it("shows Civic capability, skill, envelope, and action evidence in the civic section", () => {
    render(
      <MissionIntelligenceView
        mission={{
          mission_id: "mission-1",
          repo_path: "C:\\repo",
          branch_name: "codex/helix-mission-1",
          head_commit: "abcdef1234567890",
          civic_connection: {
            status: "healthy",
            toolkit_id: "toolkit-7",
            checked_at: "2026-03-20T10:00:00Z"
          },
          civic_capabilities: {
            provider: "Civic",
            source: "MCP"
          },
          available_skills: ["github_context", "knowledge_context"],
          skill_health: {
            github_context: "healthy",
            knowledge_context: "pending"
          },
          skill_outputs: {
            github_context: {
              summary: "CI status is green.",
              provenance: "Civic",
              confidence: 0.91,
              freshness: "2026-03-20T10:00:00Z"
            }
          },
          governed_bid_envelopes: [
            {
              bid_id: "bid-1",
              allowed_skills: ["github_context"],
              policy_decision: "allowed",
              runtime_limit_seconds: 120,
              toolkit_id: "toolkit-7"
            }
          ],
          recent_civic_actions: [
            {
              audit_id: "audit-1",
              event_type: "civic.action.executed",
              created_at: "2026-03-20T10:00:02Z",
              status: "executed",
              reason: "Fetched CI status"
            }
          ],
          civic_audit_summary: { preflight: 1 }
        }}
        history={[]}
        trace={[
          {
            id: 1,
            trace_type: "civic.action.executed",
            title: "Civic action executed",
            message: "Fetched CI status.",
            payload: { audit_id: "audit-1", skill: "github_context" }
          }
        ]}
        diffState={{}}
        usageSummary={{}}
        initialSection="civic"
        onSelectMission={vi.fn()}
      />
    );

    expect(screen.getByText(/Civic evidence/i)).toBeInTheDocument();
    expect(screen.getByText(/healthy/i)).toBeInTheDocument();
    expect(screen.getAllByText(/toolkit-7/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Active skills/i)).toBeInTheDocument();
    expect(screen.getAllByText(/github_context/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/CI status is green/i)).toBeInTheDocument();
    expect(screen.getByText(/bid-1/i)).toBeInTheDocument();
    expect(screen.getByText(/Fetched CI status/i)).toBeInTheDocument();
  });
});
