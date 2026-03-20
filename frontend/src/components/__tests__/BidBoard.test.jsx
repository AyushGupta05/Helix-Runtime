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
    strategy_family: "localized-fix",
    strategy_summary: "Patch only the failing calculator function.",
    score: 0.77,
    confidence: 0.82,
    risk: 0.18,
    cost: 0.11,
    estimated_runtime_seconds: 42,
    touched_files: ["calc.py"],
    token_usage: { input_tokens: 120, output_tokens: 44 },
    cost_usage: { usd: 0.02 },
    rejection_reason: null
  },
  {
    bid_id: "standby",
    task_id: "T2",
    role: "Quality",
    provider: "anthropic",
    lane: "bid_deep.anthropic",
    model_id: "claude-sonnet-4-5",
    strategy_family: "shared-helper",
    strategy_summary: "Refactor the helper and widen regression coverage.",
    score: 0.69,
    confidence: 0.75,
    risk: 0.28,
    cost: 0.22,
    estimated_runtime_seconds: 65,
    touched_files: ["calc.py", "tests/test_calc.py"],
    token_usage: { input_tokens: 90, output_tokens: 38 },
    cost_usage: { usd: 0.01 },
    rejection_reason: null
  },
  {
    bid_id: "rejected",
    task_id: "T2",
    role: "Fast",
    provider: "bedrock",
    lane: "bid_fast.bedrock",
    model_id: "nova",
    strategy_family: "fast-path",
    strategy_summary: "Race to a broad patch with more churn.",
    score: 0.41,
    confidence: 0.44,
    risk: 0.74,
    cost: 0.09,
    estimated_runtime_seconds: 30,
    touched_files: ["calc.py", "tests/test_calc.py", "helper.py"],
    token_usage: { input_tokens: 60, output_tokens: 25 },
    cost_usage: { usd: 0.03 },
    rejection_reason: "too much file churn"
  }
];

describe("BidBoard", () => {
  it("renders the bidding arena with prioritized bid statuses", () => {
    render(
      <BidBoard
        bids={bids}
        winnerBidId="winner"
        standbyBidId="standby"
        activeTaskId="T2"
        activePhase="market"
        activeBidRound={3}
        simulationRound={2}
        usageSummary={{
          active_task: {
            total_tokens: 377,
            total_cost: 0.06
          }
        }}
      />
    );

    expect(screen.getByText(/Live Bidding Arena/i)).toBeInTheDocument();
    expect(screen.getByText(/Round 3/i)).toBeInTheDocument();
    expect(screen.getAllByText("Openai").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Anthropic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bedrock").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Localized Fix").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Shared Helper").length).toBeGreaterThan(0);
    expect(screen.getByText("WINNER")).toBeInTheDocument();
    expect(screen.getByText("STANDBY")).toBeInTheDocument();
    expect(screen.getByText("REJECTED")).toBeInTheDocument();
  });
});
