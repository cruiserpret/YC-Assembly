import { describe, expect, it } from "vitest";

import {
  briefSchema,
  evidenceAnchorDetailSchema,
  simulationReportSchema,
} from "../../src/lib/schema";
import { buildSampleReport } from "../fixtures";

describe("briefSchema", () => {
  it("rejects a description shorter than 64 chars", () => {
    const r = briefSchema.safeParse({
      product_type: "ai_commerce_platform",
      product_name: "X",
      description: "too short",
      price_structure: { model: "subscription_monthly" },
      target_society: { description: "Some target society description here." },
      competitors: [{ name: "Magic" }],
    });
    expect(r.success).toBe(false);
  });
  it("rejects an empty competitor list", () => {
    const r = briefSchema.safeParse({
      product_type: "ai_commerce_platform",
      product_name: "Amboras",
      description: "x".repeat(80),
      price_structure: { model: "subscription_monthly" },
      target_society: { description: "Some target society description here." },
      competitors: [],
    });
    expect(r.success).toBe(false);
  });
  it("accepts a well-formed brief", () => {
    const r = briefSchema.safeParse({
      product_type: "ai_commerce_platform",
      product_name: "Amboras",
      description: "x".repeat(80),
      price_structure: { model: "subscription_monthly", amount: "$49" },
      target_society: { description: "Shopify merchants overwhelmed by plugins." },
      competitors: [{ name: "Shopify Magic" }],
    });
    expect(r.success).toBe(true);
  });
});

describe("simulationReportSchema", () => {
  it("round-trips a representative report fixture", () => {
    const r = simulationReportSchema.safeParse(buildSampleReport());
    if (!r.success) {
      // Surface the issue inline to make failures debuggable.
      throw new Error(JSON.stringify(r.error.issues, null, 2));
    }
    expect(r.data.evidence_anchor_details).toBeDefined();
    expect(Object.keys(r.data.evidence_anchor_details).length).toBeGreaterThan(0);
  });

  it("validates evidence_anchor_details entries individually", () => {
    const sample = buildSampleReport();
    const id = Object.keys(sample.evidence_anchor_details)[0];
    const detail = sample.evidence_anchor_details[id];
    const r = evidenceAnchorDetailSchema.safeParse(detail);
    expect(r.success).toBe(true);
  });
});
