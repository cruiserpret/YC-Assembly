// Phase 12+ — PDF download tests.
//
// Two-layer strategy:
//
//   1. Binary contract — render via @react-pdf/renderer and assert
//      the produced buffer is a real PDF (magic bytes, %%EOF, byte
//      range). Proves we ship a real .pdf, not HTML.
//
//   2. Content contract — walk the React Element tree produced by
//      PdfReportDocument and collect every string leaf. Assert
//      every required group / round / persona / section landed in
//      the tree. Faster than parsing the PDF and stable across
//      react-pdf font/layout changes.

import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import React from "react";

import { PdfReportDocument } from "@/components/PdfReportDocument";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
} from "@/lib/types";

vi.setConfig({ testTimeout: 30000 });

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

// ---------- Helpers ----------

interface DocBuildProps {
  transcript: DiscussionTranscriptPayload;
  report?: FounderReport;
}

function buildDocProps(
  transcript: DiscussionTranscriptPayload,
  report: FounderReport = emptyFounderReport(),
) {
  return {
    runId: "abcd1234-pdf-test-run",
    productName: "PantryPulse",
    report,
    intent: null,
    cohorts: null,
    personas: null,
    discussion: null,
    transcript,
    generatedAt: "2026-05-26 17:00 UTC",
  } as const;
}

function buildDocElement(
  transcript: DiscussionTranscriptPayload,
  report: FounderReport = emptyFounderReport(),
) {
  return React.createElement(
    PdfReportDocument,
    buildDocProps(transcript, report),
  );
}

// Render PdfReportDocument once (as a plain JS function call, which
// returns its JSX), then walk that tree collecting every string leaf.
// For react-pdf's built-in components (Document, Page, View, Text)
// we just recurse into children — we never try to "render" them
// because they're not plain functional components.
function collectAllText(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node))
    return node
      .map(collectAllText)
      .filter((s) => s.length > 0)
      .join(" ");
  const el = node as React.ReactElement<{
    children?: React.ReactNode;
  }>;
  if (!el || typeof el !== "object" || !("type" in el)) return "";
  return collectAllText(el.props?.children);
}

function collectDocText(
  transcript: DiscussionTranscriptPayload,
  report: FounderReport = emptyFounderReport(),
): string {
  // Invoke our component as a plain function so we get the raw JSX
  // it returns. Then walk the JSX tree and normalize whitespace so
  // substring assertions are stable across react-pdf node boundaries.
  const rendered = (
    PdfReportDocument as unknown as (
      props: Parameters<typeof PdfReportDocument>[0],
    ) => React.ReactElement
  )(buildDocProps(transcript, report));
  return collectAllText(rendered).replace(/\s+/g, " ");
}

async function renderPdfBuffer(
  transcript: DiscussionTranscriptPayload,
  report: FounderReport = emptyFounderReport(),
): Promise<Buffer> {
  const { pdf } = await import("@react-pdf/renderer");
  // PdfReportDocument returns a <Document> internally — cast for the
  // pdf() signature, which expects a Document element directly.
  const stream = await pdf(
    buildDocElement(transcript, report) as unknown as Parameters<
      typeof pdf
    >[0],
  ).toBuffer();
  const chunks: Buffer[] = [];
  for await (const chunk of stream as unknown as AsyncIterable<
    Buffer | string
  >) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks);
}

// ---------- Binary contract ----------

describe("PdfReportDocument — binary contract (real .pdf file)", () => {
  it("produces a real PDF starting with %PDF- magic bytes", async () => {
    const buffer = await renderPdfBuffer(loadRealTranscript());
    expect(buffer.slice(0, 5).toString("ascii")).toBe("%PDF-");
  });

  it("ends with the %%EOF marker", async () => {
    const buffer = await renderPdfBuffer(loadRealTranscript());
    expect(buffer.slice(-32).toString("ascii")).toContain("%%EOF");
  });

  it("file size is in a reasonable range for a 4×4 transcript", async () => {
    const buffer = await renderPdfBuffer(loadRealTranscript());
    expect(buffer.byteLength).toBeGreaterThan(10_000);
    expect(buffer.byteLength).toBeLessThan(2_000_000);
  });

  it("still produces a valid PDF when transcript has zero groups", async () => {
    const empty: DiscussionTranscriptPayload = {
      run_id: "empty",
      discussion_session_id: null,
      groups: [],
      private_ballots: {},
    };
    const buffer = await renderPdfBuffer(empty);
    expect(buffer.slice(0, 5).toString("ascii")).toBe("%PDF-");
    expect(buffer.byteLength).toBeGreaterThan(1_000);
  });
});

// ---------- Content contract (tree walk) ----------

describe("PdfReportDocument — content contract", () => {
  const tree = collectDocText(loadRealTranscript());

  it("includes the Full Debate & Conversations section heading", () => {
    expect(tree).toContain("Full debate & conversations");
  });

  it("includes all 4 group headings (1-indexed)", () => {
    expect(tree).toContain("Group 1");
    expect(tree).toContain("Group 2");
    expect(tree).toContain("Group 3");
    expect(tree).toContain("Group 4");
  });

  it("includes all 4 round labels in human form", () => {
    expect(tree).toContain("Public opening");
    expect(tree).toContain("Challenge");
    expect(tree).toContain("Peer response");
    expect(tree).toContain("Proof discussion");
  });

  it("includes every persona name from every group's first turn", () => {
    const transcript = loadRealTranscript();
    const names = transcript.groups
      .map((g) => g.rounds?.[0]?.turns?.[0]?.speaker_name)
      .filter((s): s is string => !!s);
    expect(names.length).toBeGreaterThan(0);
    for (const n of names) {
      expect(tree).toContain(n);
    }
  });

  it("emits every round number (1..4) for every group (4 groups × 4 rounds = 16)", () => {
    const counts = ["Round 1", "Round 2", "Round 3", "Round 4"].map((s) => ({
      label: s,
      count: (tree.match(new RegExp(`\\b${s}\\b`, "g")) ?? []).length,
    }));
    // Each (group, round) pair emits the head string once → 4×4 = 16.
    const total = counts.reduce((a, b) => a + b.count, 0);
    expect(total, JSON.stringify(counts)).toBeGreaterThanOrEqual(16);
  });

  it("emits at least one full public_text from each group", () => {
    const transcript = loadRealTranscript();
    for (const g of transcript.groups) {
      const firstTurn = g.rounds?.[0]?.turns?.[0];
      expect(firstTurn).toBeDefined();
      if (firstTurn?.public_text) {
        // Take the first 6 verbatim words so the assertion is a true
        // substring of the original turn text.
        const sample = firstTurn.public_text
          .split(/\s+/)
          .slice(0, 6)
          .join(" ");
        expect(tree).toContain(sample);
      }
    }
  });

  it("renders the report's legacy sections alongside the debate", () => {
    expect(tree).toContain("Where the discussion landed");
    expect(tree).toContain("Final consensus snapshot");
    expect(tree).toContain("Caveats");
    expect(tree).toContain("ASSEMBLY");
  });

  it("uses humanized stance labels when a turn carries a stance", () => {
    const transcript = loadRealTranscript();
    const hasStance = transcript.groups.some((g) =>
      g.rounds.some((r) => r.turns.some((t) => t.stance)),
    );
    if (!hasStance) return;
    const found = [
      "Curious but unconvinced",
      "Interested if proven",
      "Skeptical",
      "Likely to reject",
      "Needs more information",
    ].some((s) => tree.includes(s));
    expect(found).toBe(true);
  });

  it("falls back to the round_label slug when not in ROUND_LABEL map", () => {
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
                  public_text: "hello world",
                  referenced_turn_ids: [],
                },
              ],
            },
          ],
        },
      ],
      private_ballots: {},
    };
    const text = collectDocText(transcript);
    expect(text).toContain("some_unknown_label");
  });

  it("marks empty rounds with '(no turns recorded)'", () => {
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
    const text = collectDocText(transcript);
    expect(text).toContain("no turns recorded");
  });

  it("skips the debate page entirely when transcript has zero groups, but still renders body + caveats", () => {
    const transcript: DiscussionTranscriptPayload = {
      run_id: "empty",
      discussion_session_id: null,
      groups: [],
      private_ballots: {},
    };
    const text = collectDocText(transcript);
    expect(text).not.toContain("Full debate & conversations");
    expect(text).toContain("Caveats");
    expect(text).toContain("ASSEMBLY");
  });

  it("renders default caveats list when report.caveats is empty", () => {
    const text = collectDocText(loadRealTranscript());
    expect(text).toContain("Synthetic simulation");
    expect(text).toContain("not a real-world forecast");
  });
});
