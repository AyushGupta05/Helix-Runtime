import { describe, expect, it } from "vitest";

import { getMissionElapsedSeconds } from "../format";

describe("getMissionElapsedSeconds", () => {
  it("does not let live event resets move the elapsed clock backward", () => {
    const mission = {
      run_state: "running",
      runtime_seconds: 1,
      created_at: "2026-03-21T18:02:22.000Z",
      events: [{ created_at: "2026-03-21T18:02:24.000Z" }]
    };

    const elapsed = getMissionElapsedSeconds(mission, {
      now: new Date("2026-03-21T18:02:39.000Z").getTime(),
      snapshotReceivedAt: new Date("2026-03-21T18:02:35.000Z").getTime()
    });

    expect(elapsed).toBe(15);
  });

  it("keeps finalized missions pinned to the reported runtime", () => {
    const mission = {
      run_state: "finalized",
      runtime_seconds: 42,
      created_at: "2026-03-21T18:02:22.000Z",
      updated_at: "2026-03-21T18:03:04.000Z"
    };

    const elapsed = getMissionElapsedSeconds(mission, {
      now: new Date("2026-03-21T18:10:00.000Z").getTime(),
      snapshotReceivedAt: new Date("2026-03-21T18:10:00.000Z").getTime()
    });

    expect(elapsed).toBe(42);
  });
});
