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
  }
];

describe("BidBoard", () => {
  it("renders compact strategy cards with winner and standby states", () => {
    render(
      <BidBoard
        bids={bids}
        winnerBidId="winner"
        standbyBidId="standby"
        activeTaskId="T2"
        providerMarketSummary={{
          families: {
            "localized-fix": [bids[0]],
            "shared-helper": [bids[1]]
          }
        }}
      />
    );

    expect(screen.getByText("Winner")).toBeInTheDocument();
    expect(screen.getByText("Standby")).toBeInTheDocument();
    expect(screen.getAllByText("Openai").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Anthropic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Localized Fix").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Shared Helper").length).toBeGreaterThan(0);
    expect(screen.getByText("WIN")).toBeInTheDocument();
    expect(screen.getByText("STBY")).toBeInTheDocument();
  });
});
