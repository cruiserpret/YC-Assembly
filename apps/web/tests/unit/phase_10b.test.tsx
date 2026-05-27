// Phase 10B — frontend MVP tests. Covers operator scenarios 1–15
// (16 build + 17 typecheck are run as `npm run build` and `tsc`).

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Phase 14A — small test-only QueryClient provider so components that
// internally call useQuery (LightweightVoterPanelLive, PersonaList's
// voter fetch) can mount without "No QueryClient set" errors.
function withQueryClient(ui: React.ReactElement): React.ReactElement {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  return (
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

import { AgentGraph } from "@/components/AgentGraph";
import { AudienceFitCards } from "@/components/AudienceFitCards";
import { BriefForm } from "@/components/BriefForm";
import { CaveatBanner } from "@/components/CaveatBanner";
import { DiscussionTranscript } from "@/components/DiscussionTranscript";
import { EvidenceBaseCard } from "@/components/EvidenceBaseCard";
import { FounderTakeaway } from "@/components/FounderTakeaway";
import { IntentSnapshot } from "@/components/IntentSnapshot";
import { LiveDistribution } from "@/components/LiveDistribution";
import { PersonaList } from "@/components/PersonaList";
import { RunCockpit } from "@/components/RunCockpit";
import { RunProgress } from "@/components/RunProgress";
import { ReportDashboard } from "@/components/ReportDashboard";
import { WhyShiftedResistedCards } from "@/components/WhyShiftedResistedCards";
import {
  bucketStance,
  bucketStyle,
  formatShift,
  stanceShift,
} from "@/lib/stance";
import { stripPersonaSystemCaveats } from "@/lib/caveatFilter";
import { ALLOWED_INTENT_LABELS } from "@/lib/types";
import * as api from "@/lib/api";

const REPO_ROOT = path.resolve(__dirname, "../..");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeTwoPersonaTranscript() {
  return {
    run_id: "abc",
    discussion_session_id: "sess-1",
    groups: [
      {
        group_index: 0,
        personas: [
          {
            persona_id: "p1",
            display_name: "Alex P.",
            role: "trust_seeker",
          },
          {
            persona_id: "p2",
            display_name: "Riley M.",
            role: "competitor_user",
          },
        ],
        rounds: [
          {
            round_number: 1,
            round_label: "public_opening",
            turns: [
              {
                turn_id: "t1",
                turn_number: 0,
                speaker_persona_id: "p1",
                speaker_name: "Alex P.",
                speaker_role: "trust_seeker",
                turn_type: "public_opening",
                stance: "interested_if_proven",
                public_text: "I want to see independent test data.",
                referenced_turn_ids: [],
              },
              {
                turn_id: "t2",
                turn_number: 1,
                speaker_persona_id: "p2",
                speaker_name: "Riley M.",
                speaker_role: "competitor_user",
                turn_type: "public_opening",
                stance: "skeptical",
                public_text:
                  "I already use Hidrate Spark and it solves my problem.",
                referenced_turn_ids: [],
              },
            ],
          },
          {
            round_number: 2,
            round_label: "challenge",
            turns: [
              {
                turn_id: "t3",
                turn_number: 0,
                speaker_persona_id: "p2",
                speaker_name: "Riley M.",
                speaker_role: "competitor_user",
                turn_type: "challenge",
                stance: "skeptical",
                public_text:
                  "Until I see hard runtime numbers I won't switch.",
                referenced_turn_ids: ["t1"],
              },
            ],
          },
        ],
      },
    ],
    private_ballots: {
      p1: {
        pre: {
          stance: "skeptical",
          reasoning: "initial caution",
          confidence: "medium",
          top_objection: null,
          top_proof_need: null,
          is_repaired: false,
        },
        final: {
          stance: "interested_if_proven",
          reasoning: "Would consider if proof shown.",
          confidence: "medium",
          top_objection: "no third-party data",
          top_proof_need: "independent benchmark",
          is_repaired: false,
        },
      },
      p2: {
        pre: {
          stance: "skeptical",
          reasoning: "loyal to existing solution",
          confidence: "high",
          top_objection: null,
          top_proof_need: null,
          is_repaired: false,
        },
        final: {
          stance: "skeptical",
          reasoning: "Stays loyal.",
          confidence: "high",
          top_objection: "incumbent already works",
          top_proof_need: null,
          is_repaired: false,
        },
      },
    },
  };
}

// 1. brief form renders
describe("Phase 10B — UI", () => {
  it("1. brief form renders", () => {
    render(<BriefForm />);
    expect(screen.getByTestId("brief-form")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /run simulation/i }),
    ).toBeInTheDocument();
  });

  // 2. required fields validate
  it("2. required fields validate", async () => {
    const user = userEvent.setup();
    render(<BriefForm />);
    const form = screen.getByTestId("brief-form");
    // disable native HTML5 validation so our zod-like client logic
    // is what surfaces the messages
    form.setAttribute("novalidate", "true");
    await user.click(screen.getByTestId("brief-submit"));
    expect(
      await screen.findByText(/Product name is required/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Describe the product in at least 30/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Price or price structure is required/i),
    ).toBeInTheDocument();
  });

  // 3. manual persona forcing is not supported
  it("3. manual persona forcing fields are not present", () => {
    render(<BriefForm />);
    // The form must not surface any persona-forcing inputs
    expect(screen.queryByLabelText(/personas \(json/i)).toBeNull();
    expect(screen.queryByLabelText(/persona_roles/i)).toBeNull();
    expect(screen.queryByLabelText(/cohorts.*manual/i)).toBeNull();
  });

  // 4. POST /assembly/runs is called correctly
  it("4. POST /assembly/runs is called correctly", async () => {
    const user = userEvent.setup();
    const spy = vi
      .spyOn(api, "createAssemblyRun")
      .mockResolvedValue({
        run_id: "00000000-0000-0000-0000-000000000abc",
        status: "running",
        mode: "live_founder_brief",
        current_stage: "validating_brief",
        estimated_steps: 13,
        artifact_manifest: {},
      });
    render(<BriefForm />);
    await user.type(screen.getByPlaceholderText(/AquaSnap/), "AquaSnap");
    await user.type(
      screen.getByPlaceholderText(/What it is/),
      "A magnetic clip-on hydration reminder for office workers and students.",
    );
    // Phase 10B.5 placeholder is "$149 one-time for starter kit"
    await user.type(
      screen.getByPlaceholderText(/\$149|\$24/),
      "$24",
    );
    // Phase 10B.5 launch-geography placeholder is "Austin, Texas metro area"
    await user.type(
      screen.getByPlaceholderText(/Austin|United States/),
      "United States",
    );
    // Phase 10B.5 target-customers placeholder is "busy parents, college students, urban renters"
    await user.type(
      screen.getByPlaceholderText(/busy parents|office workers/),
      "office workers, students",
    );
    // Phase 10B.5 competitors placeholder is "Hidrate Spark, AnyList, manual whiteboard"
    await user.type(
      screen.getByPlaceholderText(/Hidrate Spark/),
      "Hidrate Spark, Ulla",
    );
    await user.click(screen.getByTestId("brief-submit"));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const arg = spy.mock.calls[0][0];
    expect(arg.mode).toBe("live_founder_brief");
    expect(arg.brief.product_name).toBe("AquaSnap");
    expect(arg.brief.target_customers).toEqual(["office workers", "students"]);
    expect(arg.brief.competitors_or_alternatives).toEqual([
      "Hidrate Spark",
      "Ulla",
    ]);
  });

  // 5. progress polling works
  it("5. progress polling works", async () => {
    const calls: number[] = [];
    vi.spyOn(api, "getAssemblyRun").mockImplementation(async (_id) => {
      calls.push(Date.now());
      const isLater = calls.length >= 2;
      return {
        run_id: "abc",
        mode: "live_founder_brief",
        status: isLater ? "complete" : "running",
        current_stage: isLater ? "complete" : "running_group_discussion",
        completed_stages: [],
        failed_stage: null,
        progress_pct: isLater ? 100 : 53,
        stage_progress: {
          validating_brief: { status: "complete", started_at: null, completed_at: null },
          running_group_discussion: { status: isLater ? "complete" : "running", started_at: null, completed_at: null },
        },
        artifact_links: {},
        error_message: null,
        caveat: "synthetic",
      };
    });
    render(<RunProgress runId="abc" pollIntervalMs={50} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("run-progress-stage").length).toBeGreaterThan(0);
    });
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2));
  });

  // 6. failed run displays failed_stage and error_message
  it("6. failed run shows failed_stage + error_message", async () => {
    vi.spyOn(api, "getAssemblyRun").mockResolvedValue({
      run_id: "abc",
      mode: "live_founder_brief",
      status: "failed",
      current_stage: "building_personas",
      completed_stages: ["validating_brief"],
      failed_stage: "building_personas",
      progress_pct: 30,
      stage_progress: {},
      artifact_links: {},
      error_message:
        "[building_personas] persona quality gates failed: count_in_range=False",
      caveat: "synthetic",
    });
    render(<RunProgress runId="abc" pollIntervalMs={50} />);
    await waitFor(() => {
      expect(screen.getByTestId("run-failed-card")).toBeInTheDocument();
      expect(screen.getByTestId("run-failed-message")).toHaveTextContent(
        /persona quality gates failed/,
      );
      expect(screen.getByTestId("run-failed-card")).toHaveTextContent(
        /building_personas/,
      );
    });
  });

  // 7. report dashboard renders
  it("7. report dashboard renders", async () => {
    vi.spyOn(api, "getAssemblyReport").mockResolvedValue({
      schema_version: "10A.3.live.v1",
      run_id: "abc",
      mode: "live_founder_brief",
      persona_source: "fresh_retrieval_driven",
      product_brief: { product_name: "AquaSnap" },
      executive_summary: ["Synthetic AquaSnap run.", "n=24 personas."],
      synthetic_society_size: 24,
      cohort_count: 5,
      synthetic_intent_snapshot: {
        intent_distribution: { would_consider_if_proven: 16 },
        switching_status_distribution: {},
        high_intent_segments_count: 0,
        rejection_segments_count: 1,
      },
      most_receptive_cohorts: [],
      most_resistant_cohorts: [],
      loyal_to_alternative_patterns: [],
      top_objections: [{ bucket: "specs_not_disclosed", weighted_score: 0.4 }],
      proof_needed: [{ bucket: "head_to_head_comparison", weighted_score: 0.5 }],
      persuasion_levers: [],
      competitor_or_alternative_comparison: [],
      society_wide_debate_summary: {
        argument_count: 16,
        propagation_count: 80,
        argument_type_distribution: {},
        response_type_distribution: {},
      },
      arguments_that_spread: [],
      arguments_that_were_resisted: [],
      public_private_shift_summary: {
        pre_stance_distribution: {},
        final_stance_distribution: {},
      },
      recommended_next_tests: ["Run a small real-people pilot."],
      confidence_dimensions: {},
      caveats: [
        "Synthetic society. Not a market forecast.",
      ],
      evidence_traceability_summary: {},
    });
    vi.spyOn(api, "getAssemblyIntent").mockResolvedValue({
      run_id: "abc",
      intent_distribution: { would_consider_if_proven: 16 },
    });
    vi.spyOn(api, "getAssemblyCohorts").mockResolvedValue({
      run_id: "abc",
      cohort_count: 5,
      cohort_sizes: [8, 6, 5, 3, 2],
    });
    vi.spyOn(api, "getAssemblyPersonas").mockResolvedValue({
      run_id: "abc",
      persona_count: 24,
      run_scope_id: "run_live_aquasnap_xxx",
    });
    vi.spyOn(api, "getAssemblyDiscussion").mockResolvedValue({
      run_id: "abc",
      persona_count: 24,
      public_turn_count: 84,
      ballot_count_by_stage: { pre: 24, reflection: 24, final: 24 },
    });
    render(withQueryClient(<ReportDashboard runId="abc" />));
    await waitFor(() => {
      expect(screen.getByTestId("report-dashboard")).toBeInTheDocument();
      // The hero / executive-summary card was removed in 10B+;
      // the report dashboard now starts with the IntentSnapshot.
      // Objections + proof now render as natural-language sentences,
      // not raw bucket names with scores.
      expect(
        screen.getByText(
          /Personas pushed back on missing or vague technical specifications|specs not disclosed/i,
        ),
      ).toBeInTheDocument();
      // Numeric weighted_score must NOT appear in the UI now.
      expect(screen.queryByText(/0\.40/)).toBeNull();
      expect(screen.queryByText(/0\.50/)).toBeNull();
    });
  });

  // 8. intent snapshot renders
  it("8. intent snapshot renders with closed-set labels only", () => {
    render(
      <IntentSnapshot
        intentDistribution={{
          would_consider_if_proven: 16,
          would_buy_now: 2,
          would_reject: 1,
          // values that don't match closed set should be ignored
          definitely_buys_immediately: 99,
        }}
        societySize={24}
      />,
    );
    expect(screen.getByTestId("intent-snapshot")).toBeInTheDocument();
    // Allowed labels with non-zero counts render with humanized text
    expect(screen.getByText(/Would buy now/)).toBeInTheDocument();
    expect(screen.getByText(/Would consider if proven/)).toBeInTheDocument();
    expect(screen.getByText(/Would reject/)).toBeInTheDocument();
    // Disallowed label is not rendered
    expect(screen.queryByText(/definitely_buys_immediately/)).toBeNull();
  });

  // 9. caveat banner is visible
  it("9. caveat banner is visible", () => {
    render(<CaveatBanner />);
    expect(screen.getByTestId("caveat-banner")).toBeInTheDocument();
    expect(screen.getByTestId("caveat-banner")).toHaveTextContent(
      /synthetic simulation/i,
    );
  });

  // 10. no forecast/launch verdict language in UI constants
  it("10. no forecast/launch verdict language in UI constants", () => {
    const filesToScan = [
      "src/components/BriefForm.tsx",
      "src/components/CaveatBanner.tsx",
      "src/components/CohortCards.tsx",
      "src/components/DiscussionSummary.tsx",
      "src/components/IntentSnapshot.tsx",
      "src/components/PersonaList.tsx",
      "src/components/ReportDashboard.tsx",
      "src/components/RunProgress.tsx",
      "src/app/page.tsx",
      "src/app/run/[runId]/page.tsx",
      "src/app/layout.tsx",
    ];
    const forbidden = [
      /\bthe\s+market\s+will\s+(?:adopt|buy)\b/i,
      /\b\d{1,3}\s*%\s+of\s+(?:the\s+)?(?:market|customers|users)\s+will\b/i,
      /\blaunch\s+this\b/i,
      /\bkill\s+this\b/i,
      /\bguaranteed\s+demand\b/i,
      /\b(?:real\s+)?customers\s+(?:will\s+)?(?:buy|adopt|use|reject)\b/i,
      /\bsales\s+forecast\b/i,
      /\blaunch\s+verdict\b/i,
      /\b(?:real\s+)?buyers\s+said\b/i,
    ];
    const findings: string[] = [];
    for (const f of filesToScan) {
      const text = readFileSync(path.join(REPO_ROOT, f), "utf-8");
      for (const re of forbidden) {
        const m = text.match(re);
        if (m) findings.push(`${f}: ${m[0]}`);
      }
    }
    expect(findings).toEqual([]);
  });

  // 11. (Phase 10B.5) — public mode hides the fixture_demo dev
  // toggle entirely. The button is gated by the PUBLIC_MODE flag,
  // which defaults to true in production.
  it("11. fixture_demo mode is hidden in public mode", () => {
    render(<BriefForm />);
    expect(screen.queryByTestId("mode-fixture")).toBeNull();
    expect(screen.queryByTestId("mode-live")).toBeNull();
    // The clean public-mode badge is visible instead.
    expect(screen.getByTestId("mode-public-display")).toBeInTheDocument();
  });

  // 12. (Phase 10B.5) — public mode displays "Live simulation"
  // instead of the dev-mode picker.
  it("12. public mode shows 'Live simulation' label", () => {
    render(<BriefForm />);
    expect(screen.getByTestId("mode-public-display")).toHaveTextContent(
      /Live simulation/,
    );
  });

  // 13. API client exports the assembly endpoint helpers
  it("13. API client exposes /assembly/runs/* helpers", () => {
    expect(typeof api.createAssemblyRun).toBe("function");
    expect(typeof api.getAssemblyRun).toBe("function");
    expect(typeof api.getAssemblyReport).toBe("function");
    expect(typeof api.getAssemblyReportMarkdown).toBe("function");
    expect(typeof api.getAssemblyPersonas).toBe("function");
    expect(typeof api.getAssemblyCohorts).toBe("function");
    expect(typeof api.getAssemblyDiscussion).toBe("function");
    expect(typeof api.getAssemblyIntent).toBe("function");
  });

  // 14. tokens.css file exists
  it("14. tokens.css file exists with CSS variables", () => {
    const tokens = readFileSync(
      path.join(REPO_ROOT, "src/styles/tokens.css"),
      "utf-8",
    );
    expect(tokens).toContain("--background");
    expect(tokens).toContain("--surface");
    expect(tokens).toContain("--accent");
    expect(tokens).toContain("--text-body");
  });

  // 15. exact required palette literally present
  it("15. locked palette literals (#0A0A0A, #141414, #AAFF00, #CCCCCC)", () => {
    const tokens = readFileSync(
      path.join(REPO_ROOT, "src/styles/tokens.css"),
      "utf-8",
    );
    expect(tokens).toMatch(/#0A0A0A/);
    expect(tokens).toMatch(/#141414/);
    expect(tokens).toMatch(/#AAFF00/);
    expect(tokens).toMatch(/#CCCCCC/);
  });

  // 13b. transcript renders turns + ballots from /discussion/turns
  it("13b. discussion transcript renders turns + private ballots", async () => {
    vi.spyOn(api, "getAssemblyDiscussionTurns").mockResolvedValue({
      run_id: "abc",
      discussion_session_id: "sess-1",
      groups: [
        {
          group_index: 0,
          personas: [
            { persona_id: "p1", display_name: "Ellis N.", role: "competitor_user" },
          ],
          rounds: [
            {
              round_number: 1,
              round_label: "public_opening",
              turns: [
                {
                  turn_id: "t1",
                  turn_number: 0,
                  speaker_persona_id: "p1",
                  speaker_name: "Ellis N.",
                  speaker_role: "competitor_user",
                  turn_type: "public_opening",
                  stance: "curious_but_unconvinced",
                  public_text:
                    "Coming at this as someone who's been using a competitor.",
                  referenced_turn_ids: [],
                },
              ],
            },
          ],
        },
      ],
      private_ballots: {
        p1: {
          final: {
            stance: "interested_if_proven",
            reasoning: "Would consider if specs are independently verified.",
            confidence: "medium",
            top_objection: "no third-party data",
            top_proof_need: "independent benchmark",
            is_repaired: false,
          },
        },
      },
    });
    render(<DiscussionTranscript runId="abc" />);
    await waitFor(() => {
      expect(screen.getByTestId("discussion-transcript")).toBeInTheDocument();
      expect(screen.getByTestId("transcript-turn")).toHaveTextContent(
        /Coming at this as someone who's been using/,
      );
      expect(screen.getAllByText("Ellis N.").length).toBeGreaterThan(0);
      // 10B+ refinement: collapsed bucket labels read as
      // Receptive / Uncertain / Resistant in the founder-facing UI;
      // curious_but_unconvinced lands in the "neutral" bucket → "Uncertain".
      expect(screen.getByTestId("turn-stance")).toHaveTextContent(
        /Uncertain/,
      );
      // Raw stance is preserved on the title attribute for context.
      expect(screen.getByTestId("turn-stance")).toHaveAttribute(
        "title",
        "curious_but_unconvinced",
      );
    });
  });

  // 13c. empty-state when fixture_demo / no transcript available
  it("13c. transcript empty-state for fixture_demo", async () => {
    vi.spyOn(api, "getAssemblyDiscussionTurns").mockResolvedValue({
      run_id: "fix",
      discussion_session_id: null,
      groups: [],
      private_ballots: {},
      note: "Per-turn transcript is only emitted for live founder-brief runs.",
    });
    render(<DiscussionTranscript runId="fix" />);
    await waitFor(() => {
      expect(screen.getByTestId("transcript-empty")).toHaveTextContent(
        /only emitted for live/,
      );
    });
  });

  // 13d. stance bucket helper collapses enum to FOR/AGN/NEU
  it("13d. bucketStance collapses the discussion enum correctly", () => {
    expect(bucketStance("interested_if_proven")).toBe("for");
    expect(bucketStance("would_buy_now")).toBe("for");
    expect(bucketStance("skeptical")).toBe("against");
    expect(bucketStance("likely_reject")).toBe("against");
    expect(bucketStance("loyal_to_current_alternative")).toBe("against");
    expect(bucketStance("curious_but_unconvinced")).toBe("neutral");
    expect(bucketStance("needs_more_information")).toBe("neutral");
    expect(bucketStance(null)).toBe("neutral");
    expect(bucketStance("garbage_label")).toBe("neutral");
  });

  // 13e. shift magnitude is computed correctly
  it("13e. stanceShift produces a directional, normalized magnitude", () => {
    // skeptical (-1) → interested_if_proven (+1) over a 4-wide range
    // → +0.5 normalized; arrow ▲, magnitude "0.50"
    const shift = stanceShift("skeptical", "interested_if_proven");
    expect(shift).toBeCloseTo(0.5, 2);
    const fmt = formatShift(shift);
    expect(fmt.arrow).toBe("▲");
    expect(fmt.magnitude).toBe("0.50");

    // Reverse direction → ▼
    const reverseShift = stanceShift("interested_if_proven", "skeptical");
    const reverseFmt = formatShift(reverseShift);
    expect(reverseFmt.arrow).toBe("▼");

    // Same stance → no arrow
    expect(formatShift(stanceShift("skeptical", "skeptical")).arrow).toBe(
      null,
    );
  });

  // 13f. AgentGraph renders a live particle canvas
  it("13f. AgentGraph renders a canvas for the particle simulation", () => {
    const transcript = makeTwoPersonaTranscript();
    render(<AgentGraph transcript={transcript} width={400} height={400} />);
    expect(screen.getByTestId("agent-graph")).toBeInTheDocument();
    expect(screen.getByTestId("agent-graph-canvas")).toBeInTheDocument();
    // Canvas content can't be DOM-asserted directly in jsdom; the
    // build-time guarantee is that the canvas mounts + paints.
  });

  // 13g. LiveDistribution shows Receptive / Uncertain / Resistant rows
  it("13g. LiveDistribution renders the three bucket rows", () => {
    const transcript = makeTwoPersonaTranscript();
    render(<LiveDistribution transcript={transcript} />);
    const dist = screen.getByTestId("live-distribution");
    expect(dist).toHaveTextContent(/Receptive/);
    expect(dist).toHaveTextContent(/Uncertain/);
    expect(dist).toHaveTextContent(/Resistant/);
  });

  // 13h. RunCockpit exposes the GRAPH/DEBATE/SPLIT toggle
  it("13h. RunCockpit renders the view toggle with three modes", async () => {
    vi.spyOn(api, "getAssemblyDiscussionTurns").mockResolvedValue(
      makeTwoPersonaTranscript(),
    );
    // Stub the report fetches so RunCockpit's embedded ReportDashboard
    // doesn't fail; we only care about the cockpit toggle here.
    vi.spyOn(api, "getAssemblyReport").mockRejectedValue(
      new Error("skip"),
    );
    vi.spyOn(api, "getAssemblyIntent").mockRejectedValue(
      new Error("skip"),
    );
    vi.spyOn(api, "getAssemblyCohorts").mockRejectedValue(
      new Error("skip"),
    );
    vi.spyOn(api, "getAssemblyPersonas").mockRejectedValue(
      new Error("skip"),
    );
    vi.spyOn(api, "getAssemblyDiscussion").mockRejectedValue(
      new Error("skip"),
    );
    render(<RunCockpit runId="abc" />);
    await waitFor(() => {
      expect(screen.getByTestId("view-toggle")).toBeInTheDocument();
      expect(screen.getByTestId("view-graph")).toBeInTheDocument();
      expect(screen.getByTestId("view-debate")).toBeInTheDocument();
      expect(screen.getByTestId("view-split")).toBeInTheDocument();
    });
  });

  // 13i. Round selector exposes a tab per round + per-round
  // distribution bar
  it("13i. transcript exposes round tabs + per-round distribution bar", async () => {
    const transcript = makeTwoPersonaTranscript();
    render(<DiscussionTranscript runId="x" transcript={transcript} />);
    await waitFor(() => {
      expect(screen.getByTestId("transcript-round-tabs")).toBeInTheDocument();
      expect(screen.getByTestId("round-distribution-bar")).toBeInTheDocument();
    });
  });

  // ----------------------------------------------------------------
  // Phase 10B+ refinement acceptance checks
  // ----------------------------------------------------------------

  // R1. Founder takeaway renders
  it("R1. FounderTakeaway renders a synthesized summary", () => {
    const transcript = makeTwoPersonaTranscript();
    const report = {
      product_brief: { product_name: "AquaSnap" },
      executive_summary: ["whatever"],
      synthetic_society_size: 24,
      cohort_count: 5,
      synthetic_intent_snapshot: {
        intent_distribution: {},
        switching_status_distribution: {},
        high_intent_segments_count: 0,
        rejection_segments_count: 0,
      },
      most_receptive_cohorts: [],
      most_resistant_cohorts: [],
      loyal_to_alternative_patterns: [],
      top_objections: [
        { bucket: "trust_or_review_gap", weighted_score: 0.4 },
      ],
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
      schema_version: "10A.3.live.v1",
      run_id: "abc",
    };
    render(
      <FounderTakeaway
        report={report as Parameters<typeof FounderTakeaway>[0]["report"]}
        transcript={transcript}
      />,
    );
    expect(screen.getByTestId("founder-takeaway")).toBeInTheDocument();
    expect(screen.getByTestId("founder-takeaway")).toHaveTextContent(
      /synthetic society/i,
    );
  });

  // R2. FOR / NEUTRAL / AGAINST mapped to Receptive / Uncertain / Resistant
  it("R2. bucketStyle exposes founder-friendly labels", () => {
    expect(bucketStyle("for").label).toBe("Receptive");
    expect(bucketStyle("neutral").label).toBe("Uncertain");
    expect(bucketStyle("against").label).toBe("Resistant");
  });

  // R3. Run ID is hidden from the main UI (only inside Technical
  // details disclosure)
  it("R3. RunCockpit hides run id under Technical details", async () => {
    vi.spyOn(api, "getAssemblyDiscussionTurns").mockResolvedValue(
      makeTwoPersonaTranscript(),
    );
    vi.spyOn(api, "getAssemblyReport").mockRejectedValue(new Error("skip"));
    vi.spyOn(api, "getAssemblyIntent").mockRejectedValue(new Error("skip"));
    vi.spyOn(api, "getAssemblyCohorts").mockRejectedValue(new Error("skip"));
    vi.spyOn(api, "getAssemblyPersonas").mockRejectedValue(
      new Error("skip"),
    );
    vi.spyOn(api, "getAssemblyDiscussion").mockRejectedValue(
      new Error("skip"),
    );
    render(<RunCockpit runId="abcdef0123456789" />);
    await waitFor(() => {
      // Toggle exists, but the run id itself is inside a closed
      // <details> — its parent is collapsed by default.
      expect(
        screen.getByTestId("technical-details-toggle"),
      ).toBeInTheDocument();
      const runIdEl = screen.getByTestId("run-id");
      const details = runIdEl.closest("details");
      expect(details).not.toBeNull();
      expect((details as HTMLDetailsElement).open).toBe(false);
    });
  });

  // R4. Society graph legend / guide renders
  it("R4. AgentGraph exposes a Graph guide block", () => {
    const transcript = makeTwoPersonaTranscript();
    render(<AgentGraph transcript={transcript} width={400} height={400} />);
    const guide = screen.getByTestId("graph-guide");
    expect(guide).toBeInTheDocument();
    expect(guide).toHaveTextContent(/Graph guide/);
  });

  // R5. Intent snapshot segmented bar renders
  it("R5. IntentSnapshot renders a segmented bar with hover data", () => {
    render(
      <IntentSnapshot
        intentDistribution={{
          would_consider_if_proven: 16,
          would_buy_now: 2,
          would_compare_to_current_brand: 1,
          loyal_to_current_alternative: 5,
        }}
        societySize={24}
      />,
    );
    expect(screen.getByTestId("intent-segmented-bar")).toBeInTheDocument();
    const segments = screen.getAllByTestId("intent-segment");
    expect(segments.length).toBe(4);
    // R6: each segment carries label + count + pct via title attr +
    // data attributes
    for (const s of segments) {
      expect(s).toHaveAttribute("title");
      expect(s.getAttribute("title") ?? "").toMatch(
        /personas? — \d+%/,
      );
      expect(s).toHaveAttribute("data-intent-count");
      expect(s).toHaveAttribute("data-intent-pct");
    }
  });

  // R7. Why shifted / why resisted section renders
  it("R7. WhyShiftedResistedCards renders both story cards", () => {
    const transcript = makeTwoPersonaTranscript();
    const report = {
      product_brief: {},
      executive_summary: [],
      synthetic_society_size: 2,
      cohort_count: 1,
      synthetic_intent_snapshot: {
        intent_distribution: {},
        switching_status_distribution: {},
        high_intent_segments_count: 0,
        rejection_segments_count: 0,
      },
      most_receptive_cohorts: [],
      most_resistant_cohorts: [],
      loyal_to_alternative_patterns: [],
      top_objections: [],
      proof_needed: [
        { bucket: "head_to_head_comparison", weighted_score: 0.5 },
      ],
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
      schema_version: "x",
      run_id: "abc",
    };
    render(
      <WhyShiftedResistedCards
        report={report as Parameters<typeof WhyShiftedResistedCards>[0]["report"]}
        transcript={transcript}
      />,
    );
    expect(screen.getByTestId("why-shifted-resisted")).toBeInTheDocument();
    expect(screen.getByTestId("why-shifted")).toBeInTheDocument();
    expect(screen.getByTestId("why-resisted")).toBeInTheDocument();
  });

  // R8. Society composition is collapsible
  it("R8. PersonaList renders as a collapsible details element", () => {
    render(
      withQueryClient(
        <PersonaList
          personas={{ run_id: "abc", persona_count: 24 }}
        />,
      ),
    );
    const personaList = screen.getByTestId("persona-list");
    expect(personaList.tagName).toBe("DETAILS");
    expect(
      screen.getByText(/Society composition/i),
    ).toBeInTheDocument();
  });

  // R9. Best-fit + R10. Hardest-to-convince audience cards render
  it("R9 + R10. AudienceFitCards renders both cards", () => {
    const transcript = makeTwoPersonaTranscript();
    render(<AudienceFitCards transcript={transcript} />);
    expect(screen.getByTestId("audience-fit-cards")).toBeInTheDocument();
    expect(screen.getByTestId("best-fit-card")).toBeInTheDocument();
    expect(screen.getByTestId("hardest-card")).toBeInTheDocument();
  });

  // Phase 10B.3 — hardest-to-convince card must populate even when
  // no persona finished resistant. The fallback uses the
  // highest-uncertain rows.
  it("R10.3 — hardest card populates when zero resistant", () => {
    const transcript = makeTwoPersonaTranscript();
    // Mutate p2 → curious_but_unconvinced (UNCERTAIN) so neither
    // persona is RESISTANT.
    transcript.private_ballots.p2.final.stance =
      "curious_but_unconvinced";
    render(<AudienceFitCards transcript={transcript} />);
    const card = screen.getByTestId("hardest-card");
    // Must NOT show the "no friction surfaced" empty-state copy.
    expect(card).not.toHaveTextContent(/No friction pattern surfaced/i);
    expect(card).not.toHaveTextContent(/No persistent resistance/i);
    // Must surface the uncertain-fallback copy.
    expect(card.textContent || "").toMatch(
      /still required stronger proof|stronger proof|hardest/i,
    );
  });

  // R11. Evidence base indicator renders + accepts gate fallback from
  // personas payload
  it("R11. EvidenceBaseCard renders with gate fallback", async () => {
    vi.spyOn(api, "getAssemblyAudit").mockResolvedValue(null);
    render(
      <EvidenceBaseCard
        runId="abc"
        personas={{
          run_id: "abc",
          persona_count: 24,
          quality_gates_summary: {
            count_in_range: true,
            no_duplicates_ok: true,
            evidence_link_coverage_ok: true,
          },
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("evidence-base-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("evidence-base-card")).toHaveTextContent(
      /Evidence base/i,
    );
  });

  // R12. Caveats are still visible
  it("R12. CaveatBanner is still in the component graph", () => {
    render(<CaveatBanner />);
    expect(screen.getByTestId("caveat-banner")).toBeInTheDocument();
  });

  // R13. UI does not contain forbidden forecast/launch verdict
  // language in the new components
  it("R13. new components contain no forecast/launch verdict copy", () => {
    const filesToScan = [
      "src/components/FounderTakeaway.tsx",
      "src/components/WhyShiftedResistedCards.tsx",
      "src/components/AudienceFitCards.tsx",
      "src/components/EvidenceBaseCard.tsx",
      "src/components/IntentSnapshot.tsx",
    ];
    const forbidden = [
      /\bthe\s+market\s+will\s+(?:adopt|buy)\b/i,
      /\b\d{1,3}\s*%\s+of\s+(?:the\s+)?(?:market|customers|users)\s+will\b/i,
      /\blaunch\s+this\b/i,
      /\bkill\s+this\b/i,
      /\bguaranteed\s+demand\b/i,
      /\b(?:real\s+)?customers\s+will\s+(?:buy|adopt)\b/i,
      /\bsales\s+forecast\b/i,
      /\blaunch\s+verdict\b/i,
    ];
    const findings: string[] = [];
    for (const f of filesToScan) {
      const text = readFileSync(path.join(REPO_ROOT, f), "utf-8");
      for (const re of forbidden) {
        const m = text.match(re);
        if (m) findings.push(`${f}: ${m[0]}`);
      }
    }
    expect(findings).toEqual([]);
  });

  // ----------------------------------------------------------------
  // Phase 10B.1 — caveat-leak frontend filter
  // ----------------------------------------------------------------

  it("10B1.1 stripPersonaSystemCaveats removes synthetic-n caveat", () => {
    const out = stripPersonaSystemCaveats(
      "Caveat: this was a synthetic n=24 chat. I'd want a runtime benchmark.",
    );
    expect(out).not.toMatch(/synthetic n=/i);
    expect(out).not.toMatch(/n=24/i);
    expect(out).toMatch(/runtime benchmark/);
  });

  it("10B1.2 stripPersonaSystemCaveats removes 'directional, not a verdict'", () => {
    const out = stripPersonaSystemCaveats(
      "Treating it as directional, not a verdict — at $69.99 I'd want runtime proof.",
    );
    expect(out).not.toMatch(/directional, not a verdict/i);
    expect(out).toMatch(/\$69\.99/);
  });

  it("10B1.3 stripPersonaSystemCaveats removes internal calibration markers", () => {
    const out = stripPersonaSystemCaveats(
      "I'd want runtime proof. [stance_calibration:downgrade_for_to_neutral: foo bar]",
    );
    expect(out).not.toMatch(/stance_calibration/);
    expect(out).toMatch(/runtime proof/);
  });

  it("10B1.4 stripPersonaSystemCaveats keeps legitimate buyer reasoning", () => {
    const out = stripPersonaSystemCaveats(
      "I already use a PEET dryer. SoleNest's two-pod design is interesting but I'd need a clear refund window.",
    );
    expect(out).toMatch(/PEET/);
    expect(out).toMatch(/two-pod design/);
  });

  // Bonus: closed intent label set is correct
  it("bonus. closed-set IntentLabel includes the spec's labels", () => {
    expect(new Set(ALLOWED_INTENT_LABELS)).toEqual(
      new Set([
        "would_buy_now",
        "would_try_once",
        "would_join_waitlist",
        "would_consider_if_proven",
        "would_share_with_friend",
        "would_compare_to_current_brand",
        "loyal_to_current_alternative",
        "would_reject",
        "would_block",
      ]),
    );
  });
});


// ============================================================
// Phase 10B.5 — YC demo polish acceptance tests (24 checks)
// ============================================================

describe("Phase 10B.5 — YC demo polish", () => {
  // 1. public mode hides fixture_demo
  it("public mode hides fixture_demo control", () => {
    render(<BriefForm />);
    expect(screen.queryByTestId("mode-fixture")).toBeNull();
  });

  // 2. public mode hides live_founder_brief technical label
  it("public mode hides live_founder_brief technical label", () => {
    render(<BriefForm />);
    expect(screen.queryByTestId("mode-live")).toBeNull();
    // "live_founder_brief" string should not be visible to public users
    const form = screen.getByTestId("brief-form");
    expect(form.textContent || "").not.toMatch(/live_founder_brief/);
  });

  // 3. public mode hides raw LLM call count
  it("public mode hides LLM call count", () => {
    render(<BriefForm />);
    const estimate = screen.getByTestId("run-estimate");
    expect(estimate.textContent || "").not.toMatch(/LLM calls/i);
  });

  // 4. public mode hides raw cost estimate ($X.XX)
  it("public mode hides raw cost estimate", () => {
    render(<BriefForm />);
    const estimate = screen.getByTestId("run-estimate");
    expect(estimate.textContent || "").not.toMatch(/~\$\d/);
  });

  // 5. "Live simulation" appears as user-facing mode
  it("'Live simulation' appears as user-facing mode label", () => {
    render(<BriefForm />);
    expect(
      screen.getByTestId("mode-public-display"),
    ).toHaveTextContent(/Live simulation/);
  });

  // 6. Sample report button renders on landing page
  it("'View sample report' CTA renders on landing page", async () => {
    // The CTA is part of HomePage but we test it in isolation by
    // importing the link element. Smoke-test via the file.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.join(REPO_ROOT, "src/app/page.tsx"),
      "utf-8",
    );
    expect(src).toMatch(/View sample report/);
    expect(src).toMatch(/data-testid="view-sample-report-cta"/);
    expect(src).toMatch(/href="\/sample-report"/);
  });

  // 7. Sample report route file exists
  it("sample report route file exists at /sample-report", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const sampleSrc = fs.readFileSync(
      path.join(REPO_ROOT, "src/app/sample-report/page.tsx"),
      "utf-8",
    );
    expect(sampleSrc).toMatch(/sample-report-page/);
    expect(sampleSrc).toMatch(/Sample report/);
    expect(sampleSrc).toMatch(/PantryPulse/);
  });

  // 8. Primary price field renders
  it("primary price field renders", () => {
    render(<BriefForm />);
    expect(
      screen.getByPlaceholderText(/\$149 one-time for starter kit/),
    ).toBeInTheDocument();
  });

  // 9. Optional bundle price field renders
  it("optional bundle price field renders", () => {
    render(<BriefForm />);
    expect(
      screen.getByTestId("bundle-price-input"),
    ).toBeInTheDocument();
  });

  // 10. Optional subscription price field renders
  it("optional subscription price field renders", () => {
    render(<BriefForm />);
    expect(
      screen.getByTestId("subscription-price-input"),
    ).toBeInTheDocument();
  });

  // 11. Optional accessory/refill price field renders
  it("optional accessory/refill price field renders", () => {
    render(<BriefForm />);
    expect(
      screen.getByTestId("accessory-price-input"),
    ).toBeInTheDocument();
  });

  // 12. Pricing fields serialize correctly into a single
  // price_or_price_structure string on submit.
  it("structured pricing serializes into the flat backend field", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "createAssemblyRun").mockResolvedValue({
      run_id: "p10b5-test",
      status: "running",
      mode: "live_founder_brief",
      current_stage: "validating_brief",
      estimated_steps: 13,
      artifact_manifest: {},
    });
    render(<BriefForm />);
    await user.type(screen.getByPlaceholderText(/AquaSnap/), "AquaSnap");
    await user.type(
      screen.getByPlaceholderText(/What it is/),
      "A magnetic clip-on hydration reminder, at least thirty characters long.",
    );
    await user.type(
      screen.getByPlaceholderText(/\$149 one-time/),
      "$149 starter kit",
    );
    await user.type(screen.getByTestId("bundle-price-input"), "$269 2-pack");
    await user.type(
      screen.getByTestId("subscription-price-input"),
      "$7.99/month",
    );
    await user.type(
      screen.getByTestId("accessory-price-input"),
      "$19.99 for tags",
    );
    await user.type(
      screen.getByPlaceholderText(/Austin/),
      "Austin",
    );
    await user.type(
      screen.getByPlaceholderText(/busy parents/),
      "parents",
    );
    await user.type(
      screen.getByPlaceholderText(/Hidrate Spark/),
      "AnyList",
    );
    await user.click(screen.getByTestId("brief-submit"));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const sent = spy.mock.calls[0][0].brief.price_or_price_structure;
    expect(sent).toContain("$149 starter kit");
    expect(sent).toContain("Bundle: $269 2-pack");
    expect(sent).toContain("Optional subscription: $7.99/month");
    expect(sent).toContain("Accessory: $19.99 for tags");
  });

  // 13. snake_case labels are humanized in IntentSnapshot
  it("snake_case labels are humanized in IntentSnapshot", () => {
    render(
      <IntentSnapshot
        intentDistribution={{
          would_buy_now: 4,
          loyal_to_current_alternative: 2,
        }}
        switchingDistribution={{
          no_current_alternative: 8,
          actively_comparing: 4,
          weakly_attached_to_alternative: 6,
          refuses_switching: 2,
        }}
        societySize={20}
      />,
    );
    // Intent labels are humanized
    expect(screen.getByText("Would buy now")).toBeInTheDocument();
    expect(
      screen.getByText("Loyal to current alternative"),
    ).toBeInTheDocument();
    // Switching-status labels are humanized
    expect(screen.getByText("No current alternative")).toBeInTheDocument();
    expect(screen.getByText("Actively comparing options")).toBeInTheDocument();
    expect(
      screen.getByText("Weakly attached to current alternative"),
    ).toBeInTheDocument();
    expect(screen.getByText("Refuses to switch")).toBeInTheDocument();
    // No raw snake_case slugs visible
    const card = screen.getByTestId("intent-snapshot");
    expect(card.textContent || "").not.toMatch(/loyal_to_current_alternative/);
    expect(card.textContent || "").not.toMatch(/refuses_switching/);
    expect(card.textContent || "").not.toMatch(/actively_comparing/);
  });

  // 14. Intent snapshot includes stance-vs-intent explainer
  it("IntentSnapshot has stance-vs-intent explainer", () => {
    render(
      <IntentSnapshot
        intentDistribution={{ would_buy_now: 1 }}
        societySize={1}
      />,
    );
    const explainer = screen.getByTestId("intent-stance-explainer");
    expect(explainer).toBeInTheDocument();
    expect(explainer.textContent || "").toMatch(/Stance/);
    expect(explainer.textContent || "").toMatch(/Intent/);
  });

  // 15. Best-fit audience copy is human-readable (not raw role)
  it("best-fit audience copy uses target-customer language", () => {
    const transcript = makeTwoPersonaTranscript();
    transcript.private_ballots.p1.final.stance = "interested_if_proven";
    render(<AudienceFitCards transcript={transcript} />);
    const card = screen.getByTestId("best-fit-card");
    // Role labels appear under the "Simulation roles in this audience"
    // sub-header, not as the primary copy.
    expect(card.textContent || "").toMatch(
      /Simulation roles in this audience/,
    );
  });

  // 16. Hardest-to-convince copy reads as friction language
  it("hardest-to-convince copy is human-readable", () => {
    const transcript = makeTwoPersonaTranscript();
    render(<AudienceFitCards transcript={transcript} />);
    const card = screen.getByTestId("hardest-card");
    expect(card.textContent || "").toMatch(
      /Simulation roles in this audience|hardest|stronger proof|friction/i,
    );
  });

  // 17. ReportActions renders Copy report link button
  it("ReportActions Copy-link button works", async () => {
    const { ReportActions } = await import("@/components/ReportActions");
    render(<ReportActions runId="abc" />);
    expect(
      screen.getByTestId("copy-report-link"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("copy-report-link"),
    ).toHaveTextContent(/Copy report link/);
  });

  // 18. ReportActions renders Run-another-product button
  it("ReportActions Run-another-product button links home", async () => {
    const { ReportActions } = await import("@/components/ReportActions");
    render(<ReportActions runId="abc" />);
    const btn = screen.getByTestId("run-another-product");
    expect(btn).toBeInTheDocument();
    expect(btn.getAttribute("href")).toBe("/");
  });

  // 19. Caveat / trust section still exists
  it("CaveatBanner still renders", async () => {
    const { CaveatBanner } = await import("@/components/CaveatBanner");
    render(<CaveatBanner />);
    expect(screen.getByTestId("caveat-banner")).toBeInTheDocument();
  });

  // 20. CaveatBanner uses 'How to read this report' framing
  it("CaveatBanner uses 'How to read this report' framing", async () => {
    const { CaveatBanner } = await import("@/components/CaveatBanner");
    render(<CaveatBanner />);
    expect(
      screen.getByTestId("caveat-banner"),
    ).toHaveTextContent(/How to read this report/);
  });

  // 21. Top headline behavior preserved (RECEPTIVE label still
  // RECEPTIVE per operator decision in Phase 10B.3)
  it("RECEPTIVE label is preserved (not renamed)", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const stance = fs.readFileSync(
      path.join(REPO_ROOT, "src/lib/stance.ts"),
      "utf-8",
    );
    expect(stance).toMatch(/RECEPTIVE/);
    expect(stance).not.toMatch(/Conditionally receptive/i);
    expect(stance).not.toMatch(/Receptive if proven/i);
  });

  // 22. Existing progress screen file is intact
  it("existing RunProgress component is still in place", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.join(REPO_ROOT, "src/components/RunProgress.tsx"),
      "utf-8",
    );
    // Phase 10B operator decision: do NOT rebuild progress screen
    expect(src.length).toBeGreaterThan(100);
    expect(src).toMatch(/run-progress-stage|RunProgress/);
  });

  // 23. Field helpers render
  it("BriefForm field helpers render under labels", () => {
    render(<BriefForm />);
    // helper text strings used in BriefForm
    expect(
      screen.getByText(/Avoid marketing copy/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Use a specific market if possible/i),
    ).toBeInTheDocument();
  });

  // 24. Public mode is on by default (env var unset = public mode)
  it("PUBLIC_MODE flag defaults to true", async () => {
    const { PUBLIC_MODE } = await import("@/lib/debug");
    expect(PUBLIC_MODE).toBe(true);
  });
});


// ============================================================
// Phase 10B.7 — final polish acceptance tests
// ============================================================

describe("Phase 10B.7 — final polish", () => {
  // ---- header / tagline ----

  it("public header no longer shows 'synthetic-society simulation lab'", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.join(REPO_ROOT, "src/app/layout.tsx"),
      "utf-8",
    );
    expect(src.toLowerCase()).not.toContain("synthetic-society simulation lab");
    expect(src.toLowerCase()).not.toContain("synthetic society simulation lab");
  });

  it("public header has Product / Sample Report / Run Simulation / Contact nav links", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.join(REPO_ROOT, "src/app/layout.tsx"),
      "utf-8",
    );
    expect(src).toMatch(/href="\/contact"/);
    expect(src).toMatch(/\bProduct\s*</);
    expect(src).toMatch(/\bSample Report\s*</);
    expect(src).toMatch(/\bRun Simulation\s*</);
    expect(src).toMatch(/\bContact\s*</);
  });

  // ---- footer ----

  it("SiteFooter renders Privacy / Terms / Contact links + ©", async () => {
    const { SiteFooter } = await import("@/components/SiteFooter");
    render(<SiteFooter />);
    const footer = screen.getByTestId("site-footer");
    expect(footer).toHaveTextContent(/Privacy/);
    expect(footer).toHaveTextContent(/Terms/);
    expect(footer).toHaveTextContent(/Contact/);
    expect(footer).not.toHaveTextContent(/team@assemblysimulator\.com/);
    expect(footer).toHaveTextContent(/© \d{4} Assembly\. All rights reserved\./);
  });

  // ---- routes exist ----

  it("/contact, /privacy, /terms route files exist", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    for (const route of ["contact", "privacy", "terms"]) {
      const p = path.join(REPO_ROOT, "src/app", route, "page.tsx");
      expect(fs.existsSync(p)).toBe(true);
      const src = fs.readFileSync(p, "utf-8");
      expect(src.length).toBeGreaterThan(50);
    }
  });

  // ---- contact form validation ----

  it("ContactForm renders name / email / message + submit", async () => {
    const { ContactForm } = await import("@/components/ContactForm");
    render(<ContactForm />);
    expect(screen.getByTestId("contact-name")).toBeInTheDocument();
    expect(screen.getByTestId("contact-email")).toBeInTheDocument();
    expect(screen.getByTestId("contact-message")).toBeInTheDocument();
    expect(screen.getByTestId("contact-submit")).toBeInTheDocument();
  });

  it("ContactForm validates required fields client-side", async () => {
    const { ContactForm } = await import("@/components/ContactForm");
    const user = userEvent.setup();
    render(<ContactForm />);
    await user.click(screen.getByTestId("contact-submit"));
    expect(
      await screen.findByText(/Name is required/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Enter a valid email/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/at least 10 characters/i),
    ).toBeInTheDocument();
  });

  it("ContactForm POSTs to /contact and shows success on 2xx", async () => {
    const { ContactForm } = await import("@/components/ContactForm");
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({ ok: true, detail: "Thanks — we'll get back to you soon." }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    render(<ContactForm />);
    await user.type(screen.getByTestId("contact-name"), "Alex Founder");
    await user.type(screen.getByTestId("contact-email"), "alex@example.com");
    await user.type(
      screen.getByTestId("contact-message"),
      "Hi, I'd love a quick demo for my startup.",
    );
    await user.click(screen.getByTestId("contact-submit"));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/contact$/);
    expect(init?.method).toBe("POST");
    const body = JSON.parse((init?.body as string) ?? "{}");
    expect(body.name).toBe("Alex Founder");
    expect(body.email).toBe("alex@example.com");
    expect(body.message).toMatch(/quick demo/);
    expect(
      await screen.findByTestId("contact-success"),
    ).toHaveTextContent(/back to you soon/);
  });

  it("ContactForm shows backend error message on 4xx/5xx", async () => {
    const { ContactForm } = await import("@/components/ContactForm");
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail:
            "Too many contact requests from this address. Please try again in a few minutes.",
        }),
        { status: 429, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<ContactForm />);
    await user.type(screen.getByTestId("contact-name"), "Bot Tester");
    await user.type(screen.getByTestId("contact-email"), "test@example.com");
    await user.type(
      screen.getByTestId("contact-message"),
      "Trying to trigger the rate limit branch.",
    );
    await user.click(screen.getByTestId("contact-submit"));
    expect(
      await screen.findByTestId("contact-error"),
    ).toHaveTextContent(/Too many contact requests/);
  });

  it("ContactForm prevents duplicate rapid submissions", async () => {
    const { ContactForm } = await import("@/components/ContactForm");
    const user = userEvent.setup();
    // Slow mock so we can click twice before the first resolves
    let resolveFn: (r: Response) => void = () => {};
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFn = resolve;
        }),
    );
    render(<ContactForm />);
    await user.type(screen.getByTestId("contact-name"), "Alex Founder");
    await user.type(screen.getByTestId("contact-email"), "alex@example.com");
    await user.type(
      screen.getByTestId("contact-message"),
      "Testing duplicate-submit guard.",
    );
    const submit = screen.getByTestId("contact-submit");
    await user.click(submit);
    // submit is disabled (loading state)
    expect(submit).toBeDisabled();
    // Resolve the pending request so the next test's afterEach isn't stuck
    resolveFn(
      new Response(
        JSON.stringify({ ok: true, detail: "Thanks." }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
  });

  // ---- feature-card copy ----

  it("landing feature cards use the new bigger-headline structure", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const src = fs.readFileSync(
      path.join(REPO_ROOT, "src/app/page.tsx"),
      "utf-8",
    );
    expect(src).toMatch(/eyebrow="SIMULATE"/);
    expect(src).toMatch(/eyebrow="EVOLVE"/);
    expect(src).toMatch(/eyebrow="PREDICT"/);
    expect(src).toMatch(/Evidence-grounded personas debate your product/);
    expect(src).toMatch(/Opinions shift and converge/);
    expect(src).toMatch(/Market Reaction Report of the outcome/);
  });
});
