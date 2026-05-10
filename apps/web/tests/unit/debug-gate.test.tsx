import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportShell } from "../../src/components/report/ReportShell";
import { buildSampleReport } from "../fixtures";

describe("DebugPanel gating", () => {
  // Reset modules between tests so each one re-evaluates DEBUG_MODE
  // against the current process.env.NEXT_PUBLIC_ASSEMBLY_DEBUG.
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_ASSEMBLY_DEBUG;
  });

  it("does NOT render the debug panel when the env flag is unset", () => {
    delete process.env.NEXT_PUBLIC_ASSEMBLY_DEBUG;
    render(<ReportShell report={buildSampleReport()} />);
    expect(screen.queryByText(/Debug · raw report payload/i)).toBeNull();
  });

  it("renders the debug panel when the env flag is true", async () => {
    process.env.NEXT_PUBLIC_ASSEMBLY_DEBUG = "true";
    // Re-import after env mutation so DEBUG_MODE picks up the new value.
    const mod = await import("../../src/components/report/ReportShell");
    render(<mod.ReportShell report={buildSampleReport()} />);
    expect(screen.getByText(/Debug · raw report payload/i)).toBeInTheDocument();
  });
});
