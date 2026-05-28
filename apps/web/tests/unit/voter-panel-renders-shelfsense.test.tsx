// Direct render of LightweightVoterPanel against the EXACT production
// payload for the ShelfSense run (344d0f4f-...). If this passes, the
// panel renders correctly with real data and the production bug is
// NOT in the panel component itself.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";

import { LightweightVoterPanel } from "@/components/LightweightVoterPanel";
import type { LightweightVotersPayload } from "@/lib/types";

const PAYLOAD_PATH = path.resolve(
  __dirname,
  "..",
  "fixtures",
  "shelfsense-voter-payload.json",
);

describe("LightweightVoterPanel — real ShelfSense production payload", () => {
  const payload = JSON.parse(
    readFileSync(PAYLOAD_PATH, "utf8"),
  ) as LightweightVotersPayload;

  it("payload fixture is the real shape (sanity)", () => {
    expect(payload.voter_overlay_available).toBe(true);
    expect(payload.voters_count).toBe(100);
    expect(payload.final_distribution).toBeTruthy();
  });

  it("renders the panel against the real ShelfSense payload", () => {
    render(<LightweightVoterPanel payload={payload} />);
    expect(
      screen.getByTestId("lightweight-voter-panel"),
    ).toBeInTheDocument();
  });

  it("renders the four-bucket distribution chart against real data", () => {
    const { getAllByText } = render(<LightweightVoterPanel payload={payload} />);
    expect(
      screen.getByTestId("voter-distribution-chart"),
    ).toBeInTheDocument();
    // Phase 14B — bucket labels appear in BOTH the new voter-dot
    // graph legend AND the bar chart, so we assert "at least one"
    // per label instead of "exactly one".
    expect(getAllByText("Buyer").length).toBeGreaterThanOrEqual(1);
    expect(getAllByText("Receptive").length).toBeGreaterThanOrEqual(1);
    expect(getAllByText("Uncertain").length).toBeGreaterThanOrEqual(1);
    expect(getAllByText("Skeptical").length).toBeGreaterThanOrEqual(1);
  });

  it("does NOT crash when cluster_arguments is a dict (not {pro,con})", () => {
    // Production cluster_arguments shape is:
    //   { "cohort_label::stance": { top_objection, top_proof_need } }
    // NOT { pro: [...], con: [...] }. The panel should tolerate this.
    expect(() => {
      render(<LightweightVoterPanel payload={payload} />);
    }).not.toThrow();
  });

  it("renders the 'How the 100 voters work' copy block", () => {
    render(<LightweightVoterPanel payload={payload} />);
    expect(screen.getByTestId("how-voters-work")).toBeInTheDocument();
  });

  it("renders the influence dynamics expander toggle", () => {
    render(<LightweightVoterPanel payload={payload} />);
    expect(
      screen.getByTestId("voter-dynamics-toggle"),
    ).toBeInTheDocument();
  });
});
