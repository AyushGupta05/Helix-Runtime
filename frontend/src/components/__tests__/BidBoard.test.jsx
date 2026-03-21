import { render, screen } from "@testing-library/react";

import BidBoard from "../BidBoard";

const bids = [
  {
    bid_id: "winner",
    task_id: "T2",
    role: "Safe",
    provider: "openai",
    lane: "bid_deep.openai",
    model_id: "gpt-4.1",
    generation_mode: "provider_model",
    invocation_id: "inv-winner",
    invocation_kind: "bid_generation",
    strategy_family: "localized-fix",
    strategy_summary: "Patch only the failing calculator function.",
    score: 0.77,
    confidence: 0.82,
    risk: 0.18,
    cost: 0.11,
    estimated_runtime_seconds: 42,
    touched_files: ["calc.py"],
    required_skills: ["github_context"],
    optional_skills: ["knowledge_context"],
    governed_action_plan: { type: "github_context" },
    external_evidence_plan: { sources: ["ci"] },
    capability_reliance_score: 0.73,
    policy_friction_score: 0.14,
    revocation_risk_score: 0.08,
    governed_envelope: { status: "allowed" },
    token_usage: { input_tokens: 120, output_tokens: 44 },
    cost_usage: { usd: 0.02 },
    usage_unavailable_reason: null,
    rejection_reason: null
  },
  {
    bid_id: "standby",
    task_id: "T2",
    role: "Quality",
    provider: "anthropic",
    lane: "bid_deep.anthropic",
    model_id: "claude-sonnet-4-5",
    generation_mode: "mock",
    invocation_id: "inv-standby",
    invocation_kind: "bid_generation",
    strategy_family: "shared-helper",
    strategy_summary: "Refactor the helper and widen regression coverage.",
    score: 0.69,
    confidence: 0.75,
    risk: 0.28,
    cost: 0.22,
    estimated_runtime_seconds: 65,
    touched_files: ["calc.py", "tests/test_calc.py"],
    required_skills: ["github_context", "knowledge_context"],
    optional_skills: [],
    governed_action_plan: { type: "read_only" },
    external_evidence_plan: { sources: ["pr"] },
    capability_reliance_score: 0.54,
    policy_friction_score: 0.31,
    revocation_risk_score: 0.17,
    civic_preflight: { decision: "allowed" },
    token_usage: null,
    cost_usage: null,
    usage_unavailable_reason: "Mock strategy backend generated this proposal without a provider call.",
    rejection_reason: null
  },
  {
    bid_id: "rejected",
    task_id: "T2",
    role: "Fast",
    provider: "system",
    lane: "fallback.deterministic",
    model_id: null,
    generation_mode: "deterministic_fallback",
    strategy_family: "fast-path",
    strategy_summary: "Race to a broad patch with more churn.",
    score: 0.41,
    confidence: 0.44,
    risk: 0.74,
    cost: 0.09,
    estimated_runtime_seconds: 30,
    touched_files: ["calc.py", "tests/test_calc.py", "helper.py"],
    token_usage: null,
    cost_usage: null,
    usage_unavailable_reason: "Deterministic fallback market generated without a provider call.",
    rejection_reason: "too much file churn"
  }
];

describe("BidBoard", () => {
  it("renders the strategy market with prioritized bid statuses", () => {
    render(
      <BidBoard
        bids={bids}
        winnerBidId="winner"
        standbyBidId="standby"
        activeTaskId="T2"
        activePhase="market"
        activeBidRound={3}
        simulationRound={2}
        biddingState={{
          generation_mode: "deterministic_fallback",
          degraded: true,
          warning: "Provider lanes were unavailable."
        }}
        usageSummary={{
          mission: {
            total_tokens: 0,
            total_cost: 0
          },
          active_task: {
            total_tokens: 377,
            total_cost: 0.06
          }
        }}
        events={[
          {
            id: 7,
            event_type: "bid.generated",
            created_at: "2026-03-20T10:00:00Z",
            message: "Safe strategy generated.",
            payload: { task_id: "T2" }
          }
        ]}
      />
    );

    expect(screen.getByText(/Competing plans stay visible/i)).toBeInTheDocument();
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getByText(/Strategy notice/i)).toBeInTheDocument();
    expect(screen.getByText(/Provider lanes were unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/Safe strategy generated/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Openai/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Mock/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/System/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Required:/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Optional: knowledge_context/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Envelope: allowed/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Reliance 0\.73/i)).toBeInTheDocument();
    expect(screen.getByText(/Friction 0\.14/i)).toBeInTheDocument();
    expect(screen.getByText("LEADING")).toBeInTheDocument();
    expect(screen.getByText("STANDBY")).toBeInTheDocument();
    expect(screen.getByText("REJECTED")).toBeInTheDocument();
    expect(screen.getByText(/^Spend$/i)).toBeInTheDocument();
    expect(screen.getByText(/^Blocked$/i)).toBeInTheDocument();
  });

  it("keeps winner and standby context visible even when the active task changes", () => {
    render(
      <BidBoard
        bids={bids}
        winnerBidId="winner"
        standbyBidId="standby"
        activeTaskId="T3"
        activePhase="select"
        activeBidRound={4}
        simulationRound={3}
        biddingState={{ generation_mode: "provider_model", degraded: false }}
        usageSummary={{ mission: { total_tokens: 377, total_cost: 0.06 }, active_task: { total_tokens: 0, total_cost: 0 } }}
      />
    );

    expect(screen.getByText(/Competing plans stay visible/i)).toBeInTheDocument();
    expect(screen.getByText(/^T3$/)).toBeInTheDocument();
    expect(screen.getByText("LEADING")).toBeInTheDocument();
    expect(screen.getByText("STANDBY")).toBeInTheDocument();
  });
});
