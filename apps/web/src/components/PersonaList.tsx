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
import { DownloadReportButton } from "./DownloadReportButton";

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
              <> · {totalPersonas} personas · {distinctRoles} roles</>
            ) : null}
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
        <p className="text-sm text-text-body">
          Assembly generated{" "}
          <span className="font-mono text-accent">{totalPersonas}</span>{" "}
          synthetic personas{distinctRoles > 0 ? (
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
          <div className="rounded-md border border-border bg-surface-elevated p-4 text-sm">
            <p className="mb-1 font-medium text-text-primary">
              In-depth report
            </p>
            <p className="mb-3 text-text-body">
              Download the full report — every section, every persona,
              every objection and proof bucket — packaged as a
              self-contained HTML document with the Assembly logo on
              top.
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
            />
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

