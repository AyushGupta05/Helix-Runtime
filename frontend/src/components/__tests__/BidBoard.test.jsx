import { render, screen } from "@testing-library/react";

import BidBoard from "../BidBoard";

const bids = [
  {
    bid_id: "winner",
    task_id: "T2",
    role: "Safe",
    strategy_family: "localized-fix",
    strategy_summary: "Patch only the failing calculator function.",
    score: 0.77,
    risk: 0.18,
    cost: 0.11,
    estimated_runtime_seconds: 42,
    touched_files: ["calc.py"],
    rejection_reason: null
  },
  {
    bid_id: "standby",
    task_id: "T2",
    role: "Quality",
    strategy_family: "shared-helper",
    strategy_summary: "Refactor the helper and widen regression coverage.",
    score: 0.69,
    risk: 0.28,
    cost: 0.22,
    estimated_runtime_seconds: 65,
    touched_files: ["calc.py", "tests/test_calc.py"],
    rejection_reason: null
  }
];

describe("BidBoard", () => {
  it("highlights winner and standby contenders", () => {
    render(<BidBoard bids={bids} winnerBidId="winner" standbyBidId="standby" />);

    expect(screen.getByText("Winner")).toBeInTheDocument();
    expect(screen.getByText("Standby")).toBeInTheDocument();
    expect(screen.getAllByText("Safe").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Quality").length).toBeGreaterThan(0);
    expect(screen.getAllByText("localized-fix").length).toBeGreaterThan(0);
    expect(screen.getAllByText("shared-helper").length).toBeGreaterThan(0);
  });
});
