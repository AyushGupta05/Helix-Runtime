import { describe, expect, it } from "vitest";

import { mergeMissionEvent, normalizeIncomingBid } from "../missionStream";

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

  it("advances mission phase and checkpoint state from live events", () => {
    const snapshot = {
      mission_id: "mission-1",
      run_state: "running",
      active_phase: "collect",
      latest_event_id: 0,
      events: [],
      recent_trace: [],
      tasks: [],
      bids: [],
      accepted_checkpoints: [],
      worktree_state: {},
      latest_diff_summary: ""
    };

    const afterScan = mergeMissionEvent(snapshot, {
      id: 1,
      event_type: "repo.scan.completed",
      created_at: "2026-03-21T10:00:00Z",
      message: "Repository scan completed.",
      payload: {
        runtime: "python",
        risky_paths: ["calc.py"]
      }
    });

    expect(afterScan.active_phase).toBe("decompose");
    expect(afterScan.repo_snapshot.capabilities.runtime).toBe("python");

    const afterCheckpoint = mergeMissionEvent(afterScan, {
      id: 2,
      event_type: "checkpoint.accepted",
      created_at: "2026-03-21T10:01:00Z",
      message: "Accepted checkpoint committed.",
      payload: {
        checkpoint_id: "chk-1",
        label: "T2_bugfix",
        commit_sha: "abc123def456",
        diff_summary: "1 file changed",
        rollback_pointer: "prev-1"
      }
    });

    expect(afterCheckpoint.active_phase).toBe("select_task");
    expect(afterCheckpoint.head_commit).toBe("abc123def456");
    expect(afterCheckpoint.accepted_checkpoints).toHaveLength(1);
    expect(afterCheckpoint.worktree_state.has_changes).toBe(false);
    expect(afterCheckpoint.worktree_state.accepted_commit).toBe("abc123def456");
  });
});
