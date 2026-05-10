// Phase 10B — cohort cards. Backend exposes only a high-level cohort
// summary by default (count + sizes). This component renders that
// summary plus any per-cohort detail the founder report includes
// under most_receptive / most_resistant.

import type { CohortsPayload, FounderReport } from "@/lib/types";

export interface CohortCardsProps {
  cohorts: CohortsPayload;
  report?: FounderReport | null;
}

export function CohortCards({ cohorts, report }: CohortCardsProps) {
  const sizes = cohorts.cohort_sizes ?? [];
  const cohortCount = cohorts.cohort_count ?? sizes.length;
  const receptive = (report?.most_receptive_cohorts ?? []) as Array<
    Record<string, unknown>
  >;
  const resistant = (report?.most_resistant_cohorts ?? []) as Array<
    Record<string, unknown>
  >;

  return (
    <section
      data-testid="cohort-cards"
      className="space-y-4 rounded-md border border-border bg-surface p-6"
    >
      <header className="space-y-1">
        <h3 className="text-lg font-semibold text-text-primary">
          Run-scoped synthetic cohorts
        </h3>
        <p className="text-sm text-text-muted">
          Cohorts are computed from this run only. Not real-world market
          segments.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {sizes.map((size, i) => (
          <div
            key={i}
            data-testid="cohort-card"
            className="rounded-md border border-border bg-surface-elevated p-4"
          >
            <p className="text-xs uppercase tracking-wider text-text-muted">
              Synthetic cohort {i + 1}
            </p>
            <p className="mt-1 font-mono text-2xl text-accent">{size}</p>
            <p className="text-xs text-text-body">personas</p>
          </div>
        ))}
        {sizes.length === 0 ? (
          <div className="rounded-md border border-border bg-surface-elevated p-4 text-sm text-text-muted">
            No cohorts yet — pipeline still in progress or this run had
            no cohort stage output.
          </div>
        ) : null}
      </div>

      {(receptive.length > 0 || resistant.length > 0) && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <CohortList
            label="Most receptive (synthetic)"
            entries={receptive}
            tone="accent"
          />
          <CohortList
            label="Most resistant (synthetic)"
            entries={resistant}
            tone="muted"
          />
        </div>
      )}

      <p className="text-xs text-text-muted">
        {cohortCount} cohorts · {sizes.reduce((a, b) => a + b, 0)} personas
      </p>
    </section>
  );
}

function CohortList({
  label,
  entries,
  tone,
}: {
  label: string;
  entries: Array<Record<string, unknown>>;
  tone: "accent" | "muted";
}) {
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-4">
      <p
        className={`text-xs uppercase tracking-wider ${tone === "accent" ? "text-accent" : "text-text-muted"}`}
      >
        {label}
      </p>
      {entries.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">
          No data yet for this segment.
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {entries.slice(0, 6).map((c, i) => {
            const labelText =
              (c.cohort_label as string | undefined) ||
              (c.label as string | undefined) ||
              `Cohort ${i + 1}`;
            const stance =
              (c.dominant_stance as string | undefined) ||
              (c.stance as string | undefined) ||
              "—";
            return (
              <li
                key={i}
                className="rounded-md border border-border/60 bg-surface px-3 py-2 text-sm"
              >
                <p className="text-text-primary">{labelText}</p>
                <p className="text-xs text-text-muted">stance: {stance}</p>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
