import { render, screen } from "@testing-library/react";

import EventStrip from "../EventStrip";

describe("EventStrip", () => {
  it("renders a stage-aware mission timeline with event context", () => {
    render(
      <EventStrip
        mission={{
          active_phase: "recover",
          run_state: "running",
          accepted_checkpoints: [{ checkpoint_id: "chk-1" }]
        }}
        events={[
          {
            id: 1,
            event_type: "repo.scan.completed",
            created_at: "2026-03-20T10:00:01Z",
            message: "Repository scan completed.",
            payload: { task_id: "T1_localize", provider: "openai" }
          },
          {
            id: 2,
            event_type: "recovery.round_opened",
            created_at: "2026-03-20T10:00:05Z",
            message: "Rebidding with prior evidence.",
            payload: { task_id: "T2_bugfix", round: 2 }
          }
        ]}
        trace={[
          {
            id: 9,
            trace_type: "proposal.selected",
            title: "Proposal selected",
            message: "Safe path chosen.",
            payload: { summary: "Safe path chosen." }
          }
        ]}
      />
    );

    expect(screen.getByText(/Mission Timeline/i)).toBeInTheDocument();
    expect(screen.getByText(/Current phase/i)).toBeInTheDocument();
    expect(screen.getAllByText(/^Recover$/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Repository Scan Completed/i)).toBeInTheDocument();
    expect(screen.getByText(/Rebidding with Prior Evidence/i)).toBeInTheDocument();
    expect(screen.getByText(/task T1_localize/i)).toBeInTheDocument();
  });
});
