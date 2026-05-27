// Phase 12 follow-up — Full Debate & Conversations section in the
// downloaded HTML report. These tests pin the contract that the
// downloadable report includes the entire debate (every group, every
// round, every public turn) and degrades gracefully when transcript
// artifacts are missing.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  renderStructuredReport,
  type ReportContext,
} from "@/components/DownloadReportButton";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
  TranscriptGroup,
} from "@/lib/types";

const REAL_TRANSCRIPT_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "data",
  "sample_discussion_transcript.json",
);

function loadRealTranscript(): DiscussionTranscriptPayload {
  const raw = JSON.parse(readFileSync(REAL_TRANSCRIPT_PATH, "utf8"));
  return {
    run_id: raw.run_id ?? "test-run",
    discussion_session_id: raw.discussion_session_id ?? null,
    groups: raw.groups ?? [],
    private_ballots: raw.private_ballots ?? {},
    note: raw.note,
  };
}

function emptyFounderReport(): FounderReport {
  return {
    schema_version: "v0.1",
    run_id: "test-run",
    product_brief: {},
    executive_summary: [],
    synthetic_society_size: 0,
    cohort_count: 0,
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

function buildContext(
  transcript: DiscussionTranscriptPayload,
): ReportContext {
  return {
    runId: "abcd1234-test-run",
    productName: "Test product",
    report: emptyFounderReport(),
    intent: null,
    cohorts: null,
    personas: null,
    discussion: null,
    transcript,
  };
}

describe("renderStructuredReport — Full Debate & Conversations section", () => {
  it("includes the Full Debate heading when transcript has groups", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    expect(html).toContain("Full debate &amp; conversations");
  });

  it("renders all 4 groups from the real PantryPulse transcript", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    // Group headings are emitted as 1-indexed for human display
    // (group_index 0 → "Group 1", etc.).
    expect(html).toContain("Group 1</strong>");
    expect(html).toContain("Group 2</strong>");
    expect(html).toContain("Group 3</strong>");
    expect(html).toContain("Group 4</strong>");
  });

  it("renders all 4 rounds per group with humanized round labels", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    expect(html).toContain("Round 1</strong>");
    expect(html).toContain("Round 2</strong>");
    expect(html).toContain("Round 3</strong>");
    expect(html).toContain("Round 4</strong>");
    expect(html).toContain("Public opening");
    expect(html).toContain("Challenge");
    expect(html).toContain("Peer response");
    expect(html).toContain("Proof discussion");
  });

  it("emits collapsible <details> elements with the first group/round open by default", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    // At least one details element should be open by default so the
    // user sees content immediately on opening the file.
    expect(html).toMatch(/<details[^>]*\bopen\b/);
    // ROUND_LABEL-mapped labels live inside <summary> tags.
    expect(html).toContain("<summary>");
    // Specific group/round classes ship with the section.
    expect(html).toContain('class="debate-group"');
    expect(html).toContain('class="debate-round"');
  });

  it("emits one <li class='debate-turn'> per public turn (4 groups × 4 rounds × 6 turns = 96)", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    const matches = html.match(/<li class="debate-turn">/g) ?? [];
    expect(matches.length).toBe(96);
  });

  it("renders speaker names and persona role for each turn", () => {
    const transcript = loadRealTranscript();
    const html = renderStructuredReport(buildContext(transcript));

    // Pull the first speaker name from the first turn of the first
    // group and assert it appears in the HTML.
    const firstTurn =
      transcript.groups[0]?.rounds?.[0]?.turns?.[0] ?? null;
    expect(firstTurn).not.toBeNull();
    if (firstTurn) {
      expect(html).toContain(firstTurn.speaker_name);
      // role wrapper class
      expect(html).toContain('class="debate-role"');
      // public_text class wrapper
      expect(html).toContain('class="debate-text"');
    }
  });

  it("includes the print-time script that force-opens all <details> on beforeprint", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    expect(html).toContain("beforeprint");
    expect(html).toContain("afterprint");
    expect(html).toContain("nodes[i].open = true");
  });

  it("escapes HTML special characters in speaker name and turn text", () => {
    const transcript: DiscussionTranscriptPayload = {
      run_id: "x",
      discussion_session_id: null,
      groups: [
        {
          group_index: 0,
          personas: [
            {
              persona_id: "p1",
              display_name: "Alice <script>",
              role: "ops_lead",
            },
          ],
          rounds: [
            {
              round_number: 1,
              round_label: "public_opening",
              turns: [
                {
                  turn_id: "t1",
                  turn_number: 1,
                  speaker_persona_id: "p1",
                  speaker_name: "Alice <script>alert('xss')</script>",
                  speaker_role: "ops_lead",
                  turn_type: "public",
                  stance: "skeptical",
                  public_text: "I think <b>this</b> is risky.",
                  referenced_turn_ids: [],
                },
              ],
            },
          ],
        } satisfies TranscriptGroup,
      ],
      private_ballots: {},
    };
    const html = renderStructuredReport(buildContext(transcript));
    // Raw "<script>" must NOT appear — must be escaped.
    expect(html).not.toContain("<script>alert");
    expect(html).toContain("&lt;script&gt;alert");
    // <b>this</b> must be escaped too.
    expect(html).toContain("&lt;b&gt;this&lt;/b&gt;");
  });

  it("does NOT crash and does NOT emit the section when transcript has zero groups", () => {
    const transcript: DiscussionTranscriptPayload = {
      run_id: "empty",
      discussion_session_id: null,
      groups: [],
      private_ballots: {},
    };
    const html = renderStructuredReport(buildContext(transcript));
    expect(html).not.toContain("Full debate &amp; conversations");
    // The rest of the report still renders.
    expect(html).toContain("[ASSEMBLY]".slice(1, 9)); // logo text
    expect(html).toContain("Caveats");
  });

  it("renders the round with a '(no turns recorded)' marker when a round has zero turns", () => {
    const transcript: DiscussionTranscriptPayload = {
      run_id: "x",
      discussion_session_id: null,
      groups: [
        {
          group_index: 0,
          personas: [
            { persona_id: "p1", display_name: "P1", role: "ops_lead" },
          ],
          rounds: [
            {
              round_number: 1,
              round_label: "public_opening",
              turns: [],
            },
          ],
        },
      ],
      private_ballots: {},
    };
    const html = renderStructuredReport(buildContext(transcript));
    expect(html).toContain("no turns recorded");
  });

  it("falls back to round_label slug if not in ROUND_LABEL map", () => {
    const transcript: DiscussionTranscriptPayload = {
      run_id: "x",
      discussion_session_id: null,
      groups: [
        {
          group_index: 0,
          personas: [
            { persona_id: "p1", display_name: "P1", role: "ops_lead" },
          ],
          rounds: [
            {
              round_number: 1,
              round_label: "some_unknown_label",
              turns: [
                {
                  turn_id: "t1",
                  turn_number: 1,
                  speaker_persona_id: "p1",
                  speaker_name: "P1",
                  speaker_role: "ops_lead",
                  turn_type: "public",
                  stance: null,
                  public_text: "hello",
                  referenced_turn_ids: [],
                },
              ],
            },
          ],
        },
      ],
      private_ballots: {},
    };
    const html = renderStructuredReport(buildContext(transcript));
    expect(html).toContain("some_unknown_label");
  });

  it("still produces a working report when discussion + intent + cohorts payloads are all null", () => {
    const html = renderStructuredReport(
      buildContext(loadRealTranscript()),
    );
    // Critical legacy sections still render.
    expect(html).toContain("Where the discussion landed");
    expect(html).toContain("Final consensus snapshot");
    expect(html).toContain("Caveats");
    // And the new section is included.
    expect(html).toContain("Full debate &amp; conversations");
  });
});
