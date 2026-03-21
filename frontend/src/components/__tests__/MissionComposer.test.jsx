import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MissionComposer from "../MissionComposer";

describe("MissionComposer", () => {
  it("submits simple and advanced mission fields", async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn().mockResolvedValue(undefined);

    render(<MissionComposer busy={false} blocked={false} onSubmit={handleSubmit} />);

    await user.type(screen.getByLabelText(/Repo Path/i), "C:\\repo");
    await user.type(screen.getByLabelText(/Objective/i), "Fix tests");
    await user.click(screen.getByText(/Advanced mission settings/i));
    await user.type(screen.getByLabelText(/Constraints/i), "no api breaks,keep churn low");
    await user.type(screen.getByLabelText(/Preferences/i), "prefer tests");
    await user.type(screen.getByLabelText(/Protected Paths/i), "src/api.py");
    await user.type(screen.getByLabelText(/Public API Surface/i), "src/sdk.py");
    await user.type(screen.getByLabelText(/Benchmark Requirement/i), "pytest tests/perf");
    await user.clear(screen.getByLabelText(/Max Runtime/i));
    await user.type(screen.getByLabelText(/Max Runtime/i), "15");

    await user.click(screen.getByRole("button", { name: /Launch mission/i }));

    expect(handleSubmit).toHaveBeenCalledWith({
      repo: "C:\\repo",
      objective: "Fix tests",
      constraints: ["no api breaks", "keep churn low"],
      preferences: ["prefer tests"],
      requested_skills: [],
      protected_paths: ["src/api.py"],
      public_api_surface: ["src/sdk.py"],
      benchmark_requirement: "pytest tests/perf",
      max_runtime: 15
    });
  });
});
