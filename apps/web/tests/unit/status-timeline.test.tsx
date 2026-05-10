import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusTimeline } from "../../src/components/status/StatusTimeline";
import { FailedStateCard } from "../../src/components/status/FailedStateCard";

describe("StatusTimeline", () => {
  it("highlights the active stage when simulating", () => {
    render(
      <StatusTimeline
        status={{
          id: "sim",
          status: "simulating",
          progress: {
            stage: "simulating",
            current_round: "first_exposure",
            round_index: 2,
            agents_completed: 4,
            agents_total: 6,
          },
          failed_stage: null,
          error: null,
        }}
      />,
    );
    expect(screen.getByText(/Running the simulation/i)).toBeInTheDocument();
    expect(screen.getByText(/Round 2 of 7 — first exposure/i)).toBeInTheDocument();
    expect(screen.getByText(/4 \/ 6 agents/i)).toBeInTheDocument();
  });

  it("marks the failed stage when status=failed", () => {
    render(
      <StatusTimeline
        status={{
          id: "sim",
          status: "failed",
          progress: { stage: "aggregating" },
          failed_stage: "aggregating",
          error: { kind: "AggregationFailed", message: "claim_validator rejected 2 claims" },
        }}
      />,
    );
    const aggLine = screen.getByText(/Synthesising the report/i);
    expect(aggLine).toBeInTheDocument();
    expect(aggLine).toHaveClass("text-warn");
  });
});

describe("FailedStateCard", () => {
  it("renders failed_stage + kind + message in a readable block", () => {
    render(
      <FailedStateCard
        status={{
          id: "sim",
          status: "failed",
          failed_stage: "aggregating",
          error: { kind: "AggregationFailed", message: "claim_validator rejected 2 claims" },
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/aggregating/i);
    expect(screen.getByText(/AggregationFailed/i)).toBeInTheDocument();
    expect(screen.getByText(/claim_validator rejected 2 claims/i)).toBeInTheDocument();
  });
});
