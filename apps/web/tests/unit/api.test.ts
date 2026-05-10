import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getSimulationReport, postSimulation } from "../../src/lib/api";
import { buildSampleReport } from "../fixtures";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const validBrief = {
  product_type: "ai_commerce_platform",
  product_name: "Amboras",
  description: "x".repeat(80),
  price_structure: { model: "subscription_monthly", amount: "$49" },
  target_society: { description: "Shopify merchants overwhelmed by plugins." },
  competitors: [{ name: "Shopify Magic" }],
} as const;

describe("postSimulation", () => {
  it("returns SimulationCreated on 202", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "9bb2061b-c226-4c2e-a493-bdf894090ba1",
          status: "pending",
          created_at: "2026-05-02T22:36:22.000Z",
        }),
        { status: 202 },
      ),
    );
    const r = await postSimulation(validBrief as any);
    expect(r.id).toBe("9bb2061b-c226-4c2e-a493-bdf894090ba1");
  });

  it("throws ApiError on non-202", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { kind: "invalid_brief" } }), { status: 400 }),
    );
    await expect(postSimulation(validBrief as any)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("getSimulationReport", () => {
  it("returns ready+report on 200", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(buildSampleReport()), { status: 200 }),
    );
    const r = await getSimulationReport("9bb2061b-c226-4c2e-a493-bdf894090ba1");
    expect(r.kind).toBe("ready");
    if (r.kind === "ready") {
      expect(r.report.status).toBe("reported");
    }
  });

  it("returns report_not_ready on 409", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: {
            kind: "report_not_ready",
            current_status: "simulating",
            guidance: "poll /status",
          },
        }),
        { status: 409 },
      ),
    );
    const r = await getSimulationReport("9bb2061b-c226-4c2e-a493-bdf894090ba1");
    expect(r.kind).toBe("report_not_ready");
    if (r.kind === "report_not_ready") {
      expect(r.current_status).toBe("simulating");
    }
  });

  it("throws ApiError on 404", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 404 }));
    await expect(
      getSimulationReport("00000000-0000-0000-0000-000000000000"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
