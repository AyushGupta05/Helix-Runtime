import { expect, test } from "@playwright/test";

const historyPayload = [];

const missionPayload = {
  mission_id: "mission-1",
  repo_path: "C:\\repo",
  objective: "Fix checkout tests",
  outcome: null,
  run_state: "running",
  active_phase: "market",
  active_bid_round: 1,
  latest_event_id: 0,
  branch_name: "codex/arbiter-mission-1",
  head_commit: null,
  latest_diff_summary: "",
  winner_bid_id: null,
  standby_bid_id: null,
  decision_history: [],
  failed_attempt_history: [],
  tasks: [
    {
      task_id: "T1_localize",
      title: "Localize the likely root cause",
      task_type: "localize",
      status: "completed",
      requirement_level: "required",
      dependencies: []
    },
    {
      task_id: "T2_bugfix",
      title: "Implement the safest validated bug fix",
      task_type: "bugfix",
      status: "running",
      requirement_level: "required",
      dependencies: ["T1_localize"]
    }
  ],
  bids: [],
  events: [],
  validation_report: null
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const eventSources = [];
    class MockEventSource extends EventTarget {
      constructor(url) {
        super();
        this.url = url;
        eventSources.push(this);
      }

      close() {}
    }

    window.EventSource = MockEventSource;
    window.__emitMissionEvent = (eventType, payload, id = Date.now()) => {
      for (const source of eventSources) {
        const event = new Event(eventType);
        event.data = JSON.stringify(payload);
        event.lastEventId = String(id);
        source.dispatchEvent(event);
      }
    };
  });

  await page.route("**/api/missions", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: { mission_id: "mission-1", run_state: "running" } });
      return;
    }
    await route.fulfill({ json: historyPayload });
  });

  await page.route("**/api/missions/mission-1", async (route) => {
    await route.fulfill({ json: missionPayload });
  });

  await page.route("**/api/missions/mission-1/pause", async (route) => {
    await route.fulfill({ json: { mission_id: "mission-1", run_state: "pause_requested" } });
  });

  await page.route("**/api/missions/mission-1/resume", async (route) => {
    await route.fulfill({ json: { mission_id: "mission-1", run_state: "running" } });
  });

  await page.route("**/api/missions/mission-1/cancel", async (route) => {
    await route.fulfill({ json: { mission_id: "mission-1", run_state: "cancelling" } });
  });
});

test("launches a mission and renders live event updates", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Repo Path").fill("C:\\repo");
  await page.getByLabel("Objective").fill("Fix checkout tests");
  await page.getByRole("button", { name: "Launch mission" }).click();

  await expect(page.getByText("Fix checkout tests")).toBeVisible();
  await page.evaluate(() =>
    window.__emitMissionEvent(
      "standby.promoted",
      {
        created_at: "2026-03-20T10:00:04Z",
        message: "Standby promoted after failure.",
        payload: { bid_id: "bid-2" }
      },
      11
    )
  );
  await expect(page.getByText(/Standby promoted after failure/i)).toBeVisible();
});

test("keeps the control room after reload", async ({ page }) => {
  await page.goto("/missions/mission-1");
  await expect(page.getByText("Task Graph")).toBeVisible();
  await page.reload();
  await expect(page.getByText("Bid Market")).toBeVisible();
  await expect(page.getByText("Live Timeline")).toBeVisible();
});
