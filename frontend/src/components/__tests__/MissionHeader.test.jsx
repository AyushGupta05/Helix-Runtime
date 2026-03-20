import { render, screen } from "@testing-library/react";

import MissionHeader from "../MissionHeader";

describe("MissionHeader", () => {
  it("renders pause and cancel controls for a running mission", () => {
    render(
      <MissionHeader
        mission={{
          mission_id: "abc123",
          objective: "Fix tests",
          repo_path: "C:\\repo",
          run_state: "running",
          active_phase: "market",
          outcome: null,
          branch_name: "codex/arbiter-abc123",
          head_commit: null
        }}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: /Pause/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
  });
});
