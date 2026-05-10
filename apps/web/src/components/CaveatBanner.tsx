// Phase 10B — caveat banner. Always visible alongside any report or
// stat. Elegant, not scary. Uses the locked palette: dark surface +
// muted body text + a single accent edge for trust labels.
//
// Phase 10B.5 — reframed as "How to read this report" so it feels
// like an explainer, not a legal disclaimer. Same content, gentler
// framing.

const DEFAULT_CAVEATS = [
  "Synthetic simulation, not a real customer interview",
  "Not a real-world forecast or revenue prediction",
  "Run-scoped — not representative of the whole market",
  "Simulated intent is not actual purchase behavior",
  "No launch / kill verdict — the report surfaces objections, proof needs, and audience reactions",
  "Evidence-backed, but still needs real-world validation",
];

export interface CaveatBannerProps {
  caveats?: string[];
  compact?: boolean;
}

export function CaveatBanner({
  caveats = DEFAULT_CAVEATS,
  compact = false,
}: CaveatBannerProps) {
  return (
    <div
      role="note"
      aria-label="How to read this report"
      className={`rounded-md border border-border bg-surface p-4 text-sm text-text-body ${compact ? "py-3" : ""}`}
      data-testid="caveat-banner"
    >
      <div className="mb-2 flex items-center gap-2">
        <span
          aria-hidden="true"
          className="inline-block h-2 w-2 rounded-full bg-accent"
        />
        <span className="font-medium uppercase tracking-wider text-xs text-accent">
          How to read this report
        </span>
      </div>
      <p className="mb-3 leading-relaxed text-text-body">
        Assembly simulates a run-scoped synthetic society using live
        market evidence. It is not a real customer interview or
        revenue prediction, but it helps surface likely objections,
        proof needs, and audience reactions before launch.
      </p>
      <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {caveats.map((c) => (
          <li
            key={c}
            className="flex items-start gap-2 text-text-body"
          >
            <span
              aria-hidden="true"
              className="mt-2 inline-block h-px w-3 bg-text-muted"
            />
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
