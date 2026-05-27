// Phase 14A — 100-voter influence layer frontend tests.
//
// Covers:
//   - LightweightVoterPanel renders final distribution + counts
//   - "How the 100 voters work" copy block is visible by default
//   - missing voter artifact hides the panel without crashing
//   - Society Composition shows "24 debate agents + 100 voters"
//   - brief form copy says "Debate agents" + "100-voter overlay"
//   - HTML downloaded report includes voter section
//   - PDF downloaded report includes voter section
//   - PDF does NOT claim "100 debate agents"
//   - no Phase 13 behavioral_mind_layer references anywhere

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { LightweightVoterPanel } from "@/components/LightweightVoterPanel";
import { PersonaList } from "@/components/PersonaList";
import {
  renderStructuredReport,
  type ReportContext,
} from "@/components/DownloadReportButton";
import { PdfReportDocument } from "@/components/PdfReportDocument";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
  LightweightVotersPayload,
  PersonasPayload,
} from "@/lib/types";


function withQueryClient(ui: React.ReactElement): React.ReactElement {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

function makeVotersPayload(
  overrides: Partial<LightweightVotersPayload> = {},
): LightweightVotersPayload {
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
    calibrated_distribution: {
      distribution_percent: {
        buyer: 11, receptive: 27, uncertain: 44, skeptical: 18,
      },
      confidence_band_pp: 8,
      used_prior_correction: false,
      blend_weights: { rich_24: 0.4, voter_100: 0.6 },
      calibration_warnings: [],
    },
    influence_rounds: [
      {
        round_idx: 0,
        round_type: "initial",
        voters_affected: 0,
        intent_changes: 0,
        bucket_changes: 0,
        bucket_distribution: { buyer: 10, receptive: 25, uncertain: 45, skeptical: 20 },
        skeptic_transitions: {},
      },
      {
        round_idx: 1,
        round_type: "influence",
        voters_affected: 14,
        intent_changes: 7,
        bucket_changes: 5,
        bucket_distribution: { buyer: 11, receptive: 26, uncertain: 44, skeptical: 19 },
        skeptic_transitions: {},
      },
      {
        round_idx: 2,
        round_type: "influence",
        voters_affected: 9,
        intent_changes: 6,
        bucket_changes: 4,
        bucket_distribution: { buyer: 12, receptive: 27, uncertain: 43, skeptical: 18 },
        skeptic_transitions: {},
      },
      {
        round_idx: 3,
        round_type: "influence",
        voters_affected: 5,
        intent_changes: 3,
        bucket_changes: 2,
        bucket_distribution: { buyer: 12, receptive: 28, uncertain: 42, skeptical: 18 },
        skeptic_transitions: {},
      },
    ],
    cluster_arguments: {
      pro: ["The price is fair compared to alternatives"],
      con: ["Switching cost is too high"],
    },
    diversity_health: {
      n_voters: 100,
      n_cohorts_represented: 6,
      n_segments_represented: 6,
      n_roles_represented: 12,
      max_role_concentration: 0.21,
      competitor_user_share: 0.42,
    },
    samples: [],
    source_notes: {},
    ...overrides,
  };
}


// ============================================================
// LightweightVoterPanel — component-level rendering
// ============================================================

describe("LightweightVoterPanel — primary panel", () => {
  it("renders the final distribution buckets with counts", () => {
    render(<LightweightVoterPanel payload={makeVotersPayload()} />);
    // Panel title contains the voter count
    expect(
      screen.getByTestId("lightweight-voter-panel"),
    ).toBeInTheDocument();
    expect(screen.getByText(/100-voter influence layer/)).toBeInTheDocument();
    // All four buckets render
    expect(screen.getByText("Buyer")).toBeInTheDocument();
    expect(screen.getByText("Receptive")).toBeInTheDocument();
    expect(screen.getByText("Uncertain")).toBeInTheDocument();
    expect(screen.getByText("Skeptical")).toBeInTheDocument();
    // Counts derived from percentages × 100 voters
    expect(screen.getByText(/12\/100/)).toBeInTheDocument(); // buyer
    expect(screen.getByText(/28\/100/)).toBeInTheDocument(); // receptive
    expect(screen.getByText(/42\/100/)).toBeInTheDocument(); // uncertain
    expect(screen.getByText(/18\/100/)).toBeInTheDocument(); // skeptical
  });

  it("renders the 'How the 100 voters work' copy block visible by default", () => {
    render(<LightweightVoterPanel payload={makeVotersPayload()} />);
    const expl = screen.getByTestId("how-voters-work");
    expect(expl).toBeInTheDocument();
    // The details element is open by default
    expect(expl.hasAttribute("open")).toBe(true);
    // The key honesty phrase is present
    expect(
      screen.getByText(/debate agents talk; voters absorb and spread\./i),
    ).toBeInTheDocument();
    // The explanation states voters do not write new messages
    const text = expl.textContent ?? "";
    expect(text).toMatch(/do not write new messages/i);
    // Should NOT use any of the "voters talk" framings
    expect(text).not.toMatch(/the 100 voters speak/i);
    expect(text).not.toMatch(/100 voters argue/i);
  });

  it("shows the visible unavailable notice (not silent null) when voter_overlay_available is false", () => {
    // Production-bug fix: the panel must NEVER silently hide. When
    // the voter artifact is missing, the user must see a clear
    // unavailable state.
    render(
      <LightweightVoterPanel
        payload={{
          run_id: "abc",
          voter_overlay_available: false,
          reason: "lightweight_voters.json not on disk",
        }}
      />,
    );
    const shell = screen.getByTestId("lightweight-voter-panel-unavailable");
    expect(shell).toBeInTheDocument();
    // Eyebrow + title both mention "100-voter influence layer".
    expect(shell.textContent).toMatch(/100-voter influence layer/i);
    expect(shell.textContent).toMatch(
      /100-voter influence layer unavailable for this run/i,
    );
    // The API-supplied reason is surfaced for diagnosability
    expect(
      screen.getByText(/lightweight_voters\.json not on disk/),
    ).toBeInTheDocument();
  });

  it("shows the visible unavailable notice when payload is null", () => {
    render(<LightweightVoterPanel payload={null} />);
    expect(
      screen.getByTestId("lightweight-voter-panel-unavailable"),
    ).toBeInTheDocument();
  });

  it("shows the visible error notice when fetchError is provided", () => {
    render(
      <LightweightVoterPanel
        payload={null}
        fetchError={new Error("Network error: 500")}
      />,
    );
    expect(
      screen.getByTestId("lightweight-voter-panel-error"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Voter overlay could not be loaded/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Network error: 500/),
    ).toBeInTheDocument();
  });

  it("shows the visible loading state (with the panel section emitted)", () => {
    render(
      <LightweightVoterPanel payload={undefined} isLoading={true} />,
    );
    expect(
      screen.getByTestId("lightweight-voter-panel-loading"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Loading 100-voter overlay/i),
    ).toBeInTheDocument();
  });

  it("renders the influence-dynamics toggle when rounds are present", () => {
    render(<LightweightVoterPanel payload={makeVotersPayload()} />);
    expect(
      screen.getByTestId("voter-dynamics-toggle"),
    ).toBeInTheDocument();
  });

  it("shows the cluster-argument highlights when present", () => {
    render(<LightweightVoterPanel payload={makeVotersPayload()} />);
    expect(
      screen.getByText(/Strongest spreading arguments/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Most resisted arguments/i),
    ).toBeInTheDocument();
  });
});


// ============================================================
// PersonaList — Society Composition card update
// ============================================================

describe("PersonaList — Society Composition surface", () => {
  it("shows 'debate agents + 100 voters' in the collapsible summary", () => {
    render(
      withQueryClient(
        <PersonaList
          personas={{ run_id: "abc", persona_count: 24 }}
        />,
      ),
    );
    const list = screen.getByTestId("persona-list");
    // Open the details so inner content is visible
    list.setAttribute("open", "true");
    // The text appears in both the summary (collapsed-state subtitle)
    // and the open-state "society model" line. Both occurrences are
    // intentional; assert there's at least one match.
    const matches = within(list).getAllByText(
      /24 debate agents \+ 100 voters/,
    );
    expect(matches.length).toBeGreaterThanOrEqual(1);
    // The dedicated society-model summary line is uniquely test-id'd
    expect(
      within(list).getByTestId("society-model-summary"),
    ).toBeInTheDocument();
  });

  it("renders both 'Deep debate agents' and 'Voter overlay' cards", () => {
    render(
      withQueryClient(
        <PersonaList
          personas={{ run_id: "abc", persona_count: 24 }}
        />,
      ),
    );
    const list = screen.getByTestId("persona-list");
    list.setAttribute("open", "true");
    expect(screen.getByTestId("society-layer-deep")).toBeInTheDocument();
    expect(screen.getByTestId("society-layer-voters")).toBeInTheDocument();
    // Voter overlay card always says 100
    const voterCard = screen.getByTestId("society-layer-voters");
    expect(within(voterCard).getByText("100")).toBeInTheDocument();
  });

  it("does NOT claim '100 debate agents' or '100 LLM agents'", () => {
    render(
      withQueryClient(
        <PersonaList
          personas={{ run_id: "abc", persona_count: 24 }}
        />,
      ),
    );
    const list = screen.getByTestId("persona-list");
    list.setAttribute("open", "true");
    const text = list.textContent ?? "";
    expect(text).not.toMatch(/100 debate agents/i);
    expect(text).not.toMatch(/100 LLM agents/i);
    expect(text).not.toMatch(/124 (LLM )?agents/i);
  });

  it("includes the 'debate agents talk; voters absorb and spread' framing", () => {
    render(
      withQueryClient(
        <PersonaList
          personas={{ run_id: "abc", persona_count: 24 }}
        />,
      ),
    );
    const list = screen.getByTestId("persona-list");
    list.setAttribute("open", "true");
    expect(
      within(list).getByText(/Debate agents talk; voters absorb and spread\./i),
    ).toBeInTheDocument();
  });
});


// ============================================================
// Brief form — copy update
// ============================================================

describe("Brief form copy — Phase 14A", () => {
  it("labels the slider 'Debate agents' (not just 'Number of agents')", () => {
    const src = readFileSync(
      path.resolve(__dirname, "..", "..", "src", "components", "BriefForm.tsx"),
      "utf8",
    );
    expect(src).toContain('label="Debate agents (21-30, optional)"');
    expect(src).toContain("Every simulation also includes a 100-voter influence overlay");
    expect(src).toContain("100 voters always run after the debate");
  });
});


// ============================================================
// Downloaded HTML report — voter section
// ============================================================

function _emptyFounderReport(): FounderReport {
  return {
    schema_version: "v0.1",
    run_id: "abc",
    product_brief: {},
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

function _emptyTranscript(): DiscussionTranscriptPayload {
  return {
    run_id: "abc",
    discussion_session_id: null,
    groups: [],
    private_ballots: {},
  };
}

function _ctx(
  voters: LightweightVotersPayload | null,
): ReportContext {
  return {
    runId: "abc-1234",
    productName: "TestProduct",
    report: _emptyFounderReport(),
    intent: { run_id: "abc", intent_distribution: { would_try_once: 8 } },
    cohorts: null,
    personas: null,
    discussion: null,
    transcript: _emptyTranscript(),
    voters,
  };
}

describe("Downloaded HTML report — voter section", () => {
  it("includes the voter section when payload is available", () => {
    const html = renderStructuredReport(_ctx(makeVotersPayload()));
    // The actual section emission — distinct from the CSS comment
    expect(html).toContain('<section class="voter-panel">');
    // The "debate agents talk; voters absorb and spread" line is
    // broken across lines in the rendered HTML; normalize whitespace
    // before substring-checking.
    const norm = html.replace(/\s+/g, " ");
    expect(norm).toMatch(/debate agents talk; voters absorb and spread/i);
  });

  it("renders all four buckets in the HTML report", () => {
    const html = renderStructuredReport(_ctx(makeVotersPayload()));
    expect(html).toContain("Buyer");
    expect(html).toContain("Receptive");
    expect(html).toContain("Uncertain");
    expect(html).toContain("Skeptical");
  });

  it("includes the 4-round influence dynamics table", () => {
    const html = renderStructuredReport(_ctx(makeVotersPayload()));
    expect(html).toContain("Influence dynamics across 4 rounds");
    expect(html).toContain("Round 0");
    expect(html).toContain("Round 1");
    expect(html).toContain("Round 2");
    expect(html).toContain("Round 3");
  });

  it("HTML emits a visible voter-panel-unavailable notice when payload is null", () => {
    const html = renderStructuredReport(_ctx(null));
    // We MUST emit a section so the user sees that the feature
    // exists, just isn't populated. (This is the ShelfSense AI
    // bug fix: previously the renderer returned empty string and
    // the report shipped CSS with no matching section.)
    expect(html).toContain('class="voter-panel voter-panel-unavailable"');
    // Whitespace in HTML output varies; normalize before substring match.
    const norm = html.replace(/\s+/g, " ");
    expect(norm).toMatch(/not available in this downloaded report/i);
    // Real distribution content must NOT appear (no fake data)
    expect(html).not.toContain("Influence dynamics across 4 rounds");
  });

  it("HTML emits the unavailable notice with API reason when voter_overlay_available is false", () => {
    const html = renderStructuredReport(
      _ctx({
        run_id: "abc",
        voter_overlay_available: false,
        reason: "lightweight_voters.json not on disk",
      }),
    );
    expect(html).toContain('class="voter-panel voter-panel-unavailable"');
    expect(html).toContain("lightweight_voters.json not on disk");
  });

  it("does not call voters '100 debate agents' in the HTML", () => {
    const html = renderStructuredReport(_ctx(makeVotersPayload()));
    expect(html).not.toMatch(/100 debate agents/i);
    expect(html).not.toMatch(/100 LLM agents/i);
    const norm = html.replace(/\s+/g, " ");
    expect(norm).toMatch(/no new LLM calls per voter/i);
  });
});


// ============================================================
// Downloaded PDF report — voter section
// ============================================================

function _collectPdfText(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node))
    return node.map(_collectPdfText).filter(Boolean).join(" ");
  const el = node as React.ReactElement<{ children?: React.ReactNode }>;
  if (!el || typeof el !== "object" || !("type" in el)) return "";
  return _collectPdfText(el.props?.children);
}

function _collectPdfDocText(
  voters: LightweightVotersPayload | null,
): string {
  const props = {
    runId: "abc-1234",
    productName: "TestProduct",
    report: _emptyFounderReport(),
    intent: null,
    cohorts: null,
    personas: null,
    discussion: null,
    transcript: _emptyTranscript(),
    voters,
    generatedAt: "2026-05-27 17:00 UTC",
  };
  const rendered = (
    PdfReportDocument as unknown as (
      props: Parameters<typeof PdfReportDocument>[0],
    ) => React.ReactElement
  )(props);
  return _collectPdfText(rendered).replace(/\s+/g, " ");
}

describe("Downloaded PDF report — voter section", () => {
  it("includes the voter section in the PDF document tree when payload present", () => {
    const text = _collectPdfDocText(makeVotersPayload());
    expect(text).toContain("100-voter influence layer");
    expect(text).toContain("debate agents talk; voters absorb and spread");
  });

  it("renders all four bucket labels in the PDF", () => {
    const text = _collectPdfDocText(makeVotersPayload());
    expect(text).toContain("Buyer");
    expect(text).toContain("Receptive");
    expect(text).toContain("Uncertain");
    expect(text).toContain("Skeptical");
  });

  it("does NOT call voters '100 debate agents' in the PDF", () => {
    const text = _collectPdfDocText(makeVotersPayload());
    expect(text).not.toMatch(/100 debate agents/i);
    expect(text).not.toMatch(/100 LLM agents/i);
    expect(text).toMatch(/no new LLM calls per voter/i);
  });

  it("PDF shows a visible 'voter overlay unavailable' notice when payload is null", () => {
    const text = _collectPdfDocText(null);
    // Heading kept so the section is recognisable
    expect(text).toContain("100-voter influence layer");
    // Unavailable explainer body
    expect(text).toMatch(
      /not available in this downloaded report/i,
    );
    // No falsy bucket-distribution data
    expect(text).not.toMatch(/Influence dynamics across 4 rounds/);
  });

  it("PDF shows the unavailable notice with the API reason when voter_overlay_available is false", () => {
    const text = _collectPdfDocText({
      run_id: "abc",
      voter_overlay_available: false,
      reason: "lightweight_voters.json not on disk for this run",
    });
    expect(text).toContain("100-voter influence layer");
    expect(text).toMatch(
      /lightweight_voters\.json not on disk for this run/,
    );
  });

  it("includes 4-round influence dynamics table headers in PDF", () => {
    const text = _collectPdfDocText(makeVotersPayload());
    expect(text).toContain("Influence dynamics across 4 rounds");
  });
});


// ============================================================
// Anti-Phase-13 / behavioral_mind_layer checks
// ============================================================

describe("Phase 14A — no Phase 13 / behavioral_mind_layer leakage", () => {
  function _allWebSourceFiles(): string[] {
    const root = path.resolve(__dirname, "..", "..", "src");
    const out: string[] = [];
    const visit = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          visit(p);
        } else if (
          p.endsWith(".ts") ||
          p.endsWith(".tsx") ||
          p.endsWith(".js")
        ) {
          out.push(p);
        }
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

  it("no web source file references Phase 13 flags", () => {
    const forbidden = [
      "assembly_behavioral",
      "phase_13",
    ];
    for (const p of _allWebSourceFiles()) {
      const src = readFileSync(p, "utf8").toLowerCase();
      for (const tok of forbidden) {
        expect(src).not.toContain(tok);
      }
    }
  });
});
