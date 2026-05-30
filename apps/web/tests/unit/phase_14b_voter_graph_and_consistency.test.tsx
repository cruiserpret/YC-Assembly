// Phase 14B — visual 100-voter graph + report consistency tests.
//
// Covers:
//   - VoterInfluenceGraph renders exactly N dots (100 by default)
//   - Bucket colors + legend totals + "debate agents talk; voters
//     absorb and spread" copy
//   - VoterInfluenceGraph shows visible unavailable state when
//     distribution is null
//   - Agent graph renamed to "Deep-agent debate graph"
//   - Physical-product objection buckets filtered on software briefs
//   - "7-round" wording replaced by dynamic round count
//   - Final-ballot vs persona-count divergence is surfaced in copy
//   - Safety: no Phase 13 / behavioral_mind_layer / ASSEMBLY_BEHAVIORAL
//     refs in source

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import React from "react";

import { AgentGraph } from "@/components/AgentGraph";
import { VoterInfluenceGraph } from "@/components/VoterInfluenceGraph";
import {
  filterApplicableObjectionBuckets,
  filterApplicableProofBuckets,
  isLikelyFreeProduct,
  isLikelySoftwareProduct,
  objectionSentence,
} from "@/lib/buckets";
import {
  DownloadReportButton,
  renderStructuredReport,
  type ReportContext,
} from "@/components/DownloadReportButton";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
  LightweightVotersPayload,
  VoterBucketDistribution,
} from "@/lib/types";

// Phase 14B TASK 5 — the download buttons refetch the voter overlay at
// click time via a dynamic import of "@/lib/api". Mock that module so the
// click-time refetch can be asserted without a real network call.
vi.mock("@/lib/api", () => ({
  getAssemblyLightweightVoters: vi.fn(),
}));
import { getAssemblyLightweightVoters } from "@/lib/api";


// =====================================================================
// VoterInfluenceGraph — the 100-dot SVG graph
// =====================================================================

describe("VoterInfluenceGraph — 100-voter dot graph", () => {
  const realDist: VoterBucketDistribution = {
    buyer: 0,
    receptive: 22.8,
    uncertain: 15,
    skeptical: 62.2,
    n_voters: 100,
  };

  it("renders exactly 100 voter dots when voterCount=100", () => {
    const { container } = render(
      <VoterInfluenceGraph distribution={realDist} voterCount={100} />,
    );
    const circles = container.querySelectorAll("svg circle");
    expect(circles.length).toBe(100);
  });

  it("uses the four bucket colors (testid markers per bucket)", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    // At least one dot per non-zero bucket
    expect(
      document.querySelectorAll('[data-testid="voter-dot-receptive"]').length,
    ).toBeGreaterThan(0);
    expect(
      document.querySelectorAll('[data-testid="voter-dot-uncertain"]').length,
    ).toBeGreaterThan(0);
    expect(
      document.querySelectorAll('[data-testid="voter-dot-skeptical"]').length,
    ).toBeGreaterThan(0);
  });

  it("dot counts per bucket sum to voterCount (no rounding drift)", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    const buyerN = document.querySelectorAll(
      '[data-testid="voter-dot-buyer"]',
    ).length;
    const receptiveN = document.querySelectorAll(
      '[data-testid="voter-dot-receptive"]',
    ).length;
    const uncertainN = document.querySelectorAll(
      '[data-testid="voter-dot-uncertain"]',
    ).length;
    const skepticalN = document.querySelectorAll(
      '[data-testid="voter-dot-skeptical"]',
    ).length;
    expect(buyerN + receptiveN + uncertainN + skepticalN).toBe(100);
  });

  it("includes the 'Debate agents talk' / 'absorb and spread' copy", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    expect(
      screen.getByText(/Debate agents talk\. 100 voters absorb and spread\./),
    ).toBeInTheDocument();
  });

  it("includes the 'not LLM debate agents' clarifier note", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    expect(
      screen.getByTestId("voter-graph-not-debate-agents-note"),
    ).toBeInTheDocument();
  });

  it("renders the legend with counts per bucket", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    expect(screen.getByTestId("voter-graph-legend")).toBeInTheDocument();
    expect(
      screen.getByTestId("voter-graph-legend-buyer"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("voter-graph-legend-receptive"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("voter-graph-legend-uncertain"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("voter-graph-legend-skeptical"),
    ).toBeInTheDocument();
  });

  it("shows the visible empty state when distribution is null", () => {
    render(<VoterInfluenceGraph distribution={null} voterCount={100} />);
    expect(screen.getByTestId("voter-graph-empty")).toBeInTheDocument();
  });

  it("renders the header copy 'colored by final bucket'", () => {
    render(<VoterInfluenceGraph distribution={realDist} voterCount={100} />);
    expect(
      screen.getByText(/100 voters · colored by final bucket/),
    ).toBeInTheDocument();
  });

  it("supports non-100 voter counts (e.g., 50 voters → 50 dots)", () => {
    const { container } = render(
      <VoterInfluenceGraph
        distribution={{
          buyer: 10, receptive: 30, uncertain: 40, skeptical: 20,
        }}
        voterCount={50}
      />,
    );
    expect(container.querySelectorAll("svg circle").length).toBe(50);
  });
});


// =====================================================================
// Deep-agent graph rename (TASK 3)
// =====================================================================

describe("AgentGraph rename — Phase 14B", () => {
  it("AgentGraph source no longer says 'Agent relationship graph'", () => {
    const src = readFileSync(
      path.resolve(
        __dirname, "..", "..", "src", "components", "AgentGraph.tsx",
      ),
      "utf8",
    );
    expect(src).toContain("Deep-agent debate graph");
    expect(src).toContain("debate agents · live particle flow");
    expect(src).toContain("These are the agents who generated the public debate");
    expect(src).not.toContain("Agent relationship graph");
  });
});


// =====================================================================
// Bucket filter — TASK 6D (physical-product objections on software)
// =====================================================================

describe("filterApplicableObjectionBuckets — Phase 14B", () => {
  it("isLikelySoftwareProduct detects software briefs", () => {
    expect(
      isLikelySoftwareProduct({
        product_name: "GraphNest AI",
        product_description: "Local-first AI knowledge base for engineers.",
      }),
    ).toBe(true);
    expect(
      isLikelySoftwareProduct({
        product_name: "Tasknory",
        product_description: "AI-assisted talent marketplace platform.",
      }),
    ).toBe(true);
  });

  it("isLikelySoftwareProduct does NOT match physical products", () => {
    expect(
      isLikelySoftwareProduct({
        product_name: "LumaLoop",
        product_description:
          "A rechargeable snap-on LED safety band for night runners.",
      }),
    ).toBe(false);
    expect(
      isLikelySoftwareProduct({
        product_name: "PantryPulse",
        product_description:
          "A smart kitchen device with a camera and NFC tags.",
      }),
    ).toBe(false);
  });

  it("drops low-score 'no_ip_rating_or_durability_proof' on a software brief", () => {
    const objections = [
      { bucket: "price_value_concern", weighted_score: 0.67 },
      { bucket: "no_ip_rating_or_durability_proof", weighted_score: 0.06 },
      { bucket: "competitor_already_solves", weighted_score: 0.06 },
    ];
    const filtered = filterApplicableObjectionBuckets(objections, {
      product_name: "GraphNest AI",
      product_description: "Local-first AI knowledge base.",
    });
    expect(filtered.find((o) => o.bucket === "no_ip_rating_or_durability_proof"))
      .toBeUndefined();
    expect(filtered.find((o) => o.bucket === "price_value_concern"))
      .toBeDefined();
  });

  it("KEEPS high-score durability objection even on software brief (real signal)", () => {
    const objections = [
      { bucket: "no_ip_rating_or_durability_proof", weighted_score: 0.42 },
    ];
    const filtered = filterApplicableObjectionBuckets(objections, {
      product_name: "GraphNest AI",
      product_description: "Local-first AI knowledge base.",
    });
    expect(filtered.length).toBe(1);
  });

  it("KEEPS low-score durability objection on a physical-product brief", () => {
    const objections = [
      { bucket: "no_ip_rating_or_durability_proof", weighted_score: 0.06 },
    ];
    const filtered = filterApplicableObjectionBuckets(objections, {
      product_name: "LumaLoop",
      product_description: "Rechargeable LED safety device for runners.",
    });
    expect(filtered.length).toBe(1);
  });

  it("filters battery and shipping buckets on software products too", () => {
    const objections = [
      { bucket: "battery_or_runtime_concern", weighted_score: 0.05 },
      { bucket: "shipping_or_availability", weighted_score: 0.04 },
      { bucket: "price_value_concern", weighted_score: 0.7 },
    ];
    const filtered = filterApplicableObjectionBuckets(objections, {
      product_name: "Tasknory",
      product_description: "AI-assisted talent marketplace platform.",
    });
    expect(filtered.length).toBe(1);
    expect(filtered[0].bucket).toBe("price_value_concern");
  });

  it("filterApplicableProofBuckets drops low-score physical proof on software briefs", () => {
    const proofs = [
      { bucket: "durability_test", weighted_score: 0.05 },
      { bucket: "third_party_review", weighted_score: 0.4 },
    ];
    const filtered = filterApplicableProofBuckets(proofs, {
      product_name: "GraphNest AI",
      product_description: "AI knowledge base SaaS.",
    });
    expect(filtered.find((p) => p.bucket === "durability_test")).toBeUndefined();
    expect(filtered.find((p) => p.bucket === "third_party_review")).toBeDefined();
  });
});


// =====================================================================
// Report consistency — TASK 6B "7-round" wording + 6C ballot count
// =====================================================================

function _emptyReport(productBrief?: Record<string, unknown>): FounderReport {
  return {
    schema_version: "v0.1",
    run_id: "abc",
    product_brief: productBrief ?? {},
    executive_summary: [],
    synthetic_society_size: 24,
    cohort_count: 4,
    synthetic_intent_snapshot: {
      intent_distribution: { would_try_once: 8 },
      switching_status_distribution: {},
      high_intent_segments_count: 0,
      rejection_segments_count: 0,
    },
    most_receptive_cohorts: [],
    most_resistant_cohorts: [],
    loyal_to_alternative_patterns: [],
    top_objections: [],
    proof_needed: [],
    persuasion_levers: [],
    competitor_or_alternative_comparison: [],
    society_wide_debate_summary: {
      argument_count: 0,
      propagation_count: 0,
      argument_type_distribution: {},
      response_type_distribution: {},
    },
    arguments_that_spread: [],
    arguments_that_were_resisted: [],
    public_private_shift_summary: {
      pre_stance_distribution: {},
      final_stance_distribution: {},
    },
    recommended_next_tests: [],
    confidence_dimensions: {},
    caveats: [],
    evidence_traceability_summary: {},
  };
}

function _transcriptWithRounds(numRounds: number): DiscussionTranscriptPayload {
  return {
    run_id: "abc",
    discussion_session_id: null,
    groups: [
      {
        group_index: 0,
        personas: [
          { persona_id: "p1", display_name: "P1", role: "ops_lead" },
        ],
        rounds: Array.from({ length: numRounds }, (_, i) => ({
          round_number: i + 1,
          round_label: `round_${i + 1}`,
          turns: [{
            turn_id: `t${i}`,
            turn_number: 1,
            speaker_persona_id: "p1",
            speaker_name: "P1",
            speaker_role: "ops_lead",
            turn_type: "public",
            stance: null,
            public_text: `Round ${i + 1} text`,
            referenced_turn_ids: [],
          }],
        })),
      },
    ],
    private_ballots: {},
  };
}

describe("Report 'N-round discussion' copy reflects actual round count (Phase 14B)", () => {
  it("HTML report says 4-round when transcript has 4 rounds (not '7-round')", () => {
    const ctx: ReportContext = {
      runId: "abc",
      productName: "Test",
      report: _emptyReport(),
      intent: { run_id: "abc", intent_distribution: {} },
      cohorts: null,
      personas: null,
      discussion: {
        run_id: "abc",
        persona_count: 24,
        group_count: 1,
        public_turn_count: 12,
        ballot_count_by_stage: { pre: 24, final: 24 },
      },
      transcript: _transcriptWithRounds(4),
      voters: null,
    };
    const html = renderStructuredReport(ctx);
    expect(html).not.toMatch(/Synthetic 7-round/);
    expect(html).toMatch(/Synthetic 4-round/);
  });

  it("HTML report says 5-round when transcript has 5 rounds", () => {
    const ctx: ReportContext = {
      runId: "abc",
      productName: "Test",
      report: _emptyReport(),
      intent: { run_id: "abc", intent_distribution: {} },
      cohorts: null,
      personas: null,
      discussion: {
        run_id: "abc",
        persona_count: 24,
        group_count: 1,
        public_turn_count: 12,
        ballot_count_by_stage: { pre: 24, final: 24 },
      },
      transcript: _transcriptWithRounds(5),
      voters: null,
    };
    const html = renderStructuredReport(ctx);
    expect(html).toMatch(/Synthetic 5-round/);
  });

  it("RunProgress copy no longer hardcodes 7-round", () => {
    const src = readFileSync(
      path.resolve(__dirname, "..", "..", "src", "components", "RunProgress.tsx"),
      "utf8",
    );
    expect(src).not.toMatch(/7-round/);
    expect(src).toMatch(/multi-round/);
  });

  it("PDF source no longer hardcodes 'Synthetic 7-round'", () => {
    const src = readFileSync(
      path.resolve(__dirname, "..", "..", "src", "components", "PdfReportDocument.tsx"),
      "utf8",
    );
    expect(src).not.toMatch(/Synthetic 7-round/);
  });
});


describe("Final-ballot vs persona-count honesty (Phase 14B TASK 6C)", () => {
  it("HTML report shows a caption when final ballots < persona count", () => {
    const ctx: ReportContext = {
      runId: "abc",
      productName: "Test",
      report: _emptyReport(),
      intent: { run_id: "abc", intent_distribution: {} },
      cohorts: null,
      personas: null,
      discussion: {
        run_id: "abc",
        persona_count: 24,
        group_count: 1,
        public_turn_count: 12,
        ballot_count_by_stage: { pre: 24, final: 19 },
      },
      transcript: _transcriptWithRounds(4),
      voters: null,
    };
    const html = renderStructuredReport(ctx);
    expect(html).toMatch(/5 of 24 personas did not complete a final ballot/);
  });

  it("HTML report does NOT show the divergence caption when counts match", () => {
    const ctx: ReportContext = {
      runId: "abc",
      productName: "Test",
      report: _emptyReport(),
      intent: { run_id: "abc", intent_distribution: {} },
      cohorts: null,
      personas: null,
      discussion: {
        run_id: "abc",
        persona_count: 24,
        group_count: 1,
        public_turn_count: 12,
        ballot_count_by_stage: { pre: 24, final: 24 },
      },
      transcript: _transcriptWithRounds(4),
      voters: null,
    };
    const html = renderStructuredReport(ctx);
    expect(html).not.toMatch(/did not complete a final ballot/);
  });
});


// =====================================================================
// Deep-agent requested-vs-actual explainer — TASK 6A (Phase 14B)
// =====================================================================

function _transcriptWithAgents(
  groupCount: number,
  perGroup: number,
): DiscussionTranscriptPayload {
  return {
    run_id: "abc",
    discussion_session_id: null,
    groups: Array.from({ length: groupCount }, (_, gi) => ({
      group_index: gi,
      personas: Array.from({ length: perGroup }, (_, pi) => ({
        persona_id: `g${gi}p${pi}`,
        display_name: `G${gi}P${pi}`,
        role: "ops_lead",
      })),
      rounds: [],
    })),
    private_ballots: {},
  };
}

describe("Deep-agent requested-vs-actual explainer (Phase 14B)", () => {
  it("shows the explainer when the requested count differs from the actual count", () => {
    render(
      <AgentGraph
        transcript={_transcriptWithAgents(4, 6)}
        requestedAgentCount={25}
      />,
    );
    const explainer = screen.getByTestId("deep-agent-count-explainer");
    const text = (explainer.textContent ?? "").replace(/\s+/g, " ");
    expect(text).toMatch(/You asked for 25 debate agents/);
    expect(text).toMatch(/debated with 24 agents across 4 discussion groups/);
    // Must NOT assert a (wrong) causal rule for the gap.
    expect(text).not.toMatch(/balanced/);
  });

  it("does NOT show the explainer when requested equals actual", () => {
    render(
      <AgentGraph
        transcript={_transcriptWithAgents(4, 6)}
        requestedAgentCount={24}
      />,
    );
    expect(screen.queryByTestId("deep-agent-count-explainer")).toBeNull();
  });

  it("does NOT show the explainer when no requested count is supplied", () => {
    render(<AgentGraph transcript={_transcriptWithAgents(4, 6)} />);
    expect(screen.queryByTestId("deep-agent-count-explainer")).toBeNull();
  });
});


// =====================================================================
// Free / open-source price-objection reframing — TASK 6E (Phase 14B)
// =====================================================================

describe("Free / open-source price-objection reframing (Phase 14B)", () => {
  it("isLikelyFreeProduct detects free + open-source briefs", () => {
    expect(isLikelyFreeProduct({ pricing_assumptions: { model: "free" } })).toBe(
      true,
    );
    expect(
      isLikelyFreeProduct({ product_description: "Free and open-source CLI." }),
    ).toBe(true);
    expect(
      isLikelyFreeProduct({ price_or_price_structure: "Free, MIT-licensed." }),
    ).toBe(true);
    expect(isLikelyFreeProduct({ price_structure: { amount: "$0" } })).toBe(true);
    // open-source WITH paid support (but no explicit sticker price) is
    // still free — the price objection is about adoption cost.
    expect(
      isLikelyFreeProduct({
        product_description: "Open-source core with paid enterprise support.",
        pricing_assumptions: { model: "enterprise_contract" },
      }),
    ).toBe(true);
  });

  it("isLikelyFreeProduct does NOT match paid or freemium-with-paid-tier briefs", () => {
    expect(
      isLikelyFreeProduct({
        price_structure: { model: "subscription", amount: "$49/mo" },
      }),
    ).toBe(false);
    // freemium implies a real paid tier — a "$0 tier" alone is not "free"
    expect(
      isLikelyFreeProduct({
        pricing_assumptions: { model: "freemium" },
        product_description: "$0 starter tier, $49 pro tier.",
      }),
    ).toBe(false);
    // "free trial" / "risk-free" / "free shipping" must not false-positive
    expect(
      isLikelyFreeProduct({
        product_description: "14-day free trial then $20/mo.",
      }),
    ).toBe(false);
    expect(
      isLikelyFreeProduct({
        product_description: "A risk-free, money-back guarantee. $99/yr.",
      }),
    ).toBe(false);
    expect(
      isLikelyFreeProduct({
        product_description: "Free shipping on orders over $50.",
      }),
    ).toBe(false);
    // a PAID product that merely name-drops open source is NOT free
    expect(
      isLikelyFreeProduct({
        product_description: "Paid SaaS built on open-source libraries.",
        price_structure: { model: "subscription", amount: "$99/mo" },
      }),
    ).toBe(false);
    expect(isLikelyFreeProduct(null)).toBe(false);
    expect(isLikelyFreeProduct({})).toBe(false);
  });

  it("objectionSentence reframes the price objection for a free product", () => {
    const freeBrief = {
      pricing_assumptions: { model: "free" },
      product_description: "Free, open-source AI tool.",
    };
    const sentence = objectionSentence("price_value_concern", freeBrief);
    expect(sentence).toMatch(/setup time/i);
    expect(sentence).not.toMatch(/price was justified/i);
  });

  it("objectionSentence keeps generic price copy for a paid product", () => {
    const paidBrief = { price_structure: { model: "subscription", amount: "$49/mo" } };
    expect(objectionSentence("price_value_concern", paidBrief)).toMatch(
      /price was justified/i,
    );
  });

  it("objectionSentence is back-compat with no brief and never reframes non-price buckets", () => {
    expect(objectionSentence("price_value_concern")).toMatch(
      /price was justified/i,
    );
    const freeBrief = { pricing_assumptions: { model: "free" } };
    expect(objectionSentence("trust_or_review_gap", freeBrief)).toMatch(
      /independent reviews/i,
    );
  });

  it("a free product's HTML report reframes the price objection (no 'price was justified')", () => {
    const report: FounderReport = {
      ..._emptyReport({
        pricing_assumptions: { model: "free" },
        product_name: "GraphNest AI",
        product_description: "Free, open-source local AI knowledge base.",
      }),
      top_objections: [
        { bucket: "price_value_concern", weighted_score: 0.8 },
      ] as unknown as FounderReport["top_objections"],
    };
    const ctx: ReportContext = {
      runId: "abc",
      productName: "GraphNest AI",
      report,
      intent: { run_id: "abc", intent_distribution: {} },
      cohorts: null,
      personas: null,
      discussion: null,
      transcript: _transcriptWithRounds(4),
      voters: null,
    };
    const html = renderStructuredReport(ctx).replace(/\s+/g, " ");
    // The objections section must actually be present (so an emptied
    // section can't masquerade as a successful reframe).
    expect(html).toMatch(/What this society pushed back on/);
    expect(html).toMatch(/setup time/i);
    expect(html).not.toMatch(/price was justified/i);
  });
});


// =====================================================================
// Download buttons refetch the voter overlay at click time — TASK 5
// =====================================================================

function _availableVoters(): LightweightVotersPayload {
  return {
    run_id: "abc",
    voter_overlay_available: true,
    voters_count: 100,
    final_distribution: {
      buyer: 12,
      receptive: 28,
      uncertain: 42,
      skeptical: 18,
      n_voters: 100,
    },
    influence_rounds: [],
    cluster_arguments: { pro: [], con: [] },
    samples: [],
    source_notes: {},
  } as unknown as LightweightVotersPayload;
}

let _origCreateObjectURL: typeof URL.createObjectURL | undefined;
let _origRevokeObjectURL: typeof URL.revokeObjectURL | undefined;

function _stubObjectURL(): void {
  _origCreateObjectURL = URL.createObjectURL;
  _origRevokeObjectURL = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(
    () => "blob:mock",
  ) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
}

// Keep the URL global stub + the api mock self-contained so they cannot
// leak into later tests in this file.
afterEach(() => {
  URL.createObjectURL = _origCreateObjectURL as typeof URL.createObjectURL;
  URL.revokeObjectURL = _origRevokeObjectURL as typeof URL.revokeObjectURL;
  vi.mocked(getAssemblyLightweightVoters).mockReset();
});

describe("DownloadReportButton — click-time voter refetch (Phase 14B TASK 5)", () => {
  it("refetches the voter overlay at click time when the voters prop is null", async () => {
    const mock = vi.mocked(getAssemblyLightweightVoters);
    mock.mockReset();
    mock.mockResolvedValue(_availableVoters());
    _stubObjectURL();

    render(
      <DownloadReportButton
        runId="abc-1234"
        report={_emptyReport()}
        transcript={_transcriptWithRounds(4)}
        voters={null}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(mock).toHaveBeenCalledWith("abc-1234"),
    );
  });

  it("refetches when a non-null payload reports voter_overlay_available=false", async () => {
    // The exact ShelfSense-AI race: a payload arrived but the overlay is
    // not yet available. The guard must refetch on the availability flag,
    // not just on nullness.
    const mock = vi.mocked(getAssemblyLightweightVoters);
    mock.mockReset();
    mock.mockResolvedValue(_availableVoters());
    _stubObjectURL();

    render(
      <DownloadReportButton
        runId="abc-1234"
        report={_emptyReport()}
        transcript={_transcriptWithRounds(4)}
        voters={
          {
            run_id: "abc",
            voter_overlay_available: false,
          } as LightweightVotersPayload
        }
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(mock).toHaveBeenCalledWith("abc-1234"));
  });

  it("does NOT refetch when an available voter payload is already provided", async () => {
    const mock = vi.mocked(getAssemblyLightweightVoters);
    mock.mockReset();
    _stubObjectURL();

    render(
      <DownloadReportButton
        runId="abc-1234"
        report={_emptyReport()}
        transcript={_transcriptWithRounds(4)}
        voters={_availableVoters()}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    await Promise.resolve();
    expect(mock).not.toHaveBeenCalled();
  });
});


// =====================================================================
// Coverage backfill — durability/weather copy + exact graph title
// =====================================================================

describe("Phase 14B — coverage backfill", () => {
  it("durability objection copy covers weather-resistance and is dropped on software", () => {
    expect(objectionSentence("no_ip_rating_or_durability_proof")).toMatch(
      /weather-resistance/i,
    );
    const filtered = filterApplicableObjectionBuckets(
      [{ bucket: "no_ip_rating_or_durability_proof", weighted_score: 0.05 }],
      {
        product_name: "GraphNest AI",
        product_description: "AI knowledge base SaaS.",
      },
    );
    expect(filtered.length).toBe(0);
  });

  it("VoterInfluenceGraph renders the exact '100-voter influence graph' title", () => {
    render(
      <VoterInfluenceGraph
        distribution={{
          buyer: 10,
          receptive: 20,
          uncertain: 30,
          skeptical: 40,
          n_voters: 100,
        }}
        voterCount={100}
      />,
    );
    expect(screen.getByText("100-voter influence graph")).toBeInTheDocument();
  });
});


// =====================================================================
// Anti-Phase-13 + safety
// =====================================================================

describe("Phase 14B — safety", () => {
  function _allWebSourceFiles(): string[] {
    const root = path.resolve(__dirname, "..", "..", "src");
    const out: string[] = [];
    const visit = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) visit(p);
        else if (
          p.endsWith(".ts") || p.endsWith(".tsx") || p.endsWith(".js")
        ) out.push(p);
      }
    };
    visit(root);
    return out;
  }

  it("no web source file references behavioral_mind_layer", () => {
    for (const p of _allWebSourceFiles()) {
      const src = readFileSync(p, "utf8");
      expect(src.toLowerCase()).not.toContain("behavioral_mind_layer");
    }
  });

  it("no web source file references ASSEMBLY_BEHAVIORAL", () => {
    for (const p of _allWebSourceFiles()) {
      const src = readFileSync(p, "utf8");
      expect(src.toLowerCase()).not.toContain("assembly_behavioral");
    }
  });
});
