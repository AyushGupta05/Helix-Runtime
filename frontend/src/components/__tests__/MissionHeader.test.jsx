import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MissionHeader from "../MissionHeader";

describe("MissionHeader", () => {
  it("renders the Helix mission header with tabs, status cluster, and controls", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();

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
          accepted_checkpoints: [
            { checkpoint_id: "chk-1", label: "T2_bugfix", commit_sha: "1234567890abcdef" }
          ],
          validation_report: { task_id: "T1_localize", passed: true },
          branch_name: "codex/helix-abc123",
          head_commit: "1234567890abcdef",
          runtime_seconds: 132,
          civic_audit_summary: { preflight: 2 }
        }}
        usageSummary={{ mission: { total_tokens: 456, total_cost: 0.1234 } }}
        busy={false}
        activeTab="live"
        onTabChange={onTabChange}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    await user.click(screen.getByRole("button", { name: /Mission Intelligence/i }));

    expect(onTabChange).toHaveBeenCalledWith("intelligence");
    expect(screen.getByRole("button", { name: /Pause/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByText("Helix Runtime")).toBeInTheDocument();
    expect(screen.getByText(/Fix tests/i)).toBeInTheDocument();
    expect(screen.getByText(/Repo: repo/i)).toBeInTheDocument();
    expect(screen.getByText(/Task: T2_bugfix/i)).toBeInTheDocument();
    expect(screen.getByText(/Status: Market/i)).toBeInTheDocument();
    expect(screen.getByText(/Spend: \$0.1234/i)).toBeInTheDocument();
    expect(screen.getByText(/Time elapsed: 2m 12s/i)).toBeInTheDocument();
    expect(screen.getByText(/Mission health/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Latest checkpoint/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Civic status/i)).toBeInTheDocument();
    expect(screen.getByText(/Validator status/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Live Market/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Outcome/i })).toBeInTheDocument();
  });
});
