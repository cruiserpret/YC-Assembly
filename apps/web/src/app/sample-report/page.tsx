// Phase 10B.5 — sample report page.
//
// A self-contained snapshot of a real Assembly run output, baked
// into the bundle so a YC reviewer (or any visitor) can preview
// the report shape without waiting 12–20 minutes for a live run.
//
// Source: PantryPulse run 0d7ebc2d-e2ae-468f-9f9d-dee1cb8880fa
// (Phase 10B.4 verification rerun, 16/16 J-criteria PASS).
//
// Clearly labeled as a "Sample report" — never presented as live
// output.

import Link from "next/link";
import { CaveatBanner } from "@/components/CaveatBanner";

const SAMPLE = {
  product_name: "PantryPulse",
  run_label: "Sample run · captured 2026-05-10",
  headline:
    "The synthetic society finished with limited receptive: 4 of 24 personas ended receptive, with 4 shifting toward stronger interest during discussion.",
  brief_summary:
    "PantryPulse is a smart kitchen inventory scanner with a still-image camera (physical shutter, visible LED), barcode + NFC scanning, and reusable food tags. $149 one-time, $7.99/mo optional Plus subscription, $19.99 12-pack tag accessory.",
  evidence_flavor:
    "Evidence base: search results, competitor / product pages, buyer-language from YouTube comments where available.",
  stance_distribution: {
    receptive: 4,
    uncertain: 16,
    resistant: 4,
  },
  best_fit:
    "Best-fit audience: urban renters, busy parents, college students who already understand the pain this product solves, especially people familiar with Samsung Family Hub-style alternatives but frustrated by their format or durability.",
  best_fit_roles: [
    { display: "Performance-focused buyers", count: 1, total: 4 },
    { display: "Samsung Family Hub Refrigerator users", count: 1, total: 4 },
    { display: "Trust-seekers", count: 1, total: 1 },
    { display: "People with a clear use-case match", count: 1, total: 4 },
  ],
  hardest_to_convince:
    "Price-sensitive buyers and buyers with strong unresolved objections were the hardest to move on this run. They centered on price-to-value and trust in claims before they could be convinced.",
  hardest_roles: [
    { display: "Price-sensitive buyers", count: 3, total: 3 },
    {
      display: "Buyers with strong unresolved objections",
      count: 1,
      total: 2,
    },
  ],
  top_objections: [
    "$149 + $7.99/mo adds up fast — competing with a free habit (notes app, AnyList)",
    "Workflow friction — does scanning groceries actually save time vs manual logging?",
    "Privacy: how are still images of shelves/labels stored and deleted?",
    "Camera with physical shutter is reassuring, but third-party cert would close the loop",
  ],
  top_proof_needs: [
    "30-second real-grocery-trip workflow demo",
    "Side-by-side vs AnyList showing input-time saved",
    "Battery / charge-cycle data under realistic use",
    "Privacy white-paper: still-image lifecycle + on-device retention",
  ],
  receptive_strictness_summary:
    "Of 5 RECEPTIVE ballots scanned, all 5 were kept by the v3 strictness audit (clear positive driver + use-case fit, no killer-proof phrasing). Zero RECEPTIVE labels needed downgrade — the discussion was well-calibrated at generation.",
};

export default function SampleReportPage() {
  return (
    <div
      className="mx-auto max-w-4xl space-y-10"
      data-testid="sample-report-page"
    >
      {/* Header / sample badge */}
      <header className="space-y-3">
        <div className="flex items-center gap-3">
          <span
            data-testid="sample-report-badge"
            className="rounded-md border border-accent-border bg-accent-soft px-3 py-1 font-mono text-xs uppercase tracking-wider text-accent"
          >
            Sample report
          </span>
          <span className="text-xs text-text-muted">
            {SAMPLE.run_label}
          </span>
        </div>
        <h1 className="text-3xl tracking-tight text-text-primary sm:text-4xl">
          {SAMPLE.product_name}
        </h1>
        <p className="text-sm leading-relaxed text-text-muted">
          {SAMPLE.brief_summary}
        </p>
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-xs text-accent hover:underline"
        >
          ← Run your own product
        </Link>
      </header>

      {/* Headline */}
      <section
        className="space-y-2 rounded-md border border-accent-border/50 bg-surface p-6"
        data-testid="sample-headline"
      >
        <p className="text-xs uppercase tracking-wider text-accent">
          Result
        </p>
        <p className="text-lg leading-relaxed text-text-primary">
          {SAMPLE.headline}
        </p>
        <div className="flex flex-wrap gap-6 pt-3 text-sm">
          <Stat
            label="Receptive"
            value={SAMPLE.stance_distribution.receptive}
            tone="accent"
          />
          <Stat
            label="Uncertain"
            value={SAMPLE.stance_distribution.uncertain}
            tone="muted"
          />
          <Stat
            label="Resistant"
            value={SAMPLE.stance_distribution.resistant}
            tone="danger"
          />
        </div>
      </section>

      {/* Audience cards */}
      <section
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
        data-testid="sample-audience"
      >
        <article className="space-y-3 rounded-md border border-accent-border/50 bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-accent">
            Best-fit audience
          </h4>
          <p className="text-sm leading-relaxed text-text-body">
            {SAMPLE.best_fit}
          </p>
          <p className="text-[11px] uppercase tracking-wider text-text-muted">
            Simulation roles in this audience
          </p>
          <ul className="space-y-1.5 text-sm">
            {SAMPLE.best_fit_roles.map((r) => (
              <li
                key={r.display}
                className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                <span className="text-text-muted">{r.display}</span>
                <span className="font-mono text-accent">
                  {r.count}
                  <span className="ml-1 text-text-muted">/ {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        </article>
        <article className="space-y-3 rounded-md border border-danger/40 bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-danger">
            Hardest-to-convince audience
          </h4>
          <p className="text-sm leading-relaxed text-text-body">
            {SAMPLE.hardest_to_convince}
          </p>
          <p className="text-[11px] uppercase tracking-wider text-text-muted">
            Simulation roles in this audience
          </p>
          <ul className="space-y-1.5 text-sm">
            {SAMPLE.hardest_roles.map((r) => (
              <li
                key={r.display}
                className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                <span className="text-text-muted">{r.display}</span>
                <span className="font-mono text-danger">
                  {r.count}
                  <span className="ml-1 text-text-muted">/ {r.total}</span>
                </span>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Top objections + proof needs */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <article className="space-y-3 rounded-md border border-border bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Top objections
          </h4>
          <ul className="space-y-2 text-sm leading-relaxed text-text-body">
            {SAMPLE.top_objections.map((o) => (
              <li
                key={o}
                className="rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                {o}
              </li>
            ))}
          </ul>
        </article>
        <article className="space-y-3 rounded-md border border-border bg-surface p-5">
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Proof needs
          </h4>
          <ul className="space-y-2 text-sm leading-relaxed text-text-body">
            {SAMPLE.top_proof_needs.map((p) => (
              <li
                key={p}
                className="rounded-md border border-border bg-surface-elevated px-3 py-2"
              >
                {p}
              </li>
            ))}
          </ul>
        </article>
      </section>

      {/* Stance strictness note */}
      <section className="rounded-md border border-border bg-surface p-5 text-sm leading-relaxed text-text-body">
        <p className="mb-2 font-mono text-xs uppercase tracking-wider text-text-muted">
          Stance calibration
        </p>
        <p>{SAMPLE.receptive_strictness_summary}</p>
      </section>

      {/* Evidence flavor */}
      <section className="rounded-md border border-border bg-surface p-5 text-sm leading-relaxed text-text-body">
        <p className="mb-2 font-mono text-xs uppercase tracking-wider text-text-muted">
          Evidence base
        </p>
        <p>{SAMPLE.evidence_flavor}</p>
      </section>

      {/* Trust */}
      <CaveatBanner />

      {/* CTA back home */}
      <section className="flex flex-wrap items-center justify-between gap-4 rounded-md border border-border bg-surface p-5">
        <p className="text-sm text-text-body">
          This is a pre-generated sample. Run your own brief to see a
          live synthetic society react to your product.
        </p>
        <Link
          href="/"
          className="inline-flex items-center justify-center rounded-md bg-accent px-5 py-3 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow"
        >
          Run your own product
        </Link>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "accent" | "muted" | "danger";
}) {
  const cls =
    tone === "accent"
      ? "text-accent"
      : tone === "danger"
        ? "text-danger"
        : "text-text-muted";
  return (
    <div className="flex flex-col">
      <span className={`font-mono text-2xl ${cls}`}>{value}</span>
      <span className="text-xs uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </div>
  );
}
