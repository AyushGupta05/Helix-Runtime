import { expect, test } from "@playwright/test";

let historyPayload = [];
let missionPayload = {};
let usagePayload = {
  mission: { total_tokens: 0, total_cost: 0 },
  active_task: { total_tokens: 0, total_cost: 0 },
  by_provider: {}
};

function buildMissionPayload(overrides = {}) {
  const now = Date.now();
  return {
    mission_id: "mission-1",
    repo_path: "C:\\repo",
    objective: "Fix checkout tests",
    outcome: null,
    run_state: "running",
    active_phase: "market",
    active_task_id: "T2_bugfix",
    active_bid_round: 1,
    simulation_round: 0,
    recovery_round: 0,
    latest_event_id: 0,
    branch_name: "codex/arbiter-mission-1",
    head_commit: "abc123def4567890",
    created_at: new Date(now - 90_000).toISOString(),
    updated_at: new Date(now - 2_000).toISOString(),
    runtime_seconds: 60,
    latest_diff_summary: "",
    winner_bid_id: null,
    standby_bid_id: null,
    decision_history: [],
    failed_attempt_history: [],
    available_skills: [],
    skill_health: {},
    skill_outputs: {},
    governed_bid_envelopes: [],
    recent_civic_actions: [],
    civic_connection: {
      status: "connected",
      toolkit_id: "toolkit-7",
      last_checked_at: new Date(now - 5_000).toISOString()
    },
    simulation_summary: {},
    simulation_activity: [],
    bidding_state: {
      generation_mode: "provider_model",
      degraded: false,
      warning: null
    },
    accepted_checkpoints: [
      {
        checkpoint_id: "chk-1",
        label: "T2_bugfix",
        commit_sha: "abc123def4567890",
        created_at: new Date(now - 60_000).toISOString(),
        diff_summary: "1 file changed",
        rollback_pointer: "chk-0",
        affected_files: ["calc.py"]
      }
    ],
    worktree_state: {
      worktree_path: "C:\\repo\\.arbiter\\worktrees\\mission-1\\primary",
      changed_files: [],
      diff_stat: "",
      diff_patch: "",
      has_changes: false,
      accepted_commit: "abc123def4567890",
      accepted_checkpoint_id: "chk-1",
      reason: "Changes accepted on the Arbiter-managed branch."
    },
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
    validation_report: null,
    ...overrides
  };
}

async function wireMissionApi(page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;

    if (pathname === "/api/missions" && request.method() === "POST") {
      await route.fulfill({
        json: { mission_id: "mission-1", repo_path: "C:\\repo", run_state: "running" }
      });
      return;
    }

    if (pathname === "/api/missions" && request.method() === "GET") {
      await route.fulfill({ json: historyPayload });
      return;
    }

    if (pathname === "/api/missions/mission-1") {
      await route.fulfill({ json: missionPayload });
      return;
    }

    if (pathname === "/api/missions/mission-1/trace") {
      await route.fulfill({ json: [] });
      return;
    }

    if (pathname === "/api/missions/mission-1/diff") {
      await route.fulfill({ json: { worktree_state: missionPayload.worktree_state } });
      return;
    }

    if (pathname === "/api/missions/mission-1/usage") {
      await route.fulfill({ json: usagePayload });
      return;
    }

    if (pathname.endsWith("/pause")) {
      await route.fulfill({ json: { mission_id: "mission-1", run_state: "paused" } });
      return;
    }

    if (pathname.endsWith("/resume")) {
      await route.fulfill({ json: { mission_id: "mission-1", run_state: "running" } });
      return;
    }

    if (pathname.endsWith("/cancel")) {
      await route.fulfill({ json: { mission_id: "mission-1", run_state: "cancelling" } });
      return;
    }

    await route.continue();
  });
}

test.beforeEach(async ({ page }) => {
  historyPayload = [];
  missionPayload = buildMissionPayload();
  usagePayload = {
    mission: { total_tokens: 0, total_cost: 0 },
    active_task: { total_tokens: 0, total_cost: 0 },
    by_provider: {}
  };

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
        Object.defineProperty(event, "data", {
          configurable: true,
          enumerable: true,
          value: JSON.stringify(payload)
        });
        Object.defineProperty(event, "lastEventId", {
          configurable: true,
          enumerable: true,
          value: String(id)
        });
        source.dispatchEvent(event);
      }
    };
  });

  await wireMissionApi(page);
});

test("stays on the launcher until a user submits a mission prompt", async ({ page }) => {
  historyPayload = [
    {
      mission_id: "mission-1",
      repo_path: "C:\\repo",
      objective: "Fix checkout tests",
      run_state: "running",
      outcome: null,
      branch_name: "codex/arbiter-mission-1",
      updated_at: new Date().toISOString()
    }
  ];

  await page.goto("/");

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText(/Nothing starts until you submit this form\./i)).toBeVisible();
  await expect(page.getByRole("heading", { name: /New Mission/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Open Live Workspace/i })).toHaveCount(0);
  await expect(page.getByText(/Existing mission detected/i)).toHaveCount(0);
  await expect(page.getByText(/Current Mission/i)).toHaveCount(0);
  await expect(page.getByText(/Mission Queue/i)).toBeVisible();
  await expect(page.getByText(/Live Prompt/i)).toHaveCount(0);
});

test("streams live market and monte carlo updates with Civic auth prompts", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Repo Path").fill("C:\\repo");
  await page.getByLabel("Objective").fill("Fix checkout tests");
  await page.getByRole("button", { name: /Launch mission/i }).click();

  await expect(page.getByText(/Live Prompt/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /Cancel/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Live Market/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Strategy Bidding Board/i })).toBeVisible();

  await page.evaluate(() =>
    window.__emitMissionEvent(
      "bid.generated",
      {
        created_at: "2026-03-20T10:00:01Z",
        message: "Safe strategy generated.",
        payload: {
          bid_id: "bid-1",
          task_id: "T2_bugfix",
          role: "Safe",
          provider: "openai",
          generation_mode: "provider_model",
          strategy_family: "localized_fix",
          strategy_summary: "Patch only the failing calculator function.",
          mission_rationale: "Use the smallest safe change and keep recovery cheap.",
          confidence: 0.82,
          risk: 0.18,
          estimated_runtime_seconds: 42,
          required_skills: ["github_context"],
          optional_skills: [],
          policy_friction_score: 0.12,
          capability_reliance_score: 0.56,
          status: "generated"
        }
      },
      11
    )
  );

  await page.evaluate(() =>
    window.__emitMissionEvent(
      "simulation.bid_scored",
      {
        created_at: "2026-03-20T10:00:02Z",
        message: "Monte Carlo scoring completed for bid-1.",
        payload: {
          bid_id: "bid-1",
          task_id: "T2_bugfix",
          score: 0.91,
          status: "simulated",
          search_summary: "Policy-aware Monte Carlo favors this path because it preserves rollback safety.",
          search_diagnostics: {
            sample_count: 12,
            success_rate: 0.83,
            rollback_rate: 0.08,
            capability_availability_probability: 0.94,
            policy_friction_cost: 0.12
          }
        }
      },
      12
    )
  );

  await expect(page.locator(".console-timeline-strip").getByText(/Safe strategy generated\./i)).toBeVisible();
  await expect(page.getByText(/Policy-aware Monte Carlo favors this path/i).first()).toBeVisible();

  await page.getByRole("button", { name: /Mission Intelligence/i }).click();
  await expect(page.getByRole("heading", { name: /Strategy Simulation/i })).toBeVisible();

  await page.evaluate(() =>
    window.__emitMissionEvent(
      "simulation.started",
      {
        created_at: "2026-03-20T10:00:03Z",
        message: "Bounded Monte Carlo simulation started.",
        payload: {
          task_id: "T2_bugfix",
          total_bids: 1,
          monte_carlo_samples: 12
        }
      },
      13
    )
  );

  await page.evaluate(() =>
    window.__emitMissionEvent(
      "simulation.rollout",
      {
        created_at: "2026-03-20T10:00:04Z",
        message: "Paper rollout completed for bid-1.",
        payload: {
          bid_id: "bid-1",
          task_id: "T2_bugfix",
          rollout: "paper",
          evidence: ["paper"]
        }
      },
      14
    )
  );

  await page.evaluate(() =>
    window.__emitMissionEvent(
      "civic.action.failed",
      {
        created_at: "2026-03-20T10:00:05Z",
        message: "GitHub auth required before governed read access can continue.",
        payload: {
          action_type: "open_pr_metadata",
          skill: "github_context",
          status: "failed",
          output_payload: {
            authorization_url: "https://civic.example.test/connect/github",
            error: "authorization_required"
          }
        }
      },
      15
    )
  );

  await expect(page.getByRole("link", { name: /Connect GitHub/i }).first()).toBeVisible();
});

test("ticks elapsed time locally and keeps the compact workspace after reload", async ({ page }) => {
  missionPayload = buildMissionPayload({
    runtime_seconds: 0,
    updated_at: new Date(Date.now() - 2_000).toISOString()
  });

  await page.goto("/missions/mission-1?repo=C%3A%5Crepo");

  const elapsed = page.locator(".mission-topbar-meta-item").filter({ hasText: /^Elapsed / });
  await expect(elapsed).toBeVisible();
  const before = await elapsed.textContent();
  await page.waitForTimeout(1300);
  const after = await elapsed.textContent();

  expect(before).not.toBe(after);
  await expect(page.getByRole("button", { name: /Cancel/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Pause/i })).toHaveCount(0);

  await page.reload();
  await expect(page.getByText(/Live Prompt/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: /Strategy Bidding Board/i })).toBeVisible();
});

test("shows cost unavailable when provider usage has tokens but missing billing metadata", async ({ page }) => {
  usagePayload = {
    mission: {
      total_tokens: 377,
      total_cost: 0,
      cost_status: "unavailable",
      cost_unavailable_invocation_count: 2
    },
    active_task: {
      total_tokens: 377,
      total_cost: 0,
      cost_status: "unavailable",
      cost_unavailable_invocation_count: 2
    },
    by_provider: {
      openai: {
        provider: "openai",
        total_tokens: 377,
        total_cost: 0,
        cost_status: "unavailable",
        cost_unavailable_invocation_count: 2
      }
    }
  };

  await page.goto("/missions/mission-1?repo=C%3A%5Crepo");

  await expect(page.getByRole("heading", { name: /Usage Signal/i })).toBeVisible();
  await expect(page.getByText("Spend: Cost unavailable")).toBeVisible();
  await expect(page.getByText(/2 provider calls missing cost metadata/i).first()).toBeVisible();
});
