import { describe, expect, it } from "vitest";

import { normalizeIncomingBid } from "../missionStream";

describe("normalizeIncomingBid", () => {
  it("preserves provider-backed provenance when the stream payload includes it", () => {
    const bid = normalizeIncomingBid({
      payload: {
        bid_id: "bid-1",
        task_id: "T1",
        role: "Safe",
        provider: "openai",
        model_id: "gpt-4.1",
        generation_mode: "provider_model"
      }
    });

    expect(bid.provider).toBe("openai");
    expect(bid.model_id).toBe("gpt-4.1");
    expect(bid.generation_mode).toBe("provider_model");
  });

  it("only marks system fallback when the payload says so", () => {
    const bid = normalizeIncomingBid({
      payload: {
        bid_id: "bid-2",
        task_id: "T1",
        role: "Safe",
        provider: "system"
      }
    });

    expect(bid.provider).toBe("system");
    expect(bid.generation_mode).toBe("deterministic_fallback");
  });
});
