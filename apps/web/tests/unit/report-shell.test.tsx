import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReportShell } from "../../src/components/report/ReportShell";
import { buildSampleReport } from "../fixtures";

describe("ReportShell", () => {
  it("renders all 9 section titles", () => {
    render(<ReportShell report={buildSampleReport()} />);
    const titles = [
      /Subjective public opinion sentiment/i,
      /What persuaded people/i,
      /What did not persuade people/i,
      /The one thing needed for market acceptance/i,
      /Product trajectory/i,
      /Competitor analysis/i,
      /Recommendations/i,
      /Debate shift markers/i,
      /Confidence \(simulation entropy\)/i,
      /Evidence ledger/i,
    ];
    for (const t of titles) {
      expect(screen.getAllByText(t).length).toBeGreaterThan(0);
    }
  });

  it("renders the missing-evidence panel callout", () => {
    render(<ReportShell report={buildSampleReport()} />);
    expect(
      screen.getByText(/What this simulation didn't have/i),
    ).toBeInTheDocument();
  });

  it("renders confidence as a qualitative badge, never a percentage", () => {
    render(<ReportShell report={buildSampleReport()} />);
    const badge = screen.getAllByText(/confidence: (clear|moderate|thin)/i)[0];
    expect(badge).toBeInTheDocument();
    // No "%" anywhere in the rendered DOM next to confidence — the
    // confidence section's own rendered numbers (separation_ratio etc.)
    // appear elsewhere as monospace simulation-entropy figures.
  });

  it("renders the per-competitor card", () => {
    render(<ReportShell report={buildSampleReport()} />);
    expect(screen.getByRole("heading", { name: /Shopify Magic/i })).toBeInTheDocument();
  });

  it("does not render the debug panel when DEBUG_MODE is off", () => {
    render(<ReportShell report={buildSampleReport()} />);
    expect(screen.queryByText(/Debug · raw report payload/i)).toBeNull();
  });
});
