import { describe, expect, it } from "vitest";

import { EVENT_TYPES } from "../api";

describe("api event types", () => {
  it("tracks model and bidding provenance events", () => {
    expect(EVENT_TYPES).toContain("model.invocation.started");
    expect(EVENT_TYPES).toContain("model.invocation.completed");
    expect(EVENT_TYPES).toContain("model.invocation.failed");
    expect(EVENT_TYPES).toContain("bidding.degraded_mode_entered");
    expect(EVENT_TYPES).toContain("bidding.architecture_violation");
    expect(EVENT_TYPES).not.toContain("provider.invocation.started");
    expect(EVENT_TYPES).not.toContain("provider.invocation.completed");
    expect(EVENT_TYPES).not.toContain("provider.invocation.failed");
  });
});
