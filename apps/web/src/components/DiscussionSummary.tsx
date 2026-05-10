// Phase 10B+ — public ↔ private stance card.
// The earlier "group discussion summary" stat panel + ballots-by-
// stage + society-wide debate fold were removed in 10B+ because
// founders found the counts noisy. What's left is the one block
// that tells a real story: how the synthetic society's stance
// shifted between the pre-discussion ballot and the final ballot.

import { humanizeStance } from "@/lib/labels";
import type { DiscussionPayload, FounderReport } from "@/lib/types";

export interface DiscussionSummaryProps {
  discussion: DiscussionPayload;
  report?: FounderReport | null;
}

export function DiscussionSummary({
  report,
}: DiscussionSummaryProps) {
  const shiftSummary = report?.public_private_shift_summary;
  if (!shiftSummary) return null;
  return (
    <section
      data-testid="discussion-summary"
      className="space-y-3 rounded-md border border-border bg-surface p-6"
    >
      <header>
        <h3 className="text-xs uppercase tracking-wider text-text-muted">
          Public ↔ private stance
        </h3>
      </header>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ShiftBlock
          label="Pre-discussion"
          dist={shiftSummary.pre_stance_distribution}
        />
        <ShiftBlock
          label="Final"
          dist={shiftSummary.final_stance_distribution}
        />
      </div>
    </section>
  );
}

function ShiftBlock({
  label,
  dist,
}: {
  label: string;
  dist: Record<string, number>;
}) {
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-4">
      <p className="mb-2 text-xs uppercase tracking-wider text-text-muted">
        {label}
      </p>
      {Object.keys(dist).length === 0 ? (
        <p className="text-sm text-text-muted">No data.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {Object.entries(dist)
            .sort(([, a], [, b]) => (b as number) - (a as number))
            .map(([k, v]) => (
              <li
                key={k}
                className="flex items-baseline justify-between"
              >
                <span className="text-text-body">{humanizeStance(k)}</span>
                <span className="font-mono text-text-primary">{v}</span>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
