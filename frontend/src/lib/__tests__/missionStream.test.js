import { describe, expect, it } from "vitest";

import { mergeMissionEvent, normalizeIncomingBid, reconcileMissionSnapshot } from "../missionStream";

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

    expect(afterScan.active_phase).toBe("strategize");
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

    expect(afterCheckpoint.active_phase).toBe("strategize");
    expect(afterCheckpoint.head_commit).toBe("abc123def456");
    expect(afterCheckpoint.accepted_checkpoints).toHaveLength(1);
    expect(afterCheckpoint.worktree_state.has_changes).toBe(false);
    expect(afterCheckpoint.worktree_state.accepted_commit).toBe("abc123def456");
  });

  it("does not let a stale snapshot poll overwrite newer live mission state", () => {
    const current = {
      mission_id: "mission-1",
      run_state: "running",
      active_phase: "recover",
      latest_event_id: 12,
      events: [
        {
          id: 12,
          event_type: "recovery.round_opened",
          created_at: "2026-03-21T10:00:12Z",
          message: "Rebidding with prior evidence.",
          payload: { task_id: "T2_bugfix", round: 2 }
        }
      ],
      recent_trace: [],
      tasks: [],
      bids: [],
      accepted_checkpoints: [],
      worktree_state: {},
      latest_diff_summary: ""
    };

    const incoming = {
      mission_id: "mission-1",
      run_state: "running",
      active_phase: "market",
      latest_event_id: 0,
      events: [],
      recent_trace: [],
      tasks: [],
      bids: [],
      accepted_checkpoints: [],
      worktree_state: {}
    };

    const reconciled = reconcileMissionSnapshot(current, incoming);

    expect(reconciled.latest_event_id).toBe(12);
    expect(reconciled.active_phase).toBe("recover");
    expect(reconciled.events).toHaveLength(1);
    expect(reconciled.events[0].event_type).toBe("recovery.round_opened");
  });

  it("preserves bid details when later stream events only carry status updates", () => {
    const snapshot = {
      mission_id: "mission-1",
      run_state: "running",
      active_phase: "strategize",
      latest_event_id: 1,
      events: [],
      recent_trace: [],
      tasks: [],
      bids: [
        {
          bid_id: "bid-1",
          task_id: "T1",
          role: "Safe",
          provider: "anthropic",
          model_id: "claude-haiku",
          strategy_summary: "Keep the patch narrowly scoped.",
          confidence: 0.82,
          risk: 0.18,
          status: "generated",
          selected: false,
          standby: false
        }
      ],
      accepted_checkpoints: [],
      worktree_state: {},
      latest_diff_summary: ""
    };

    const updated = mergeMissionEvent(snapshot, {
      id: 2,
      event_type: "bid.submitted",
      created_at: "2026-03-21T10:00:02Z",
      message: "Bid submitted.",
      payload: {
        bid_id: "bid-1",
        task_id: "T1",
        status: "submitted"
      }
    });

    expect(updated.bids).toHaveLength(1);
    expect(updated.bids[0].provider).toBe("anthropic");
    expect(updated.bids[0].model_id).toBe("claude-haiku");
    expect(updated.bids[0].strategy_summary).toBe("Keep the patch narrowly scoped.");
    expect(updated.bids[0].confidence).toBe(0.82);
    expect(updated.bids[0].risk).toBe(0.18);
    expect(updated.bids[0].status).toBe("submitted");
  });

  it("merges github publish skill output events into the live mission snapshot", () => {
    const snapshot = {
      mission_id: "mission-1",
      run_state: "running",
      active_phase: "finalize",
      latest_event_id: 10,
      events: [],
      recent_trace: [],
      tasks: [],
      bids: [],
      accepted_checkpoints: [],
      worktree_state: {},
      latest_diff_summary: "",
      skill_outputs: {},
      recent_civic_actions: []
    };

    const updated = mergeMissionEvent(snapshot, {
      id: 11,
      event_type: "civic.skill.github_publish",
      created_at: "2026-03-22T06:18:39Z",
      message: "Pull request opened from mission branch into main.",
      payload: {
        skill_output: {
          published: true,
          pull_request: {
            result: [
              {
                type: "text",
                text: "{\"url\":\"https://github.com/example/repo/pull/42\"}"
              }
            ]
          }
        }
      }
    });

    expect(updated.skill_outputs.github_publish.published).toBe(true);
    expect(updated.skill_outputs.github_publish.pull_request.result[0].text).toContain("/pull/42");
    expect(updated.recent_civic_actions).toHaveLength(1);
    expect(updated.recent_civic_actions[0].event_type).toBe("civic.skill.github_publish");
  });
});
