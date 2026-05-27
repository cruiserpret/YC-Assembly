// Phase 10B+ — synthetic personas card.
//
// Earlier versions exposed raw plumbing (run_scope_id, evidence
// strategy, snake_case quality-gate booleans). Founders found that
// noisy. The new card answers three founder-facing questions:
//
//   1. How many personas does my synthetic society have?
//   2. Who are they? (role distribution, humanized)
//   3. Are they trustworthy? (one-sentence quality summary, with
//      the technical gate detail tucked behind an advanced toggle)

import { humanizeRole } from "@/lib/labels";
import { bucketStance } from "@/lib/stance";
import type {
  CohortsPayload,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  PersonasPayload,
} from "@/lib/types";
import { useLightweightVoters } from "@/lib/useLightweightVoters";
import { DownloadReportButton } from "./DownloadReportButton";
import { DownloadPdfButton } from "./DownloadPdfButton";

export interface PersonaListProps {
  personas: PersonasPayload;
  /**
   * Optional. When provided, the card derives role distribution +
   * stance summary directly from the transcript so the founder
   * can see who's actually in the simulated society. Without it
   * we fall back to the headline counts only.
   */
  transcript?: DiscussionTranscriptPayload | null;
  /** Run id is used by the download button to fetch the report. */
  runId?: string;
  /** Product name shown in the downloaded report header. */
  productName?: string;
  /** Pre-fetched payloads passed straight into DownloadReportButton
   *  so the button doesn't need to re-fetch. */
  report?: FounderReport | null;
  intent?: IntentPayload | null;
  cohorts?: CohortsPayload | null;
  discussion?: DiscussionPayload | null;
}

interface RoleSlice {
  role: string;
  display: string;
  count: number;
  bucketCounts: { for: number; against: number; neutral: number };
}

export function PersonaList({
  personas,
  transcript,
  runId,
  productName,
  report,
  intent,
  cohorts,
  discussion,
}: PersonaListProps) {
  const totalPersonas = personas.persona_count ?? 0;
  const gateResults = personas.quality_gates_summary ?? {};
  const gates = Object.entries(gateResults);
  const passedGates = gates.filter(([, v]) => v).length;

  const roleSlices = transcript ? deriveRoleSlices(transcript) : [];
  const distinctRoles = roleSlices.length;

  // Phase 14A — fetch voter overlay so the download buttons can
  // include the 100-voter section in the HTML and PDF reports.
  // Cached at the React Query layer, so this shares the fetch with
  // LightweightVoterPanelLive on the same page.
  const { data: voters } = useLightweightVoters(runId);

  return (
    <details
      data-testid="persona-list"
      className="rounded-md border border-border bg-surface"
    >
      <summary className="flex cursor-pointer select-none items-center justify-between px-6 py-4">
        <div>
          <h3 className="text-lg font-semibold text-text-primary">
            Society composition
          </h3>
          <p className="mt-0.5 text-xs text-text-muted">
            Who Assembly simulated for this run
            {distinctRoles > 0 ? (
              <> · {totalPersonas} debate agents + 100 voters · {distinctRoles} roles</>
            ) : (
              <> · {totalPersonas} debate agents + 100 voters</>
            )}
          </p>
        </div>
        <span
          aria-hidden
          className="text-xs uppercase tracking-wider text-text-muted"
        >
          Tap to expand
        </span>
      </summary>
      <div className="space-y-5 border-t border-border px-6 py-5">
        {/* Phase 14A — two-layer society summary. The 24-ish deep
            agents are the ones generating arguments in the debate
            transcript; the 100 voters are a larger sample that
            absorbs and spreads those arguments through an influence
            network. */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div
            data-testid="society-layer-deep"
            className="rounded-md border border-border bg-surface-elevated p-4"
          >
            <p className="text-[11px] uppercase tracking-wider text-text-muted">
              Deep debate agents
            </p>
            <p className="mt-1 font-mono text-3xl text-accent">
              {totalPersonas}
            </p>
            <p className="mt-1 text-xs text-text-body">
              Full LLM personas who speak in the debate transcript —
              they argue, push back, and revise their views.
            </p>
          </div>
          <div
            data-testid="society-layer-voters"
            className="rounded-md border border-border bg-surface-elevated p-4"
          >
            <p className="text-[11px] uppercase tracking-wider text-text-muted">
              Voter overlay
            </p>
            <p className="mt-1 font-mono text-3xl text-accent">100</p>
            <p className="mt-1 text-xs text-text-body">
              Lightweight voters that propagate the debate signal
              through a 4-round influence loop. Do not write new
              messages.
            </p>
          </div>
        </div>
        <p
          data-testid="society-model-summary"
          className="text-xs text-text-muted"
        >
          Society model: <span className="text-text-primary">
            {totalPersonas} debate agents + 100 voters
          </span>. Debate agents talk; voters absorb and spread.
        </p>

        <p className="text-sm text-text-body">
          Assembly generated{" "}
          <span className="font-mono text-accent">{totalPersonas}</span>{" "}
          synthetic debate agents{distinctRoles > 0 ? (
            <>
              {" "}across{" "}
              <span className="font-mono text-text-primary">
                {distinctRoles}
              </span>{" "}
              distinct roles
            </>
          ) : null}
          , all built fresh from real review evidence for this specific
          brief. They&apos;re run-scoped — they don&apos;t exist outside
          this run, and they&apos;re not a global market segment.
        </p>

        {roleSlices.length > 0 ? (
          <RoleBreakdown slices={roleSlices} />
        ) : null}

        <QualitySummary
          passed={passedGates}
          total={gates.length}
          gates={gates}
        />

        {runId ? (
          <div className="space-y-4">
            <div className="rounded-md border border-border bg-surface-elevated p-4 text-sm">
              <p className="mb-1 font-medium text-text-primary">
                In-depth report — PDF
              </p>
              <p className="mb-3 text-text-body">
                Real one-click PDF — every section expanded, including
                the group-by-group debate transcript with every
                persona, every round, every turn.
              </p>
              <DownloadPdfButton
                runId={runId}
                productName={productName}
                report={report}
                intent={intent}
                cohorts={cohorts}
                personas={personas}
                discussion={discussion}
                transcript={transcript}
                voters={voters ?? null}
              />
            </div>
            <div className="rounded-md border border-border bg-surface-elevated p-4 text-sm">
              <p className="mb-1 font-medium text-text-primary">
                In-depth report — HTML
              </p>
              <p className="mb-3 text-text-body">
                Self-contained HTML document — same content, with
                interactive collapsible sections you can browse in
                any browser.
              </p>
              <DownloadReportButton
                runId={runId}
                productName={productName}
                report={report}
                intent={intent}
                cohorts={cohorts}
                personas={personas}
                discussion={discussion}
                transcript={transcript}
                voters={voters ?? null}
              />
            </div>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function RoleBreakdown({ slices }: { slices: RoleSlice[] }) {
  return (
    <div data-testid="role-breakdown" className="space-y-2">
      <p className="text-xs uppercase tracking-wider text-text-muted">
        Role makeup of the society
      </p>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {slices.map((s) => {
          const total = s.count;
          const segs = (
            ["for", "neutral", "against"] as const
          ).map((b) => ({
            bucket: b,
            count: s.bucketCounts[b],
            pct: total > 0 ? (s.bucketCounts[b] / total) * 100 : 0,
          }));
          return (
            <li
              key={s.role}
              className="space-y-2 rounded-md border border-border bg-surface-elevated p-3"
            >
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium text-text-primary">
                  {s.display}
                </p>
                <p className="font-mono text-sm text-accent">
                  {s.count}
                </p>
              </div>
              {/* Segmented stance breakdown for this role */}
              <div
                className="flex h-1.5 w-full overflow-hidden rounded-sm bg-border"
                aria-hidden
              >
                {segs.map((seg) =>
                  seg.count > 0 ? (
                    <span
                      key={seg.bucket}
                      style={{ width: `${seg.pct}%` }}
                      className={
                        seg.bucket === "for"
                          ? "bg-accent"
                          : seg.bucket === "against"
                            ? "bg-danger"
                            : "bg-text-muted"
                      }
                    />
                  ) : null,
                )}
              </div>
              <p className="text-[10px] uppercase tracking-wider text-text-muted">
                {segs
                  .filter((seg) => seg.count > 0)
                  .map(
                    (seg) =>
                      `${seg.bucket.toUpperCase()} ${seg.count}`,
                  )
                  .join("  ·  ")}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function QualitySummary({
  passed,
  total,
  gates,
}: {
  passed: number;
  total: number;
  gates: [string, boolean][];
}) {
  if (total === 0) return null;
  const allPassed = passed === total;
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-4 text-sm">
      <p
        className={`font-medium ${allPassed ? "text-accent" : "text-warning"}`}
      >
        {allPassed
          ? `All ${total} persona quality gates passed.`
          : `${passed} of ${total} persona quality gates passed.`}
      </p>
      <p className="mt-1 text-text-body">
        {allPassed
          ? "Every persona is anchored to real retrieval evidence, no duplicates, no fake claims of having used the product, and the role mix passed concentration + diversity checks."
          : "Some quality gates didn't pass — see details below."}
      </p>
      <details className="mt-3 text-xs text-text-muted">
        <summary className="cursor-pointer text-text-muted">
          Show technical detail
        </summary>
        <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
          {gates.map(([name, ok]) => (
            <li key={name} className="flex items-center gap-2">
              <span
                aria-hidden
                className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-accent" : "bg-danger"}`}
              />
              <span
                className={
                  ok ? "text-text-body" : "text-danger"
                }
              >
                {name}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

// -----------------------------------------------------------------------
// Derivation helpers (transcript → role + stance distributions)
// -----------------------------------------------------------------------

function deriveRoleSlices(
  transcript: DiscussionTranscriptPayload,
): RoleSlice[] {
  const stanceByPid: Record<string, string | null> = {};
  for (const [pid, b] of Object.entries(transcript.private_ballots)) {
    stanceByPid[pid] =
      b.final?.stance ?? b.reflection?.stance ?? b.pre?.stance ?? null;
  }
  const personasSeen = new Set<string>();
  const byRole = new Map<string, RoleSlice>();
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      if (personasSeen.has(p.persona_id)) continue;
      personasSeen.add(p.persona_id);
      const display = humanizeRole(p.role);
      let entry = byRole.get(p.role);
      if (!entry) {
        entry = {
          role: p.role,
          display,
          count: 0,
          bucketCounts: { for: 0, against: 0, neutral: 0 },
        };
        byRole.set(p.role, entry);
      }
      entry.count += 1;
      const bucket = bucketStance(stanceByPid[p.persona_id]);
      entry.bucketCounts[bucket] += 1;
    }
  }
  return [...byRole.values()].sort((a, b) => b.count - a.count);
}

