"use client";
// Phase 10B+ — "Download in-depth report" button.
//
// We deliberately do NOT pipe the backend's raw markdown into the
// downloaded HTML. That markdown contains developer-shaped content
// (raw Python dict reprs, 8-char persona ids, internal phase refs,
// audit JSON) which doesn't read well to a founder.
//
// Instead we render directly from the structured payloads the
// dashboard already loaded — the same shape the on-screen UI uses,
// just laid out for a printable document. Labels are humanized,
// numbers are kept clean, and the [ ASSEMBLY ] logo banner sits
// at the top with the locked palette and a subtle metallic
// gradient on the wordmark.

import { useState } from "react";
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

export interface DownloadReportButtonProps {
  runId: string;
  productName?: string;
  /** All five payloads needed to build a clean structured report.
   *  When the dashboard has them already, pass them in to avoid
   *  duplicate fetches. */
  report?: FounderReport | null;
  intent?: IntentPayload | null;
  cohorts?: CohortsPayload | null;
  personas?: PersonasPayload | null;
  discussion?: DiscussionPayload | null;
  transcript?: DiscussionTranscriptPayload | null;
  voters?: LightweightVotersPayload | null;
  className?: string;
}

export function DownloadReportButton({
  runId,
  productName,
  report,
  intent,
  cohorts,
  personas,
  discussion,
  transcript,
  voters,
  className,
}: DownloadReportButtonProps) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = !!(report && transcript);

  async function onDownload() {
    setError(null);
    if (!ready || !report || !transcript) {
      setError(
        "Report data still loading — try again in a moment.",
      );
      return;
    }
    setBusy(true);
    try {
      // Phase 14B — guarantee the voter payload is fresh at click
      // time. If the React Query cache already has it, this resolves
      // immediately; otherwise it forces a fetch BEFORE we generate
      // the HTML. Prevents the previous race condition where users
      // clicked Download before useLightweightVoters completed and
      // got an "unavailable" section in their report.
      let votersForDownload = voters ?? null;
      if (!votersForDownload || !votersForDownload.voter_overlay_available) {
        try {
          const { getAssemblyLightweightVoters } = await import(
            "@/lib/api"
          );
          votersForDownload = await getAssemblyLightweightVoters(
            runId,
          );
        } catch {
          // Endpoint failed at click time — fall through with the
          // existing (possibly null) voters. The renderer's
          // unavailable notice will surface the reason.
        }
      }

      const html = renderStructuredReport({
        runId,
        productName: productName ?? "Synthetic society report",
        report,
        intent: intent ?? null,
        cohorts: cohorts ?? null,
        personas: personas ?? null,
        discussion: discussion ?? null,
        transcript,
        voters: votersForDownload,
      });
      const blob = new Blob([html], {
        type: "text/html;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const slug = (productName ?? "assembly-report")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slug || "assembly-report"}-${runId.slice(0, 8)}.html`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onDownload}
        disabled={!ready || busy}
        data-testid="download-report"
        className={`inline-flex items-center justify-center gap-2 rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow disabled:opacity-60 disabled:cursor-not-allowed ${className ?? ""}`}
      >
        <span aria-hidden>↓</span>
        {!ready
          ? "Preparing report…"
          : busy
            ? "Fetching voter layer…"
            : "Download HTML report"}
      </button>
      {error ? (
        <p
          role="alert"
          className="text-xs text-danger"
          data-testid="download-report-error"
        >
          Could not generate report: {error}
        </p>
      ) : (
        <p className="text-xs text-text-muted">
          Self-contained .html file — open in any browser. For a PDF,
          use the “Download PDF report” button above.
        </p>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------
// Structured renderer
// -----------------------------------------------------------------------

export interface ReportContext {
  runId: string;
  productName: string;
  report: FounderReport;
  intent: IntentPayload | null;
  cohorts: CohortsPayload | null;
  personas: PersonasPayload | null;
  discussion: DiscussionPayload | null;
  transcript: DiscussionTranscriptPayload;
  voters?: LightweightVotersPayload | null;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

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
    {
      display: string;
      count: number;
      for: number;
      against: number;
      neutral: number;
    }
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
  let trajectory: string;
  if (forPct >= 0.55 && shiftPct >= 0.2) {
    trajectory =
      "synthetic trajectory: receptive, with the debate strengthening interest";
  } else if (forPct >= 0.55) {
    trajectory =
      "synthetic trajectory: receptive room from the start, with stances mostly intact";
  } else if (againstPct >= 0.4) {
    trajectory =
      "synthetic trajectory: contested — a sizeable group resists and would need targeted proof to move";
  } else if (neutralPct >= 0.5) {
    trajectory =
      "synthetic trajectory: undecided — the simulation suggests proof points are the bottleneck, not interest";
  } else {
    trajectory =
      "synthetic trajectory: mixed — no clear majority emerged from the synthetic discussion";
  }
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return `${cap(lean)}; ${shiftPhrase}. ${cap(trajectory)} — this is a synthetic signal, not a real-world purchase forecast, and should be validated with real prospects.`;
}

// Human label for each round-type, kept consistent with the sample report
// page and the API-side full_debate_section.py markdown renderer.
const ROUND_LABEL: Record<string, string> = {
  public_opening: "Public opening",
  challenge: "Challenge",
  peer_response: "Peer response",
  proof_discussion: "Proof discussion",
};

function renderFullDebateSection(
  transcript: DiscussionTranscriptPayload,
): string | null {
  const groups = transcript.groups ?? [];
  if (groups.length === 0) return null;

  const personaCount = new Set(
    groups.flatMap((g) =>
      g.personas.map((p) => p.persona_id),
    ),
  ).size;
  const totalTurns = groups.reduce(
    (acc, g) =>
      acc +
      g.rounds.reduce(
        (rAcc, r) => rAcc + (r.turns?.length ?? 0),
        0,
      ),
    0,
  );

  // role lookup so we can label each turn with the persona's role.
  const roleByPersona: Record<string, string> = {};
  for (const g of groups) {
    for (const p of g.personas) {
      roleByPersona[p.persona_id] = p.role;
    }
  }

  const groupBlocks = groups
    .slice()
    .sort((a, b) => a.group_index - b.group_index)
    .map((group, gIdx) => {
      const sortedRounds = group.rounds
        .slice()
        .sort((a, b) => a.round_number - b.round_number);

      const roundBlocks = sortedRounds
        .map((round, rIdx) => {
          const turns = (round.turns ?? [])
            .slice()
            .sort((a, b) => a.turn_number - b.turn_number);

          if (turns.length === 0) {
            return `
            <details class="debate-round" ${
              gIdx === 0 && rIdx === 0 ? "open" : ""
            }>
              <summary><strong>Round ${
                round.round_number
              }</strong> — ${escapeHtml(
                ROUND_LABEL[round.round_label] || round.round_label,
              )} <span class="muted">(no turns recorded)</span></summary>
            </details>`;
          }

          const turnRows = turns
            .map((t) => {
              const role = roleByPersona[t.speaker_persona_id] ?? t.speaker_role ?? "";
              const stanceLine = t.stance
                ? `<span class="debate-stance">${escapeHtml(
                    humanizeStance(t.stance),
                  )}</span>`
                : "";
              return `
              <li class="debate-turn">
                <div class="debate-turn-head">
                  <span class="debate-speaker">${escapeHtml(
                    t.speaker_name || "Unknown speaker",
                  )}</span>
                  <span class="debate-role">${escapeHtml(
                    humanizeRole(role),
                  )}</span>
                  ${stanceLine}
                </div>
                <p class="debate-text">${escapeHtml(
                  t.public_text || "(no text)",
                )}</p>
              </li>`;
            })
            .join("");

          return `
            <details class="debate-round" ${
              gIdx === 0 && rIdx === 0 ? "open" : ""
            }>
              <summary><strong>Round ${
                round.round_number
              }</strong> — ${escapeHtml(
                ROUND_LABEL[round.round_label] || round.round_label,
              )} <span class="muted">(${turns.length} turn${
                turns.length === 1 ? "" : "s"
              })</span></summary>
              <ol class="debate-turns">${turnRows}</ol>
            </details>`;
        })
        .join("");

      return `
        <details class="debate-group" ${gIdx === 0 ? "open" : ""}>
          <summary><strong>Group ${group.group_index + 1}</strong> <span class="muted">(${
        group.personas.length
      } persona${group.personas.length === 1 ? "" : "s"}, ${
        sortedRounds.length
      } round${sortedRounds.length === 1 ? "" : "s"})</span></summary>
          <div class="debate-group-body">
            ${roundBlocks}
          </div>
        </details>`;
    })
    .join("");

  return `
    <section class="full-debate">
      <h2>Full debate &amp; conversations</h2>
      <p class="caption">
        Every group, every round, every public turn from the synthetic
        discussion — ${groups.length} group${groups.length === 1 ? "" : "s"},
        ${personaCount} persona${personaCount === 1 ? "" : "s"},
        ${totalTurns} turn${totalTurns === 1 ? "" : "s"} in total. Sections
        are collapsible — open the dropdowns to read each round.
      </p>
      ${groupBlocks}
    </section>
  `;
}

// Phase 14A — 100-voter influence overlay section for the downloaded
// HTML report. Mirrors the on-screen LightweightVoterPanel: title +
// 4-bucket distribution bars + small stats + "how the 100 voters
// work" copy + optional 4-round influence dynamics table. Returns
// empty string when the run pre-dates Phase 12C / artifact missing.
function renderVoterInfluenceSection(
  voters: LightweightVotersPayload | null,
): string {
  // Visible unavailable notice instead of an empty string — otherwise
  // the report ships the .voter-panel CSS block with no matching
  // section, which is what the ShelfSense AI bug looked like.
  if (!voters || !voters.voter_overlay_available) {
    const reason =
      voters && "reason" in voters && typeof voters.reason === "string"
        ? voters.reason
        : "Voter artifact was not available at the time the report was downloaded.";
    return `
    <section class="voter-panel voter-panel-unavailable">
      <h2>100-voter influence layer</h2>
      <p>
        The 100-voter influence layer is not available in this
        downloaded report. New simulations include the 100-voter
        graph automatically. The rest of the report below is
        unaffected.
      </p>
      <p class="muted" style="font-family: 'JetBrains Mono', monospace; font-size: 11px;">
        ${escapeHtml(reason)}
      </p>
    </section>
    `;
  }
  const dist = voters.final_distribution ?? null;
  const voterCount =
    voters.voters_count ?? dist?.n_voters ?? 100;
  const cal = voters.calibrated_distribution ?? null;
  const rounds = (voters.influence_rounds ?? [])
    .slice()
    .sort((a, b) => a.round_idx - b.round_idx);

  const bucketLabels = [
    ["buyer", "Buyer"],
    ["receptive", "Receptive"],
    ["uncertain", "Uncertain"],
    ["skeptical", "Skeptical"],
  ] as const;

  const distRows = dist
    ? bucketLabels
        .map(([k, label]) => {
          const v = Number((dist as unknown as Record<string, unknown>)[k] ?? 0);
          const count = Math.round((v / 100) * voterCount);
          const width = Math.max(0, Math.min(100, v));
          const toneClass =
            k === "buyer" || k === "receptive"
              ? "accent"
              : k === "skeptical"
                ? "danger"
                : "muted";
          return `
        <li class="voter-bucket">
          <div class="voter-bucket-head">
            <span class="${toneClass}">${escapeHtml(label)}</span>
            <span class="num">${count}/${voterCount}
              <span class="muted">(${Math.round(v)}%)</span>
            </span>
          </div>
          <div class="voter-bar"><span class="voter-bar-fill ${toneClass}-bg" style="width: ${width}%;"></span></div>
        </li>`;
        })
        .join("")
    : "";

  const confidenceBand =
    cal && typeof cal.confidence_band_pp === "number"
      ? `±${Math.round(cal.confidence_band_pp)} pp`
      : "—";
  const totalShifts = rounds.reduce(
    (acc, r) => acc + (r.bucket_changes ?? 0), 0,
  );

  const dynamicsRows = rounds
    .map((r) => {
      const bd = (r.bucket_distribution ?? {}) as Record<string, number>;
      const total = bucketLabels.reduce(
        (acc, [k]) => acc + (bd[k] ?? 0), 0,
      );
      const denom = total > 0 ? total : voterCount;
      const segs = bucketLabels
        .map(([k]) => {
          const c = bd[k] ?? 0;
          const w = denom > 0 ? (c / denom) * 100 : 0;
          if (w <= 0) return "";
          const tone =
            k === "buyer" || k === "receptive"
              ? "accent"
              : k === "skeptical"
                ? "danger"
                : "muted";
          return `<span class="${tone}-bg" style="width:${w}%;"></span>`;
        })
        .join("");
      return `
      <tr>
        <td class="num">Round ${r.round_idx}</td>
        <td><div class="voter-bar">${segs}</div></td>
        <td class="num muted">${r.intent_changes ?? 0} shifts</td>
      </tr>`;
    })
    .join("");

  const cluster = voters.cluster_arguments ?? null;
  const proArgs = Array.isArray(cluster?.pro) ? cluster!.pro!.slice(0, 3) : [];
  const conArgs = Array.isArray(cluster?.con) ? cluster!.con!.slice(0, 3) : [];

  return `
    <section class="voter-panel">
      <h2>${voterCount}-voter influence layer</h2>
      <p class="caption">
        A larger simulated sample that absorbs and spreads the debate
        signal. The deep agents above are the ones generating
        arguments; the ${voterCount} voters react to those arguments
        and propagate them through a 4-round influence network. No
        new LLM calls per voter; no free-text generation.
      </p>
      ${distRows ? `<ul class="voter-buckets">${distRows}</ul>` : ""}
      <ul class="metrics">
        <li><strong class="accent">${voterCount}</strong><span>Voters in this run</span></li>
        <li><strong class="accent">${totalShifts}</strong><span>Bucket-level shifts across 4 rounds</span></li>
        <li><strong class="accent">${escapeHtml(confidenceBand)}</strong><span>Confidence band</span></li>
      </ul>
      ${
        proArgs.length || conArgs.length
          ? `
        <div class="two-col">
          ${
            proArgs.length
              ? `<div>
              <h3>Strongest spreading arguments</h3>
              <ol class="sentences">
                ${proArgs
                  .map((a) => `<li>${escapeHtml(String(a).slice(0, 240))}</li>`)
                  .join("")}
              </ol>
            </div>`
              : ""
          }
          ${
            conArgs.length
              ? `<div>
              <h3>Most resisted arguments</h3>
              <ol class="sentences">
                ${conArgs
                  .map((a) => `<li>${escapeHtml(String(a).slice(0, 240))}</li>`)
                  .join("")}
              </ol>
            </div>`
              : ""
          }
        </div>`
          : ""
      }
      ${
        rounds.length
          ? `
        <h3>Influence dynamics across 4 rounds</h3>
        <table>
          <thead>
            <tr>
              <th class="num">Round</th>
              <th>Bucket distribution</th>
              <th class="num">Intent shifts</th>
            </tr>
          </thead>
          <tbody>${dynamicsRows}</tbody>
        </table>`
          : ""
      }
      <blockquote>
        <strong>How the 100 voters work.</strong>
        The personas in the debate transcript are the ones doing the
        talking — they argue, push back, and revise their views
        across 4 groups and 4 rounds. The 100 voters are a larger
        simulated sample drawn from the same evidence and cohorts.
        They do not write new messages. Instead, they react to the
        arguments the debate agents made and propagate those
        arguments through a 100-voter influence network over 4
        rounds. In short: <em>debate agents talk; voters absorb and
        spread.</em>
      </blockquote>
    </section>
  `;
}

export function renderStructuredReport(ctx: ReportContext): string {
  const safeProduct = escapeHtml(ctx.productName);
  const generatedAt = new Date().toLocaleString();
  const buckets = deriveBucketCounts(ctx.transcript);
  const shifts = deriveShiftCounts(ctx.transcript);
  const roleBreakdown = deriveRoleBreakdown(ctx.transcript);
  const trajectory = synthesizeTrajectory({
    ...buckets,
    shifted: shifts.shifted,
    scored: shifts.scored,
  });

  // Intent — humanize the closed-set labels and drop unknown labels
  const intentDist = ctx.intent?.intent_distribution ?? {};
  const intentRows = Object.entries(intentDist)
    .filter(([, v]) => (v as number) > 0)
    .sort(([, a], [, b]) => (b as number) - (a as number));

  // Cohort sizes
  // Top objections / proof needs as natural-language sentences.
  // Phase 14B — filter physical-product-only buckets on software/digital
  // briefs unless the weighted_score is high enough to be real signal.
  const objections = filterApplicableObjectionBuckets(
    (ctx.report.top_objections || [])
      .slice()
      .sort(
        (a, b) =>
          (b.weighted_score ?? 0) - (a.weighted_score ?? 0),
      ),
    ctx.report.product_brief,
  ).slice(0, 6);
  const proofs = filterApplicableProofBuckets(
    (ctx.report.proof_needed || [])
      .slice()
      .sort(
        (a, b) =>
          (b.weighted_score ?? 0) - (a.weighted_score ?? 0),
      ),
    ctx.report.product_brief,
  ).slice(0, 6);

  // Public ↔ private shift summary
  const shiftSummary = ctx.report.public_private_shift_summary;

  // Discussion stats
  const turns = ctx.discussion?.public_turn_count ?? 0;
  const personaCount = ctx.discussion?.persona_count ?? buckets.total;
  const ballotsByStage =
    ctx.discussion?.ballot_count_by_stage ?? {};
  const groupCount = ctx.discussion?.group_count ?? 0;

  // Build content sections
  const sections: string[] = [];

  // 1. Where the discussion landed
  sections.push(`
    <section>
      <h2>Where the discussion landed</h2>
      <blockquote>${escapeHtml(trajectory)}</blockquote>
    </section>
  `);

  // 2. Final consensus snapshot
  sections.push(`
    <section>
      <h2>Final consensus snapshot</h2>
      <ul class="metrics">
        <li>
          <strong class="accent">${buckets.for}</strong>
          <span>Receptive</span>
        </li>
        <li>
          <strong class="muted">${buckets.neutral}</strong>
          <span>Uncertain</span>
        </li>
        <li>
          <strong class="danger">${buckets.against}</strong>
          <span>Resistant</span>
        </li>
      </ul>
      <ul class="metrics">
        <li>
          <strong class="accent">${shifts.shifted}</strong>
          <span>Agents shifted</span>
        </li>
        <li>
          <strong class="muted">${shifts.held}</strong>
          <span>Agents held</span>
        </li>
        <li>
          <strong class="accent">${pct(shifts.scored > 0 ? shifts.shifted / shifts.scored : 0)}</strong>
          <span>Opinion shift rate</span>
        </li>
      </ul>
    </section>
  `);

  // 3. Simulated intent
  if (intentRows.length > 0) {
    sections.push(`
      <section>
        <h2>Synthetic intent snapshot</h2>
        <p class="caption">
          Synthetic expressed intent inside this run — not real-world
          purchase behavior. n = ${personaCount} run-scoped personas.
        </p>
        <table>
          <tbody>
            ${intentRows
              .map(
                ([k, v]) => `
              <tr>
                <td>${escapeHtml(humanizeStance(k))}</td>
                <td class="num">${v}</td>
              </tr>`,
              )
              .join("")}
          </tbody>
        </table>
      </section>
    `);
  }

  // 3b. Phase 14A — 100-voter influence layer. Placed after the
  // synthetic intent snapshot, before objections / debate sections,
  // mirroring the on-screen report layout. Gracefully omitted when
  // the run pre-dates Phase 12C or the artifact is missing.
  const voterBlock = renderVoterInfluenceSection(ctx.voters ?? null);
  if (voterBlock) {
    sections.push(voterBlock);
  }

  // 5. Objections
  if (objections.length > 0) {
    sections.push(`
      <section>
        <h2>What this society pushed back on</h2>
        <p class="caption">
          Synthetic objections, ordered by how often they came up.
        </p>
        <ol class="sentences">
          ${objections
            .map(
              (o) => `
            <li>${escapeHtml(objectionSentence(o.bucket))}</li>`,
            )
            .join("")}
        </ol>
      </section>
    `);
  }

  // 6. Proof needs
  if (proofs.length > 0) {
    sections.push(`
      <section>
        <h2>What would change their minds</h2>
        <p class="caption">
          Synthetic proof needs, ordered by how much they'd shift the
          room.
        </p>
        <ol class="sentences">
          ${proofs
            .map(
              (p) => `
            <li>${escapeHtml(proofSentence(p.bucket))}</li>`,
            )
            .join("")}
        </ol>
      </section>
    `);
  }

  // 7. Personas — role breakdown
  if (roleBreakdown.length > 0) {
    sections.push(`
      <section>
        <h2>Who's in this synthetic society</h2>
        <p class="caption">
          Role makeup of the run-scoped, evidence-anchored persona
          set. Each row shows the role's count and how that role
          finished by stance bucket.
        </p>
        <table>
          <thead>
            <tr>
              <th>Role</th>
              <th class="num">Count</th>
              <th class="num">Receptive</th>
              <th class="num">Uncertain</th>
              <th class="num">Resistant</th>
            </tr>
          </thead>
          <tbody>
            ${roleBreakdown
              .map(
                (r) => `
              <tr>
                <td>${escapeHtml(r.display)}</td>
                <td class="num accent">${r.count}</td>
                <td class="num accent">${r.for}</td>
                <td class="num muted">${r.neutral}</td>
                <td class="num danger">${r.against}</td>
              </tr>`,
              )
              .join("")}
          </tbody>
        </table>
      </section>
    `);
  }

  // 8. Public ↔ private stance distributions
  if (
    shiftSummary &&
    (Object.keys(shiftSummary.pre_stance_distribution || {}).length > 0 ||
      Object.keys(shiftSummary.final_stance_distribution || {}).length > 0)
  ) {
    const renderDist = (dist: Record<string, number>) =>
      Object.entries(dist)
        .filter(([, v]) => v > 0)
        .sort(([, a], [, b]) => b - a)
        .map(
          ([k, v]) =>
            `<tr><td>${escapeHtml(humanizeStance(k))}</td><td class="num">${v}</td></tr>`,
        )
        .join("");
    sections.push(`
      <section>
        <h2>Public ↔ private stance</h2>
        <div class="two-col">
          <div>
            <h3>Pre-discussion</h3>
            <table>
              <tbody>
                ${renderDist(shiftSummary.pre_stance_distribution || {})}
              </tbody>
            </table>
          </div>
          <div>
            <h3>Final</h3>
            <table>
              <tbody>
                ${renderDist(shiftSummary.final_stance_distribution || {})}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    `);
  }

  // 9. Discussion summary (counts only)
  if (turns > 0 || personaCount > 0) {
    // Phase 14B — derive the actual round count from the transcript
    // instead of hardcoding "7-round". The pipeline shifted from the
    // original 7-round Phase-6 design to a 4-round live debate; the
    // copy must reflect what actually ran.
    const transcriptRoundCount = Math.max(
      0,
      ...(ctx.transcript.groups ?? []).map((g) =>
        (g.rounds ?? []).length,
      ),
    );
    const roundCountLabel =
      transcriptRoundCount > 0 ? transcriptRoundCount : "multi";
    sections.push(`
      <section>
        <h2>Group discussion summary</h2>
        <p class="caption">
          Synthetic ${roundCountLabel}-round discussion across ${groupCount}
          group${groupCount === 1 ? "" : "s"} — not a recording of
          real customers.
        </p>
        <ul class="metrics">
          <li>
            <strong class="accent">${personaCount}</strong>
            <span>Personas</span>
          </li>
          <li>
            <strong class="accent">${turns}</strong>
            <span>Public turns</span>
          </li>
          <li>
            <strong class="accent">${ballotsByStage.final ?? 0}</strong>
            <span>Final ballots</span>
          </li>
        </ul>
        ${
          // Phase 14B — when final ballot count diverges from persona
          // count, surface the gap honestly instead of letting the
          // founder reverse-engineer it from two separate numbers.
          personaCount > 0 &&
          typeof ballotsByStage.final === "number" &&
          ballotsByStage.final < personaCount
            ? `<p class="caption">${
                personaCount - (ballotsByStage.final ?? 0)
              } of ${personaCount} personas did not complete a final ballot during the run. Their pre-discussion stance is still factored into the consensus snapshot, which is why the consensus totals may exceed the final-ballot count.</p>`
            : ""
        }
      </section>
    `);
  }

  // 10. Full Debate & Conversations — every group, every round, every turn.
  //     Matches the on-site sample report layout: collapsible <details> per
  //     group and per round, ROUND_LABEL-mapped headings, speaker name + role
  //     + stance bucket + the actual debate turn text.
  const fullDebateBlock = renderFullDebateSection(ctx.transcript);
  if (fullDebateBlock) {
    sections.push(fullDebateBlock);
  }

  // 11. Caveats
  const caveats =
    (ctx.report.caveats && ctx.report.caveats.length > 0
      ? ctx.report.caveats
      : [
          "Synthetic simulation — not a real-world forecast.",
          "Cohorts are run-scoped + brief-scoped — never global market segments.",
          "Simulated intent labels are NOT real-world purchase forecasts.",
          "Personas have not bought, used, owned, or reviewed the unlaunched product.",
        ]).map((c) => `<li>${escapeHtml(c)}</li>`).join("");
  sections.push(`
    <section class="caveat-section">
      <h2>Caveats</h2>
      <ul>${caveats}</ul>
    </section>
  `);

  // Final HTML document
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Assembly · ${safeProduct} · in-depth report</title>
  <style>
    :root {
      --bg: #0A0A0A;
      --surface: #141414;
      --surface-elevated: #181818;
      --border: #262626;
      --text: #CCCCCC;
      --text-primary: #FFFFFF;
      --muted: #8A8A8A;
      --accent: #AAFF00;
      --danger: #FF5C5C;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: 16px;
      line-height: 1.65;
    }
    .page {
      max-width: 920px;
      margin: 0 auto;
      padding: 56px 56px 96px;
    }
    /* ---- Logo banner ---- */
    .logo-banner {
      position: relative;
      padding: 56px 0 44px;
      margin-bottom: 32px;
      border-bottom: 1px solid var(--border);
      text-align: center;
      background-image:
        linear-gradient(to right, rgba(170, 255, 0, 0.06) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(170, 255, 0, 0.06) 1px, transparent 1px);
      background-size: 48px 48px;
    }
    .logo-text {
      display: inline-flex;
      align-items: center;
      gap: 22px;
      font-family: "Inter", sans-serif;
      font-weight: 900;
      font-size: 80px;
      line-height: 1;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: linear-gradient(
        180deg,
        #ffffff 0%,
        #e6e6e6 28%,
        #aeaeae 65%,
        #7a7a7a 100%
      );
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      filter: drop-shadow(0 2px 0 rgba(0, 0, 0, 0.55))
              drop-shadow(0 0 14px rgba(0, 0, 0, 0.6));
    }
    /* Brackets are visually smaller than the wordmark — about 60%
     * of the wordmark cap-height — matching the brand mark. */
    .logo-bracket {
      color: var(--accent);
      font-weight: 700;
      font-size: 50px;
      line-height: 1;
      -webkit-text-fill-color: var(--accent);
      filter: drop-shadow(0 0 8px rgba(170, 255, 0, 0.5));
    }
    .logo-tag {
      margin-top: 22px;
      font-size: 11px;
      letter-spacing: 0.4em;
      color: var(--muted);
      text-transform: uppercase;
    }
    /* ---- Meta ---- */
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 20px;
      font-family: "JetBrains Mono", ui-monospace, SFMono-Regular,
        Menlo, monospace;
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 28px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .meta strong {
      color: var(--accent);
      font-weight: 600;
    }
    /* ---- Caveat banner ---- */
    .caveat-banner {
      border: 1px solid var(--border);
      background: var(--surface);
      border-left: 3px solid var(--accent);
      padding: 14px 18px;
      margin-bottom: 40px;
      font-size: 13px;
      color: var(--text);
    }
    /* ---- Typography ---- */
    h1, h2, h3 { color: var(--text-primary); font-weight: 700; }
    h2 {
      font-size: 22px;
      margin: 36px 0 14px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--border);
    }
    h3 { font-size: 14px; margin: 18px 0 8px; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.1em; }
    p { margin: 0 0 14px; }
    p.caption { color: var(--muted); font-size: 13px; }
    blockquote {
      border-left: 3px solid var(--accent);
      background: var(--surface);
      margin: 14px 0;
      padding: 14px 20px;
      color: var(--text);
      font-style: normal;
      font-size: 15px;
    }
    /* ---- Metrics row (3 stats inline) ---- */
    ul.metrics {
      list-style: none;
      padding: 0;
      margin: 12px 0 18px;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
    }
    ul.metrics li {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-elevated);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    ul.metrics strong {
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 36px;
      font-weight: 700;
      line-height: 1;
    }
    ul.metrics span {
      font-size: 11px;
      letter-spacing: 0.1em;
      color: var(--muted);
      text-transform: uppercase;
    }
    /* ---- Cohort grid ---- */
    ul.cohort-grid {
      list-style: none;
      padding: 0;
      margin: 12px 0;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 12px;
    }
    ul.cohort-grid li {
      border: 1px solid var(--border);
      background: var(--surface-elevated);
      border-radius: 6px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    ul.cohort-grid strong {
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 28px;
    }
    /* ---- Numbered sentence list ---- */
    ol.sentences {
      list-style: none;
      counter-reset: s;
      padding: 0;
      margin: 0;
    }
    ol.sentences li {
      counter-increment: s;
      position: relative;
      border: 1px solid var(--border);
      background: var(--surface-elevated);
      border-radius: 6px;
      padding: 12px 14px 12px 48px;
      margin-bottom: 8px;
    }
    ol.sentences li::before {
      content: counter(s);
      position: absolute;
      left: 14px;
      top: 12px;
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 12px;
      color: var(--accent);
      border: 1px solid var(--border);
      border-radius: 999px;
      width: 22px;
      height: 22px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    /* ---- Tables ---- */
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 10px 0 16px;
    }
    th, td {
      border-bottom: 1px solid var(--border);
      padding: 8px 10px;
      text-align: left;
      font-size: 14px;
    }
    th {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      font-weight: 600;
    }
    td.num, th.num {
      text-align: right;
      font-family: "JetBrains Mono", ui-monospace, monospace;
    }
    .accent { color: var(--accent); }
    .muted { color: var(--muted); }
    .danger { color: var(--danger); }
    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }
    /* ---- Caveats section ---- */
    .caveat-section ul {
      list-style: none;
      padding: 0;
      margin: 8px 0;
    }
    .caveat-section li {
      padding-left: 18px;
      position: relative;
      margin-bottom: 6px;
      color: var(--text);
    }
    .caveat-section li::before {
      content: "—";
      position: absolute;
      left: 0;
      color: var(--accent);
    }
    /* ---- Full debate (collapsible groups + rounds) ---- */
    .full-debate details {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      margin: 10px 0;
      padding: 10px 14px;
    }
    .full-debate details[open] {
      background: var(--surface-elevated);
    }
    .full-debate summary {
      cursor: pointer;
      list-style: revert;
      font-size: 14px;
      color: var(--text);
      padding: 4px 0;
    }
    .full-debate summary strong {
      color: var(--accent);
      font-weight: 700;
    }
    .full-debate summary .muted {
      margin-left: 6px;
      font-size: 12px;
    }
    .debate-group-body {
      padding-left: 8px;
      margin-top: 8px;
      border-left: 2px solid var(--border);
    }
    .debate-group-body details.debate-round {
      background: var(--bg);
    }
    .debate-group-body details.debate-round[open] {
      background: var(--surface);
    }
    ol.debate-turns {
      list-style: none;
      padding: 0;
      margin: 10px 0 0;
    }
    li.debate-turn {
      border-top: 1px solid var(--border);
      padding: 10px 0;
    }
    li.debate-turn:first-child { border-top: none; }
    .debate-turn-head {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: baseline;
      margin-bottom: 4px;
      font-size: 12px;
    }
    .debate-speaker {
      color: var(--text-primary);
      font-weight: 700;
    }
    .debate-role {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .debate-stance {
      color: var(--accent);
      font-size: 11px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 1px 8px;
    }
    p.debate-text {
      margin: 4px 0 0;
      font-size: 14px;
      line-height: 1.55;
      color: var(--text);
    }
    /* ---- Phase 14A — 100-voter influence layer ---- */
    .voter-panel ul.voter-buckets {
      list-style: none;
      padding: 0;
      margin: 12px 0 16px;
    }
    .voter-panel li.voter-bucket {
      margin-bottom: 10px;
    }
    .voter-bucket-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .voter-bar {
      display: flex;
      width: 100%;
      height: 8px;
      border-radius: 3px;
      overflow: hidden;
      background: var(--border);
    }
    .voter-bar-fill, .voter-bar > span {
      display: block;
      height: 100%;
    }
    .accent-bg { background: var(--accent); }
    .muted-bg { background: var(--muted); }
    .danger-bg { background: var(--danger); }
    footer {
      margin-top: 64px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.05em;
    }
    /* ---- Print ---- */
    @media print {
      html, body { background: #fff; color: #1a1a1a; }
      .page { padding: 0 !important; max-width: none; }
      .logo-banner { background-image: none; border-bottom-color: #ccc; }
      .logo-text {
        background: linear-gradient(180deg, #1a1a1a 0%, #555 100%);
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        filter: none;
      }
      .logo-bracket {
        -webkit-text-fill-color: #5a8a00;
        color: #5a8a00;
        filter: none;
      }
      h1, h2, h3 { color: #0a0a0a; }
      blockquote, .caveat-banner, ul.metrics li, ul.cohort-grid li,
      ol.sentences li {
        background: #f7f7f7; color: #1a1a1a; border-color: #ddd;
      }
      strong { color: #0a0a0a; }
      .accent { color: #5a8a00; }
      .danger { color: #b03030; }
      .muted { color: #555; }
      th, td { border-bottom-color: #ddd; }
      /* Force every collapsible section open when printing — the user
       * wants the full debate to render into the PDF, not stay hidden
       * behind dropdown arrows. */
      .full-debate details { background: #fff; border-color: #ddd; }
      .full-debate details[open] { background: #fff; }
      .full-debate details > *:not(summary) { display: block !important; }
      .full-debate details summary {
        list-style: none;
        font-weight: 600;
        color: #0a0a0a;
      }
      .full-debate details summary::-webkit-details-marker { display: none; }
      .full-debate summary strong { color: #5a8a00; }
      .debate-group-body { border-left-color: #ddd; }
      .debate-group-body details.debate-round,
      .debate-group-body details.debate-round[open] { background: #fff; }
      li.debate-turn { border-top-color: #eee; }
      .debate-speaker { color: #0a0a0a; }
      .debate-role, .debate-stance { color: #5a8a00; border-color: #ddd; }
      p.debate-text { color: #1a1a1a; }
    }
  </style>
</head>
<body>
  <div class="page">
    <div class="logo-banner">
      <div class="logo-text">
        <span class="logo-bracket">[</span>
        <span>ASSEMBLY</span>
        <span class="logo-bracket">]</span>
      </div>
      <p class="logo-tag">synthetic-society simulation lab</p>
    </div>
    <div class="meta">
      <span>Product: <strong>${safeProduct}</strong></span>
      <span>Run: ${escapeHtml(ctx.runId)}</span>
      <span>Generated: ${escapeHtml(generatedAt)}</span>
    </div>
    ${sections.join("\n")}
    <footer>
      Assembly &middot; synthetic-society simulation lab &middot;
      run ${escapeHtml(ctx.runId)} &middot; generated
      ${escapeHtml(generatedAt)}
    </footer>
  </div>
  <script>
    // Force every collapsible <details> open before printing so the
    // full debate transcript renders into the PDF. Restore prior
    // state after printing so the on-screen view stays interactive.
    (function () {
      var saved = [];
      window.addEventListener('beforeprint', function () {
        saved = [];
        var nodes = document.querySelectorAll('details');
        for (var i = 0; i < nodes.length; i++) {
          saved.push(nodes[i].open);
          nodes[i].open = true;
        }
      });
      window.addEventListener('afterprint', function () {
        var nodes = document.querySelectorAll('details');
        for (var i = 0; i < nodes.length; i++) {
          if (typeof saved[i] === 'boolean') nodes[i].open = saved[i];
        }
      });
    })();
  </script>
</body>
</html>`;
}
