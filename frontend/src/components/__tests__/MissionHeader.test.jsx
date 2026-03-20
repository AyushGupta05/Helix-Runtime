import { render, screen } from "@testing-library/react";

import MissionHeader from "../MissionHeader";

describe("MissionHeader", () => {
  it("renders controls and mission usage metrics for a running mission", () => {
    render(
      <MissionHeader
        mission={{
          mission_id: "abc123",
          objective: "Fix tests",
          repo_path: "C:\\repo",
          run_state: "running",
          active_phase: "market",
          outcome: null,
          active_task: { task_id: "T2_bugfix" },
          bids: [],
          validation_report: { task_id: "T1_localize", passed: true },
          branch_name: "codex/arbiter-abc123",
          head_commit: null
        }}
        usageSummary={{ mission: { total_tokens: 456, total_cost: 0.1234 } }}
        latestProposalTrace={{ provider: "openai" }}
        latestCheckpoint={{ commit_sha: "1234567890abcdef" }}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: /Pause/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByText(/Mission tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/^456$/)).toBeInTheDocument();
  });
});
