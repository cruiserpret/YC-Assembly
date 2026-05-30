// Phase 12+ — Real PDF report document, generated client-side with
// @react-pdf/renderer. Produces a one-click downloadable .pdf file
// that:
//
//   1. Carries the same content as the HTML in-depth report
//      (consensus snapshot, intent, objections, proof needs, role
//      breakdown, public/private stance, group discussion summary,
//      caveats), AND
//   2. Includes the full debate transcript in an expanded, print-
//      friendly format — every group, every round, every persona
//      turn, with speaker name, persona role, stance bucket, and
//      full public_text. No collapsed sections.
//
// This file deliberately stays a pure presentational layer over
// react-pdf primitives so it can be unit-tested by snapshotting the
// produced PDF blob byte length, or by mounting the document
// declaratively without rendering to PDF.
//
// We keep the visual style close to the sample report page: dark
// header band, white body, group cards, round subheadings, turn
// cards with speaker + role + stance + text. The PDF print is
// always laid out for letter/A4 paper.

import * as React from "react";
import {
  Document,
  Page,
  Text,
  View,
  StyleSheet,
  Font,
} from "@react-pdf/renderer";

import { humanizeRole, humanizeStance } from "@/lib/labels";
import {
  filterApplicableObjectionBuckets,
  filterApplicableProofBuckets,
  objectionSentence,
  proofSentence,
} from "@/lib/buckets";
import { bucketStance } from "@/lib/stance";
import type {
  CohortsPayload,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  LightweightVotersPayload,
  PersonasPayload,
} from "@/lib/types";

// Human label for each round-type — same map used in the HTML
// renderer and the on-site sample report.
const ROUND_LABEL: Record<string, string> = {
  public_opening: "Public opening",
  challenge: "Challenge",
  peer_response: "Peer response",
  proof_discussion: "Proof discussion",
};

// react-pdf uses system fonts by default. We register Inter only if
// the bundler has it available; otherwise the default Helvetica is
// fine for the report. Keeping this minimal to avoid network fetches
// inside the client at button-click time (which would slow the
// download).
try {
  // Intentionally a no-op when no Inter binary is bundled. We avoid
  // an absolute font URL because that would require an external
  // fetch from inside the user's browser at PDF-build time, and the
  // user wants a one-click download with no flakiness.
  // eslint-disable-next-line @typescript-eslint/no-unused-expressions
  Font;
} catch {
  // ignore
}

const colors = {
  bg: "#FFFFFF",
  surface: "#F5F5F5",
  surfaceElevated: "#FAFAFA",
  border: "#DDDDDD",
  borderStrong: "#888888",
  text: "#1A1A1A",
  muted: "#555555",
  accent: "#5A8A00",
  danger: "#B03030",
  black: "#000000",
};

const styles = StyleSheet.create({
  page: {
    backgroundColor: colors.bg,
    padding: 36,
    fontSize: 10,
    lineHeight: 1.45,
    color: colors.text,
    fontFamily: "Helvetica",
  },
  // Logo banner
  banner: {
    borderBottom: `1pt solid ${colors.border}`,
    paddingBottom: 18,
    marginBottom: 14,
    alignItems: "center",
  },
  bannerWordmark: {
    fontFamily: "Helvetica-Bold",
    fontSize: 32,
    letterSpacing: 1,
    color: colors.black,
    textAlign: "center",
  },
  bannerAccent: {
    color: colors.accent,
  },
  bannerTag: {
    fontSize: 8,
    letterSpacing: 3,
    color: colors.muted,
    marginTop: 6,
    textTransform: "uppercase",
    textAlign: "center",
  },
  // Meta
  meta: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    fontSize: 8,
    color: colors.muted,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    paddingBottom: 8,
    borderBottom: `1pt solid ${colors.border}`,
    marginBottom: 14,
  },
  metaItem: {
    marginRight: 14,
  },
  metaStrong: {
    color: colors.accent,
    fontFamily: "Helvetica-Bold",
  },
  // Caveat banner
  caveatBanner: {
    border: `1pt solid ${colors.border}`,
    borderLeft: `3pt solid ${colors.accent}`,
    backgroundColor: colors.surface,
    padding: 10,
    marginBottom: 18,
    fontSize: 9,
  },
  // Section
  section: {
    marginTop: 14,
    marginBottom: 8,
  },
  h2: {
    fontFamily: "Helvetica-Bold",
    fontSize: 14,
    color: colors.black,
    paddingBottom: 4,
    borderBottom: `1pt solid ${colors.border}`,
    marginBottom: 8,
  },
  h3: {
    fontFamily: "Helvetica-Bold",
    fontSize: 9,
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 8,
    marginBottom: 4,
  },
  paragraph: {
    fontSize: 10,
    color: colors.text,
    marginBottom: 6,
  },
  caption: {
    fontSize: 8,
    color: colors.muted,
    marginBottom: 6,
  },
  blockquote: {
    borderLeft: `3pt solid ${colors.accent}`,
    backgroundColor: colors.surface,
    paddingTop: 8,
    paddingBottom: 8,
    paddingLeft: 12,
    paddingRight: 12,
    fontSize: 10,
    marginBottom: 6,
  },
  // Metric tiles
  metricRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 8,
  },
  metricTile: {
    flexGrow: 1,
    flexBasis: 0,
    border: `1pt solid ${colors.border}`,
    backgroundColor: colors.surfaceElevated,
    padding: 8,
    borderRadius: 4,
  },
  metricValue: {
    fontFamily: "Helvetica-Bold",
    fontSize: 18,
    color: colors.black,
  },
  metricValueAccent: {
    fontFamily: "Helvetica-Bold",
    fontSize: 18,
    color: colors.accent,
  },
  metricValueDanger: {
    fontFamily: "Helvetica-Bold",
    fontSize: 18,
    color: colors.danger,
  },
  metricValueMuted: {
    fontFamily: "Helvetica-Bold",
    fontSize: 18,
    color: colors.muted,
  },
  metricLabel: {
    fontSize: 7,
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 2,
  },
  // Numbered list
  numberedItem: {
    flexDirection: "row",
    border: `1pt solid ${colors.border}`,
    backgroundColor: colors.surfaceElevated,
    padding: 8,
    marginBottom: 4,
    borderRadius: 4,
  },
  numberedBadge: {
    width: 16,
    height: 16,
    borderRadius: 8,
    border: `1pt solid ${colors.border}`,
    color: colors.accent,
    fontSize: 8,
    textAlign: "center",
    marginRight: 8,
    fontFamily: "Helvetica-Bold",
  },
  numberedText: {
    flex: 1,
    fontSize: 9.5,
    color: colors.text,
  },
  // Table
  tableRow: {
    flexDirection: "row",
    borderBottom: `1pt solid ${colors.border}`,
    paddingTop: 4,
    paddingBottom: 4,
  },
  tableCellLeft: {
    flex: 2,
    fontSize: 9.5,
  },
  tableCellNum: {
    flex: 1,
    fontSize: 9.5,
    textAlign: "right",
    fontFamily: "Helvetica",
  },
  tableHead: {
    fontSize: 7,
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    fontFamily: "Helvetica-Bold",
  },
  // Debate
  debateGroup: {
    border: `1pt solid ${colors.border}`,
    borderRadius: 4,
    padding: 10,
    marginBottom: 8,
    backgroundColor: colors.surface,
  },
  debateGroupHead: {
    fontFamily: "Helvetica-Bold",
    fontSize: 12,
    color: colors.black,
    marginBottom: 6,
  },
  debateGroupSubhead: {
    fontSize: 8,
    color: colors.muted,
    marginBottom: 4,
  },
  debateRound: {
    marginTop: 6,
    paddingTop: 4,
    borderTop: `1pt solid ${colors.border}`,
  },
  debateRoundHead: {
    fontFamily: "Helvetica-Bold",
    fontSize: 10,
    color: colors.accent,
    marginBottom: 4,
  },
  debateTurn: {
    paddingTop: 4,
    paddingBottom: 4,
    borderTop: `1pt solid ${colors.border}`,
  },
  debateTurnFirst: {
    paddingTop: 0,
    borderTop: "none",
  },
  debateTurnHead: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 2,
  },
  debateSpeaker: {
    fontFamily: "Helvetica-Bold",
    fontSize: 9.5,
    color: colors.black,
  },
  debateRole: {
    fontSize: 7,
    color: colors.muted,
    textTransform: "uppercase",
    marginLeft: 6,
  },
  debateStance: {
    fontSize: 7,
    color: colors.accent,
    textTransform: "uppercase",
    border: `1pt solid ${colors.border}`,
    borderRadius: 8,
    paddingLeft: 4,
    paddingRight: 4,
    marginLeft: 6,
  },
  debateText: {
    fontSize: 9.5,
    color: colors.text,
    marginTop: 2,
    lineHeight: 1.45,
  },
  // Footer
  footer: {
    position: "absolute",
    bottom: 18,
    left: 36,
    right: 36,
    fontSize: 7,
    color: colors.muted,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    borderTop: `1pt solid ${colors.border}`,
    paddingTop: 6,
  },
  pageNumber: {
    position: "absolute",
    bottom: 18,
    right: 36,
    fontSize: 7,
    color: colors.muted,
  },
  // Caveat list
  caveatListItem: {
    fontSize: 9,
    color: colors.text,
    marginBottom: 3,
  },
});

function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

function deriveBucketCounts(
  transcript: DiscussionTranscriptPayload,
): { for: number; against: number; neutral: number; total: number } {
  let f = 0;
  let a = 0;
  let n = 0;
  for (const [, b] of Object.entries(transcript.private_ballots)) {
    const stance = b.final?.stance ?? b.reflection?.stance ?? null;
    if (!stance) continue;
    const bucket = bucketStance(stance);
    if (bucket === "for") f += 1;
    else if (bucket === "against") a += 1;
    else n += 1;
  }
  return { for: f, against: a, neutral: n, total: f + a + n };
}

function deriveShiftCounts(transcript: DiscussionTranscriptPayload): {
  shifted: number;
  held: number;
  scored: number;
} {
  let shifted = 0;
  let held = 0;
  let scored = 0;
  for (const [, b] of Object.entries(transcript.private_ballots)) {
    const pre = b.pre?.stance ?? null;
    const final = b.final?.stance ?? b.reflection?.stance ?? null;
    if (!pre || !final) continue;
    scored += 1;
    if (bucketStance(pre) !== bucketStance(final)) shifted += 1;
    else held += 1;
  }
  return { shifted, held, scored };
}

function deriveRoleBreakdown(
  transcript: DiscussionTranscriptPayload,
): Array<{ display: string; count: number; for: number; against: number; neutral: number }> {
  const stanceByPid: Record<string, string | null> = {};
  for (const [pid, b] of Object.entries(transcript.private_ballots)) {
    stanceByPid[pid] =
      b.final?.stance ?? b.reflection?.stance ?? b.pre?.stance ?? null;
  }
  const seen = new Set<string>();
  const byRole = new Map<
    string,
    { display: string; count: number; for: number; against: number; neutral: number }
  >();
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      if (seen.has(p.persona_id)) continue;
      seen.add(p.persona_id);
      let entry = byRole.get(p.role);
      if (!entry) {
        entry = {
          display: humanizeRole(p.role),
          count: 0,
          for: 0,
          against: 0,
          neutral: 0,
        };
        byRole.set(p.role, entry);
      }
      entry.count += 1;
      const bucket = bucketStance(stanceByPid[p.persona_id]);
      if (bucket === "for") entry.for += 1;
      else if (bucket === "against") entry.against += 1;
      else entry.neutral += 1;
    }
  }
  return [...byRole.values()].sort((a, b) => b.count - a.count);
}

function synthesizeTrajectory(stats: {
  for: number;
  against: number;
  neutral: number;
  total: number;
  shifted: number;
  scored: number;
}): string {
  const { total, shifted, scored } = stats;
  if (total === 0) {
    return "The synthetic society finished with no recorded final stances for this run.";
  }
  const forPct = stats.for / total;
  const againstPct = stats.against / total;
  const neutralPct = stats.neutral / total;
  const shiftPct = scored > 0 ? shifted / scored : 0;
  let lean: string;
  if (forPct >= 0.6) {
    lean = `${pct(forPct)} of the synthetic society finished receptive by the end`;
  } else if (forPct >= 0.45 && forPct > againstPct) {
    lean = `the synthetic society leaned receptive, with ${stats.for} of ${total} personas finishing supportive`;
  } else if (againstPct >= 0.4) {
    lean = `the synthetic society leaned skeptical, with ${stats.against} of ${total} personas resisting`;
  } else if (neutralPct >= 0.5) {
    lean = `most of the synthetic society stayed uncertain — ${stats.neutral} of ${total} personas finished still curious or wanting more information`;
  } else {
    lean = `the room split — ${stats.for} receptive, ${stats.neutral} uncertain, ${stats.against} resistant`;
  }
  let shiftPhrase: string;
  if (scored === 0) {
    shiftPhrase = "stance shift was not measurable on this run";
  } else if (shiftPct >= 0.4) {
    shiftPhrase = `the discussion materially moved ${shifted} personas (${pct(shiftPct)} shift rate)`;
  } else if (shiftPct >= 0.15) {
    shiftPhrase = `${shifted} personas shifted position during the discussion (${pct(shiftPct)} shift rate)`;
  } else if (shiftPct > 0) {
    shiftPhrase = `stances mostly held — only ${shifted} personas shifted (${pct(shiftPct)} shift rate)`;
  } else {
    shiftPhrase = "no personas changed bucket during the discussion";
  }
  return `${lean.charAt(0).toUpperCase()}${lean.slice(1)}; ${shiftPhrase}.`;
}

// Phase 14A — 100-voter influence overlay PDF section. Compact
// summary: 4-bucket distribution table + influence-rounds table +
// "debate agents talk; voters absorb and spread" copy block. No
// sankey, no per-voter dots — keeps the PDF reliable in
// @react-pdf/renderer's layout engine.
function renderPdfVoterUnavailableNotice(
  voters: LightweightVotersPayload | null,
): React.ReactElement {
  const reason =
    voters && typeof voters.reason === "string"
      ? voters.reason
      : "Voter artifact was not available at the time the report was downloaded.";
  return (
    <View style={styles.section}>
      <Text style={styles.h2}>100-voter influence layer</Text>
      <Text style={styles.caption}>
        The 100-voter influence layer is not available in this
        downloaded report. New simulations include the 100-voter
        graph automatically. The rest of the report below is
        unaffected.
      </Text>
      <Text style={[styles.caption, { fontFamily: "Helvetica-Oblique" }]}>
        {reason}
      </Text>
    </View>
  );
}

function renderPdfVoterSection(
  voters: LightweightVotersPayload,
): React.ReactElement {
  const dist = voters.final_distribution ?? null;
  const voterCount = voters.voters_count ?? dist?.n_voters ?? 100;
  const cal = voters.calibrated_distribution ?? null;
  const rounds = (voters.influence_rounds ?? [])
    .slice()
    .sort((a, b) => a.round_idx - b.round_idx);
  const totalShifts = rounds.reduce(
    (acc, r) => acc + (r.bucket_changes ?? 0), 0,
  );
  const bucketLabels: Array<[
    "buyer" | "receptive" | "uncertain" | "skeptical",
    string,
  ]> = [
    ["buyer", "Buyer"],
    ["receptive", "Receptive"],
    ["uncertain", "Uncertain"],
    ["skeptical", "Skeptical"],
  ];
  return (
    <View style={styles.section} break>
      <Text style={styles.h2}>100-voter influence layer</Text>
      <Text style={styles.caption}>
        A larger simulated sample that absorbs and spreads the debate
        signal. The deep agents above generated the arguments; the{" "}
        {voterCount} voters react to those arguments and propagate
        them through a 4-round influence network. No new LLM calls
        per voter; no free-text generation.
      </Text>
      {dist ? (
        <View>
          <Text style={styles.h3}>Voter distribution (4 buckets)</Text>
          {bucketLabels.map(([k, label]) => {
            const v = Number((dist as unknown as Record<string, unknown>)[k] ?? 0);
            const count = Math.round((v / 100) * voterCount);
            return (
              <View key={k} style={styles.tableRow}>
                <Text style={styles.tableCellLeft}>{label}</Text>
                <Text style={styles.tableCellNum}>
                  {count}/{voterCount} ({Math.round(v)}%)
                </Text>
              </View>
            );
          })}
        </View>
      ) : null}
      {/* Compact stats row */}
      <View style={styles.metricRow}>
        <View style={styles.metricTile}>
          <Text style={styles.metricValueAccent}>{voterCount}</Text>
          <Text style={styles.metricLabel}>Voters</Text>
        </View>
        <View style={styles.metricTile}>
          <Text style={styles.metricValueAccent}>{totalShifts}</Text>
          <Text style={styles.metricLabel}>Bucket shifts (4 rounds)</Text>
        </View>
        <View style={styles.metricTile}>
          <Text style={styles.metricValueAccent}>
            {cal && typeof cal.confidence_band_pp === "number"
              ? `±${Math.round(cal.confidence_band_pp)} pp`
              : "—"}
          </Text>
          <Text style={styles.metricLabel}>Confidence band</Text>
        </View>
      </View>
      {/* Influence dynamics — simple per-round table */}
      {rounds.length > 0 ? (
        <View>
          <Text style={styles.h3}>Influence dynamics across 4 rounds</Text>
          <View style={styles.tableRow}>
            <Text style={[styles.tableCellLeft, styles.tableHead]}>
              Round
            </Text>
            <Text style={[styles.tableCellNum, styles.tableHead]}>
              Buyer
            </Text>
            <Text style={[styles.tableCellNum, styles.tableHead]}>
              Receptive
            </Text>
            <Text style={[styles.tableCellNum, styles.tableHead]}>
              Uncertain
            </Text>
            <Text style={[styles.tableCellNum, styles.tableHead]}>
              Skeptical
            </Text>
            <Text style={[styles.tableCellNum, styles.tableHead]}>
              Shifts
            </Text>
          </View>
          {rounds.map((r) => {
            const bd = (r.bucket_distribution ?? {}) as Record<string, number>;
            return (
              <View key={r.round_idx} style={styles.tableRow}>
                <Text style={styles.tableCellLeft}>Round {r.round_idx}</Text>
                <Text style={styles.tableCellNum}>{bd.buyer ?? 0}</Text>
                <Text style={styles.tableCellNum}>{bd.receptive ?? 0}</Text>
                <Text style={styles.tableCellNum}>{bd.uncertain ?? 0}</Text>
                <Text style={styles.tableCellNum}>{bd.skeptical ?? 0}</Text>
                <Text style={styles.tableCellNum}>
                  {r.intent_changes ?? 0}
                </Text>
              </View>
            );
          })}
          <Text style={[styles.caption, { marginTop: 6 }]}>
            Round 0 = baseline; rounds 1–3 propagate the debate
            arguments through the voter network.
          </Text>
        </View>
      ) : null}
      <View style={[styles.blockquote, { marginTop: 10 }]}>
        <Text>
          <Text style={{ fontFamily: "Helvetica-Bold" }}>
            How the 100 voters work.{" "}
          </Text>
          The personas in the debate transcript are the ones doing
          the talking — they argue, push back, and revise their views
          across 4 groups and 4 rounds. The 100 voters are a larger
          simulated sample drawn from the same evidence and cohorts.
          They do not write new messages. Instead, they react to the
          arguments the debate agents made and propagate those
          arguments through a 100-voter influence network over 4
          rounds. In short: debate agents talk; voters absorb and
          spread.
        </Text>
      </View>
    </View>
  );
}

export interface PdfReportProps {
  runId: string;
  productName: string;
  report: FounderReport;
  intent: IntentPayload | null;
  cohorts: CohortsPayload | null;
  personas: PersonasPayload | null;
  discussion: DiscussionPayload | null;
  transcript: DiscussionTranscriptPayload;
  voters?: LightweightVotersPayload | null;
  generatedAt?: string;
}

export function PdfReportDocument({
  runId,
  productName,
  report,
  intent,
  // cohorts and personas are not currently surfaced in the PDF body —
  // they're already represented inside the transcript+report data we
  // do render. Kept in the props surface so the call site stays
  // symmetric with the HTML download.
  cohorts: _cohorts,
  personas: _personas,
  discussion,
  transcript,
  voters,
  generatedAt,
}: PdfReportProps) {
  const generatedAtStr = generatedAt ?? new Date().toLocaleString();

  const buckets = deriveBucketCounts(transcript);
  const shifts = deriveShiftCounts(transcript);
  const roleBreakdown = deriveRoleBreakdown(transcript);
  const trajectory = synthesizeTrajectory({
    ...buckets,
    shifted: shifts.shifted,
    scored: shifts.scored,
  });

  const intentDist = intent?.intent_distribution ?? {};
  const intentRows = Object.entries(intentDist)
    .filter(([, v]) => (v as number) > 0)
    .sort(([, a], [, b]) => (b as number) - (a as number));

  // Phase 14B — filter physical-product-only buckets on software/digital
  // briefs unless weighted_score is strong-signal high.
  const objections = filterApplicableObjectionBuckets(
    (report.top_objections || [])
      .slice()
      .sort((a, b) => (b.weighted_score ?? 0) - (a.weighted_score ?? 0)),
    report.product_brief,
  ).slice(0, 6);
  const proofs = filterApplicableProofBuckets(
    (report.proof_needed || [])
      .slice()
      .sort((a, b) => (b.weighted_score ?? 0) - (a.weighted_score ?? 0)),
    report.product_brief,
  ).slice(0, 6);

  const shiftSummary = report.public_private_shift_summary;
  const turns = discussion?.public_turn_count ?? 0;
  const personaCount = discussion?.persona_count ?? buckets.total;
  const ballotsByStage = discussion?.ballot_count_by_stage ?? {};
  const groupCount = discussion?.group_count ?? transcript.groups.length;

  // Role lookup for the debate section.
  const roleByPersona: Record<string, string> = {};
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      roleByPersona[p.persona_id] = p.role;
    }
  }

  const caveats =
    report.caveats && report.caveats.length > 0
      ? report.caveats
      : [
          "Synthetic simulation — not a real-world forecast.",
          "Cohorts are run-scoped + brief-scoped — never global market segments.",
          "Simulated intent labels are NOT real-world purchase forecasts.",
          "Personas have not bought, used, owned, or reviewed the unlaunched product.",
        ];

  const sortedGroups = transcript.groups
    .slice()
    .sort((a, b) => a.group_index - b.group_index);

  return (
    <Document
      title={`Assembly · ${productName} · in-depth report`}
      author="Assembly"
      subject="Synthetic-society simulation report"
    >
      {/* ====== PAGE 1+ : main body ====== */}
      <Page size="LETTER" style={styles.page}>
        <View style={styles.banner}>
          <Text style={styles.bannerWordmark}>
            <Text style={styles.bannerAccent}>[ </Text>
            ASSEMBLY
            <Text style={styles.bannerAccent}> ]</Text>
          </Text>
          <Text style={styles.bannerTag}>
            Synthetic-society simulation lab
          </Text>
        </View>

        <View style={styles.meta}>
          <Text style={styles.metaItem}>
            Product: <Text style={styles.metaStrong}>{productName}</Text>
          </Text>
          <Text style={styles.metaItem}>Run: {runId}</Text>
          <Text style={styles.metaItem}>Generated: {generatedAtStr}</Text>
        </View>

        {/* 1. Where the discussion landed */}
        <View style={styles.section}>
          <Text style={styles.h2}>Where the discussion landed</Text>
          <View style={styles.blockquote}>
            <Text>{trajectory}</Text>
          </View>
        </View>

        {/* 2. Final consensus snapshot */}
        <View style={styles.section}>
          <Text style={styles.h2}>Final consensus snapshot</Text>
          <View style={styles.metricRow}>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueAccent}>{buckets.for}</Text>
              <Text style={styles.metricLabel}>Receptive</Text>
            </View>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueMuted}>{buckets.neutral}</Text>
              <Text style={styles.metricLabel}>Uncertain</Text>
            </View>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueDanger}>{buckets.against}</Text>
              <Text style={styles.metricLabel}>Resistant</Text>
            </View>
          </View>
          <View style={styles.metricRow}>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueAccent}>{shifts.shifted}</Text>
              <Text style={styles.metricLabel}>Agents shifted</Text>
            </View>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueMuted}>{shifts.held}</Text>
              <Text style={styles.metricLabel}>Agents held</Text>
            </View>
            <View style={styles.metricTile}>
              <Text style={styles.metricValueAccent}>
                {pct(shifts.scored > 0 ? shifts.shifted / shifts.scored : 0)}
              </Text>
              <Text style={styles.metricLabel}>Opinion shift rate</Text>
            </View>
          </View>
        </View>

        {/* 3. Synthetic intent */}
        {intentRows.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.h2}>Synthetic intent snapshot</Text>
            <Text style={styles.caption}>
              Synthetic expressed intent inside this run — not real-world
              purchase behavior. n = {personaCount} run-scoped personas.
            </Text>
            {intentRows.map(([k, v]) => (
              <View key={k} style={styles.tableRow}>
                <Text style={styles.tableCellLeft}>{humanizeStance(k)}</Text>
                <Text style={styles.tableCellNum}>{v}</Text>
              </View>
            ))}
          </View>
        )}

        {/* 3b. Phase 14A — 100-voter influence overlay. Always emits
            SOMETHING — either the full panel when payload is
            available, or a visible unavailable notice — so the PDF
            never silently drops the feature (which is what hid the
            ShelfSense AI voter section in the previous version). */}
        {voters && voters.voter_overlay_available
          ? renderPdfVoterSection(voters)
          : renderPdfVoterUnavailableNotice(voters ?? null)}

        {/* 4. Objections */}
        {objections.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.h2}>What this society pushed back on</Text>
            <Text style={styles.caption}>
              Synthetic objections, ordered by how often they came up.
            </Text>
            {objections.map((o, i) => (
              <View key={i} style={styles.numberedItem}>
                <Text style={styles.numberedBadge}>{i + 1}</Text>
                <Text style={styles.numberedText}>
                  {objectionSentence(o.bucket, report.product_brief)}
                </Text>
              </View>
            ))}
          </View>
        )}

        {/* 5. Proof needs */}
        {proofs.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.h2}>What would change their minds</Text>
            <Text style={styles.caption}>
              Synthetic proof needs, ordered by how much they&apos;d shift
              the room.
            </Text>
            {proofs.map((p, i) => (
              <View key={i} style={styles.numberedItem}>
                <Text style={styles.numberedBadge}>{i + 1}</Text>
                <Text style={styles.numberedText}>
                  {proofSentence(p.bucket)}
                </Text>
              </View>
            ))}
          </View>
        )}

        {/* 6. Role breakdown */}
        {roleBreakdown.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.h2}>Who&apos;s in this synthetic society</Text>
            <Text style={styles.caption}>
              Role makeup of the run-scoped, evidence-anchored persona
              set. Each row shows the role&apos;s count and how that role
              finished by stance bucket.
            </Text>
            <View style={styles.tableRow}>
              <Text style={[styles.tableCellLeft, styles.tableHead]}>Role</Text>
              <Text style={[styles.tableCellNum, styles.tableHead]}>Count</Text>
              <Text style={[styles.tableCellNum, styles.tableHead]}>Receptive</Text>
              <Text style={[styles.tableCellNum, styles.tableHead]}>Uncertain</Text>
              <Text style={[styles.tableCellNum, styles.tableHead]}>Resistant</Text>
            </View>
            {roleBreakdown.map((r) => (
              <View key={r.display} style={styles.tableRow}>
                <Text style={styles.tableCellLeft}>{r.display}</Text>
                <Text style={styles.tableCellNum}>{r.count}</Text>
                <Text style={[styles.tableCellNum, { color: colors.accent }]}>
                  {r.for}
                </Text>
                <Text style={[styles.tableCellNum, { color: colors.muted }]}>
                  {r.neutral}
                </Text>
                <Text style={[styles.tableCellNum, { color: colors.danger }]}>
                  {r.against}
                </Text>
              </View>
            ))}
          </View>
        )}

        {/* 7. Public ↔ private stance */}
        {shiftSummary &&
          (Object.keys(shiftSummary.pre_stance_distribution || {}).length > 0 ||
            Object.keys(shiftSummary.final_stance_distribution || {}).length >
              0) && (
            <View style={styles.section}>
              <Text style={styles.h2}>Public vs private stance</Text>
              <Text style={styles.h3}>Pre-discussion</Text>
              {Object.entries(shiftSummary.pre_stance_distribution || {})
                .filter(([, v]) => v > 0)
                .sort(([, a], [, b]) => b - a)
                .map(([k, v]) => (
                  <View key={`pre-${k}`} style={styles.tableRow}>
                    <Text style={styles.tableCellLeft}>{humanizeStance(k)}</Text>
                    <Text style={styles.tableCellNum}>{v}</Text>
                  </View>
                ))}
              <Text style={styles.h3}>Final</Text>
              {Object.entries(shiftSummary.final_stance_distribution || {})
                .filter(([, v]) => v > 0)
                .sort(([, a], [, b]) => b - a)
                .map(([k, v]) => (
                  <View key={`fin-${k}`} style={styles.tableRow}>
                    <Text style={styles.tableCellLeft}>{humanizeStance(k)}</Text>
                    <Text style={styles.tableCellNum}>{v}</Text>
                  </View>
                ))}
            </View>
          )}

        {/* 8. Discussion summary */}
        {(turns > 0 || personaCount > 0) && (
          <View style={styles.section}>
            <Text style={styles.h2}>Group discussion summary</Text>
            <Text style={styles.caption}>
              Synthetic{" "}
              {(() => {
                const r = Math.max(
                  0,
                  ...(transcript.groups ?? []).map((g) =>
                    (g.rounds ?? []).length,
                  ),
                );
                return r > 0 ? r : "multi";
              })()}
              -round discussion across {groupCount} group
              {groupCount === 1 ? "" : "s"} — not a recording of real
              customers.
            </Text>
            <View style={styles.metricRow}>
              <View style={styles.metricTile}>
                <Text style={styles.metricValueAccent}>{personaCount}</Text>
                <Text style={styles.metricLabel}>Personas</Text>
              </View>
              <View style={styles.metricTile}>
                <Text style={styles.metricValueAccent}>{turns}</Text>
                <Text style={styles.metricLabel}>Public turns</Text>
              </View>
              <View style={styles.metricTile}>
                <Text style={styles.metricValueAccent}>
                  {ballotsByStage.final ?? 0}
                </Text>
                <Text style={styles.metricLabel}>Final ballots</Text>
              </View>
            </View>
            {personaCount > 0 &&
            typeof ballotsByStage.final === "number" &&
            ballotsByStage.final < personaCount ? (
              <Text style={styles.caption}>
                {personaCount - (ballotsByStage.final ?? 0)} of{" "}
                {personaCount} personas did not complete a final
                ballot during the run. Their pre-discussion stance
                is still factored into the consensus snapshot, which
                is why the consensus totals may exceed the final-
                ballot count.
              </Text>
            ) : null}
          </View>
        )}

        {/* Footer + page number */}
        <Text
          style={styles.footer}
          render={({ pageNumber, totalPages }) =>
            `Assembly · synthetic-society simulation lab · run ${runId} · generated ${generatedAtStr} · page ${pageNumber} of ${totalPages}`
          }
          fixed
        />
      </Page>

      {/* ====== FULL DEBATE PAGES (auto-paginated) ====== */}
      {sortedGroups.length > 0 && (
        <Page size="LETTER" style={styles.page} wrap>
          <View style={styles.section}>
            <Text style={styles.h2}>Full debate & conversations</Text>
            <Text style={styles.caption}>
              Every group, every round, every public turn from the
              synthetic discussion — {sortedGroups.length} group
              {sortedGroups.length === 1 ? "" : "s"},{" "}
              {Object.keys(roleByPersona).length} persona
              {Object.keys(roleByPersona).length === 1 ? "" : "s"},{" "}
              {sortedGroups.reduce(
                (acc, g) =>
                  acc +
                  g.rounds.reduce(
                    (rAcc, r) => rAcc + (r.turns?.length ?? 0),
                    0,
                  ),
                0,
              )}{" "}
              turn(s) in total. All sections are fully expanded for the
              PDF.
            </Text>
          </View>
          {sortedGroups.map((group) => {
            const sortedRounds = group.rounds
              .slice()
              .sort((a, b) => a.round_number - b.round_number);
            return (
              <View
                key={`g-${group.group_index}`}
                style={styles.debateGroup}
                wrap
              >
                <Text style={styles.debateGroupHead}>
                  Group {group.group_index + 1}
                </Text>
                <Text style={styles.debateGroupSubhead}>
                  {group.personas.length} persona
                  {group.personas.length === 1 ? "" : "s"} ·{" "}
                  {sortedRounds.length} round
                  {sortedRounds.length === 1 ? "" : "s"}
                </Text>
                {sortedRounds.map((round) => {
                  const sortedTurns = (round.turns ?? [])
                    .slice()
                    .sort((a, b) => a.turn_number - b.turn_number);
                  return (
                    <View
                      key={`r-${group.group_index}-${round.round_number}`}
                      style={styles.debateRound}
                      wrap
                    >
                      <Text style={styles.debateRoundHead}>
                        Round {round.round_number} —{" "}
                        {ROUND_LABEL[round.round_label] || round.round_label}
                        {sortedTurns.length === 0
                          ? " (no turns recorded)"
                          : ` (${sortedTurns.length} turn${
                              sortedTurns.length === 1 ? "" : "s"
                            })`}
                      </Text>
                      {sortedTurns.map((turn, idx) => {
                        const role =
                          roleByPersona[turn.speaker_persona_id] ??
                          turn.speaker_role ??
                          "";
                        return (
                          <View
                            key={`t-${turn.turn_id}-${idx}`}
                            style={[
                              styles.debateTurn,
                              idx === 0 ? styles.debateTurnFirst : {},
                            ]}
                            wrap={false}
                          >
                            <View style={styles.debateTurnHead}>
                              <Text style={styles.debateSpeaker}>
                                {turn.speaker_name || "Unknown speaker"}
                              </Text>
                              <Text style={styles.debateRole}>
                                {humanizeRole(role)}
                              </Text>
                              {turn.stance && (
                                <Text style={styles.debateStance}>
                                  {humanizeStance(turn.stance)}
                                </Text>
                              )}
                            </View>
                            <Text style={styles.debateText}>
                              {turn.public_text || "(no text)"}
                            </Text>
                          </View>
                        );
                      })}
                    </View>
                  );
                })}
              </View>
            );
          })}
          <Text
            style={styles.footer}
            render={({ pageNumber, totalPages }) =>
              `Assembly · synthetic-society simulation lab · run ${runId} · generated ${generatedAtStr} · page ${pageNumber} of ${totalPages}`
            }
            fixed
          />
        </Page>
      )}

      {/* ====== CAVEATS PAGE ====== */}
      <Page size="LETTER" style={styles.page}>
        <View style={styles.section}>
          <Text style={styles.h2}>Caveats</Text>
          {caveats.map((c, i) => (
            <Text key={i} style={styles.caveatListItem}>
              — {c}
            </Text>
          ))}
        </View>
        <Text
          style={styles.footer}
          render={({ pageNumber, totalPages }) =>
            `Assembly · synthetic-society simulation lab · run ${runId} · generated ${generatedAtStr} · page ${pageNumber} of ${totalPages}`
          }
          fixed
        />
      </Page>
    </Document>
  );
}
