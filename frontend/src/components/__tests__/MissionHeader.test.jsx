import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MissionHeader from "../MissionHeader";

describe("MissionHeader", () => {
  it("renders the compact mission top bar with prompt, tabs, and controls", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    const onCancel = vi.fn();

    render(
      <MissionHeader
        mission={{
          mission_id: "abc123",
          objective: "Fix tests",
          repo_path: "C:\\repo",
          run_state: "running",
          active_phase: "market",
          outcome: null,
          runtime_seconds: 132,
          updated_at: "2026-03-20T10:00:00Z",
          civic_connection: {
            status: "healthy",
            toolkit_id: "toolkit-7",
            checked_at: "2026-03-20T10:00:00Z"
          },
          available_skills: ["github_context", "knowledge_context"],
          recent_civic_actions: [
            {
              audit_id: "audit-1",
              output_payload: {
                authorization_url: "https://civic.example.test/connect/github"
              }
            }
          ]
        }}
        busy={false}
        activeTab="live"
        onTabChange={onTabChange}
        onResume={vi.fn()}
        onCancel={onCancel}
      />
    );

    await user.click(screen.getByRole("button", { name: /Mission Intelligence/i }));
    await user.click(screen.getByRole("button", { name: /Cancel/i }));

    expect(onTabChange).toHaveBeenCalledWith("intelligence");
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Connect GitHub/i })).toHaveAttribute(
      "href",
      "https://civic.example.test/connect/github"
    );
    expect(screen.getByText(/Live Prompt/i)).toBeInTheDocument();
    expect(screen.getByText(/Fix tests/i)).toBeInTheDocument();
    expect(screen.getByText("repo")).toBeInTheDocument();
    expect(screen.getByText(/^Market$/)).toBeInTheDocument();
    expect(screen.getByText(/Elapsed/i)).toBeInTheDocument();
    expect(screen.getByText(/2 civic skills/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Live Market/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Outcome/i })).toBeInTheDocument();
  });
});
