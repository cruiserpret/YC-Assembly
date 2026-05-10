import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceAnchorList } from "../../src/components/report/EvidenceAnchorList";

describe("EvidenceAnchorList", () => {
  const id = "11111111-1111-1111-1111-111111111111";
  const details = {
    [id]: {
      evidence_id: id,
      kind: "direct",
      node_class: "trust_barrier",
      source_type: "user_input",
      source_url: null,
      source_excerpt: "Founders worry about brand identity",
      content_preview: "Founders worry about brand identity damage from autonomous AI.",
      captured_at: null,
    },
  };

  it("renders one pill per anchor", () => {
    render(<EvidenceAnchorList anchors={[id]} details={details} />);
    expect(screen.getByRole("button", { name: /direct · trust_barrier/i })).toBeInTheDocument();
  });

  it("opens a modal with hydrated detail on click", () => {
    render(<EvidenceAnchorList anchors={[id]} details={details} />);
    fireEvent.click(screen.getByRole("button", { name: /direct · trust_barrier/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/Source excerpt/i)).toBeInTheDocument();
    // The excerpt appears in source_excerpt AND content_preview by design
    // (excerpt is a substring of content). One match is enough.
    expect(
      screen.getAllByText(/Founders worry about brand identity/i).length,
    ).toBeGreaterThan(0);
  });

  it("shows an unhydrated fallback when detail is missing", () => {
    render(<EvidenceAnchorList anchors={[id]} details={{}} />);
    expect(screen.getByRole("button", { name: /unhydrated/i })).toBeInTheDocument();
  });
});
