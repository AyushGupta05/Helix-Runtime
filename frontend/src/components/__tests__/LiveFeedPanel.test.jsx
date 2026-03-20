import { render, screen } from "@testing-library/react";

import LiveFeedPanel from "../LiveFeedPanel";

describe("LiveFeedPanel", () => {
  it("renders live action and model activity feeds from representative data", () => {
    const { container } = render(
      <LiveFeedPanel
        validationReport={{ task_id: "T-42", passed: false }}
        events={[
          {
            id: 1,
            event_type: "task.running",
            created_at: "2026-03-20T10:00:00Z",
            message: "Task T-42 is running.",
            payload: { task_id: "T-42" }
          },
          {
            id: 2,
            event_type: "market.opened",
            created_at: "2026-03-20T10:00:05Z",
            message: "Competitive market opened for T-42.",
            payload: { task_id: "T-42" }
          },
          {
            id: 3,
            event_type: "tool.executed",
            created_at: "2026-03-20T10:00:10Z",
            message: "Material edit applied.",
            payload: { task_id: "T-42", reason: "Updated isolated worktree." }
          }
        ]}
        trace={[
          {
            id: 10,
            trace_type: "proposal.selected",
            created_at: "2026-03-20T10:00:12Z",
            title: "Proposal selected",
            message: "OpenAI proposal selected for T-42.",
            payload: {
              provider: "openai",
              lane: "bid.deep.openai",
              summary: "Apply the safe fix",
              model_id: "gpt-4.1"
            }
          }
        ]}
        invocations={[
          {
            invocation_id: "inv-1",
            provider: "anthropic",
            invocation_kind: "analysis",
            status: "completed",
            started_at: "2026-03-20T10:00:01Z",
            completed_at: "2026-03-20T10:00:03Z",
            model_id: "claude-sonnet-4-5",
            prompt_preview: "Inspect checkout tests and suggest the safest fix.",
            response_preview: "Focus on the checkout edge case and add a regression test."
          }
        ]}
        executionSteps={[
          {
            step_id: "step-1",
            task_id: "T-42",
            bid_id: "bid-1",
            action_type: "edit_file",
            tool_name: "filesystem",
            description: "Applied the planned fix",
            timestamp: "2026-03-20T10:00:11Z"
          }
        ]}
      />
    );

    expect(screen.getByText("Live action feed")).toBeInTheDocument();
    expect(screen.getByText("Live model activity / reasoning")).toBeInTheDocument();
    expect(screen.getByText(/Market opened/i, { selector: ".live-feed-entry-title" })).toBeInTheDocument();
    expect(screen.getByText(/Proposal selected/i, { selector: ".live-feed-entry-title" })).toBeInTheDocument();
    expect(screen.getByText(/Prompt preview/i)).toBeInTheDocument();
    expect(screen.getByText(/Inspect checkout tests/i)).toBeInTheDocument();

    const columns = container.querySelectorAll(".live-feed-column");
    expect(columns).toHaveLength(2);
    expect(columns[0].textContent).toMatch(/Tool executed|Market opened|Task running/i);
    expect(columns[1].textContent).toMatch(/claude-sonnet-4-5|OpenAI proposal selected/i);
  });
});
