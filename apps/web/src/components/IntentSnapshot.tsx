// Phase 10B — simulated intent snapshot card.
// Always labeled as synthetic; closed-set intent labels only.
//
// 10B+ refinement: a segmented horizontal bar visualizes the
// proportions across three intent groupings (positive / comparing /
// resistant). Hovering a segment reveals label + count + percentage
// of the synthetic society.

import { ALLOWED_INTENT_LABELS, type IntentLabel } from "@/lib/types";
import { humanizeLabel } from "@/lib/labels";

const HUMAN: Record<IntentLabel, string> = {
  would_buy_now: "Would buy now",
  would_try_once: "Would try once",
  would_join_waitlist: "Would join waitlist",
  would_consider_if_proven: "Would consider if proven",
  would_share_with_friend: "Would share with a friend",
  would_compare_to_current_brand: "Would compare to current brand",
  loyal_to_current_alternative: "Loyal to current alternative",
  would_reject: "Would reject",
  would_block: "Would actively block",
};

type IntentGroup = "positive" | "comparing" | "resistant";

const INTENT_GROUP: Record<IntentLabel, IntentGroup> = {
  would_buy_now: "positive",
  would_try_once: "positive",
  would_join_waitlist: "positive",
  would_consider_if_proven: "positive",
  would_share_with_friend: "positive",
  would_compare_to_current_brand: "comparing",
  loyal_to_current_alternative: "resistant",
  would_reject: "resistant",
  would_block: "resistant",
};

const GROUP_FILL: Record<IntentGroup, string> = {
  positive: "bg-accent",
  comparing: "bg-text-muted",
  resistant: "bg-danger",
};

const GROUP_TEXT: Record<IntentGroup, string> = {
  positive: "text-accent",
  comparing: "text-text-muted",
  resistant: "text-danger",
};

export interface IntentSnapshotProps {
  intentDistribution: Record<string, number>;
  switchingDistribution?: Record<string, number>;
  societySize: number;
}

export function IntentSnapshot({
  intentDistribution,
  switchingDistribution,
  societySize,
}: IntentSnapshotProps) {
  const total = Object.values(intentDistribution).reduce(
    (a, b) => a + b,
    0,
  );
  const entries: [IntentLabel, number][] = ALLOWED_INTENT_LABELS.map(
    (label) =>
      [label, intentDistribution[label] ?? 0] as [IntentLabel, number],
  ).filter(([, v]) => v > 0);
  // Sort: positive first, then volume desc within group
  entries.sort((a, b) => {
    const ga = INTENT_GROUP[a[0]];
    const gb = INTENT_GROUP[b[0]];
    const order = { positive: 0, comparing: 1, resistant: 2 };
    if (order[ga] !== order[gb]) return order[ga] - order[gb];
    return b[1] - a[1];
  });

  // Build segmented-bar slices in the same order
  const slices = entries.map(([label, count]) => ({
    label,
    count,
    pct: total > 0 ? (count / total) * 100 : 0,
    group: INTENT_GROUP[label],
  }));

  return (
    <section
      data-testid="intent-snapshot"
      className="space-y-4 rounded-md border border-border bg-surface p-6"
    >
      <header className="space-y-1">
        <h3 className="text-lg font-semibold text-text-primary">
          Synthetic intent snapshot
        </h3>
        {/* Phase 10B.5 — explicit stance-vs-intent explainer so the
            reader doesn't read a "would_reject" segment as a
            contradiction with a receptive final stance. */}
        <p
          className="text-xs leading-relaxed text-text-muted"
          data-testid="intent-stance-explainer"
        >
          <span className="text-text-body">Stance</span> shows where
          personas landed after discussion.{" "}
          <span className="text-text-body">Intent</span> shows the
          next action they expressed inside the simulation. The two
          can diverge — a persona may end the discussion receptive
          but still need proof before they would buy.
        </p>
      </header>

      {entries.length === 0 ? (
        <p className="text-sm text-text-body">
          No intent records yet for this run.
        </p>
      ) : (
        <>
          {/* Segmented bar — visual overview before the detail list */}
          <div
            className="space-y-2"
            data-testid="intent-segmented-bar"
          >
            <div
              className="flex h-3 w-full overflow-hidden rounded-sm bg-border"
              role="img"
              aria-label="Synthetic intent distribution"
            >
              {slices.map((s) => (
                <span
                  key={s.label}
                  className={`${GROUP_FILL[s.group]} transition-opacity hover:opacity-75`}
                  style={{ width: `${s.pct}%` }}
                  title={`${HUMAN[s.label] ?? s.label} — ${s.count} ${s.count === 1 ? "persona" : "personas"} — ${Math.round(s.pct)}%`}
                  data-testid="intent-segment"
                  data-intent-label={s.label}
                  data-intent-count={s.count}
                  data-intent-pct={Math.round(s.pct)}
                />
              ))}
            </div>
            <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
              <LegendDot tone="accent" label="Positive intent" />
              <LegendDot tone="muted" label="Comparing" />
              <LegendDot tone="danger" label="Resistant / loyal" />
            </p>
          </div>

          <p className="text-xs text-text-muted">
            Synthetic expressed intent inside this simulation — not
            real-world purchase behavior. n =
            <span className="ml-1 font-mono text-text-body">
              {societySize}
            </span>
          </p>

          <ul className="space-y-2">
            {entries.map(([label, count]) => {
              const pct = total > 0 ? (count / total) * 100 : 0;
              const group = INTENT_GROUP[label];
              return (
                <li
                  key={label}
                  className="flex items-center gap-3 rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm"
                >
                  <span
                    className={`min-w-[1.5rem] text-right font-mono ${GROUP_TEXT[group]}`}
                    data-testid="intent-count"
                  >
                    {count}
                  </span>
                  <span className="flex-1 text-text-body">
                    {HUMAN[label] ?? label}
                  </span>
                  <span
                    className="h-1.5 w-24 overflow-hidden rounded-sm bg-border"
                    aria-label={`${pct.toFixed(0)}%`}
                  >
                    <span
                      className={`block h-full ${GROUP_FILL[group]}`}
                      style={{ width: `${pct}%` }}
                    />
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {switchingDistribution &&
      Object.keys(switchingDistribution).length > 0 ? (
        <details className="rounded-md border border-border bg-surface-elevated p-3 text-sm text-text-body">
          <summary className="cursor-pointer font-medium text-text-primary">
            Switching status (synthetic)
          </summary>
          <ul className="mt-2 space-y-1">
            {Object.entries(switchingDistribution).map(([k, v]) => (
              <li key={k} className="flex justify-between">
                {/* Phase 10B.5 — humanize switching-status snake_case
                    (no_current_alternative → "No current alternative",
                    refuses_switching → "Refuses to switch", etc.) */}
                <span className="text-text-body">{humanizeLabel(k)}</span>
                <span className="font-mono text-text-muted">{v}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function LegendDot({
  tone,
  label,
}: {
  tone: "accent" | "muted" | "danger";
  label: string;
}) {
  const fill =
    tone === "accent"
      ? "bg-accent"
      : tone === "danger"
        ? "bg-danger"
        : "bg-text-muted";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={`inline-block h-1.5 w-1.5 rounded-full ${fill}`}
      />
      {label}
    </span>
  );
}
