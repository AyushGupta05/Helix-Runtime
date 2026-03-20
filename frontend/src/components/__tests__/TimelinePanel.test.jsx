import { render, screen } from "@testing-library/react";

import TimelinePanel from "../TimelinePanel";

describe("TimelinePanel", () => {
  it("shows recovery and checkpoint events in reverse chronological order", () => {
    render(
      <TimelinePanel
        validationReport={{ task_id: "T2", passed: false, notes: ["Validation failed"] }}
        events={[
          {
            id: 1,
            event_type: "checkpoint.reverted",
            created_at: "2026-03-20T10:00:00Z",
            message: "Worktree reverted to accepted checkpoint.",
            payload: { commit_sha: "1234567890abcdef" }
          },
          {
            id: 2,
            event_type: "standby.promoted",
            created_at: "2026-03-20T10:00:02Z",
            message: "Standby promoted after failure.",
            payload: { bid_id: "bid-2" }
          }
        ]}
      />
    );

    expect(screen.getByText(/Latest validation/i)).toBeInTheDocument();
    expect(screen.getAllByText(/standby promoted/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/checkpoint reverted/i).length).toBeGreaterThan(0);
    const emphasis = screen.getAllByText(/standby promoted|checkpoint reverted/i);
    expect(emphasis[0]).toHaveTextContent("standby promoted");
  });
});
