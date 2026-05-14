"use client";
// Phase 10B.8 — landing page polish.
//
// Hero (wordmark + sharper sub-tagline + CTAs lifted above feature
// triad) → Sample-Report Proof Teaser → Live Simulation Preview →
// What Founders Learn → BriefForm (renamed header + CaveatBanner) →
// Market Reaction Report sample. Same locked palette, same dark-lab
// feel, sharper founder-facing language.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { BriefForm } from "@/components/BriefForm";
import { CaveatBanner } from "@/components/CaveatBanner";
import { LiveSimulationPreview } from "@/components/LiveSimulationPreview";
import { MetaReportSample } from "@/components/MetaReportSample";

export default function HomePage() {
  const router = useRouter();

  return (
    <div id="product" className="space-y-24">
      {/* ───────────────────── HERO ───────────────────── */}
      <section className="relative -mx-4 overflow-hidden px-4 pb-12 pt-6 sm:-mx-6 sm:px-6 lg:-mx-12 lg:px-12">
        <div
          aria-hidden
          className="bg-grid-fade pointer-events-none absolute inset-0 -z-10"
        />

        <div className="mx-auto max-w-5xl space-y-12">
          {/* Top label */}
          <p className="flex items-center justify-center gap-2 font-mono text-xs uppercase tracking-[0.25em] text-text-muted">
            <span
              aria-hidden
              className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
            />
            PREDICTIVE PUBLIC OPINION ENGINE
          </p>

          {/* Wordmark — metallic gradient + lime brackets */}
          <div className="space-y-6 text-center">
            <h1 className="flex items-center justify-center gap-6 font-sans text-7xl tracking-[0.02em] sm:text-8xl">
              <span aria-hidden className="brand-bracket font-light">
                [
              </span>
              <span className="brand-wordmark font-extrabold uppercase">
                Assembly
              </span>
              <span aria-hidden className="brand-bracket font-light">
                ]
              </span>
            </h1>

            <p className="mx-auto max-w-3xl text-balance text-3xl font-light leading-tight text-text-primary sm:text-4xl">
              Model where public opinion lands &mdash; before it does.
            </p>
            <p className="mx-auto inline-block border-b border-accent/40 pb-1 font-mono text-sm uppercase tracking-[0.3em] text-accent">
              Rehearse reality.
            </p>
          </div>

          {/* Sharper sub-tagline */}
          <p className="mx-auto max-w-2xl text-center text-base leading-relaxed text-text-body">
            Assembly builds an evidence-grounded market room around
            your product, then shows who leans in, who pushes back,
            and why.
          </p>

          {/* Primary CTAs — lifted above feature cards */}
          <div className="flex flex-wrap items-center justify-center gap-4">
            <Link
              href="#submit-brief"
              className="inline-flex items-center justify-center rounded-md bg-accent px-6 py-3 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow"
              data-testid="hero-primary-cta"
            >
              Run a simulation
            </Link>
            <Link
              href="/sample-report"
              className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-5 py-3 text-sm text-text-body transition-colors hover:border-accent-border hover:text-accent"
              data-testid="view-sample-report-cta"
            >
              View sample report <span aria-hidden>→</span>
            </Link>
          </div>

          {/* Three feature cards */}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            <FeatureCard
              icon="◎"
              eyebrow="SIMULATE"
              headline="Evidence-grounded personas debate your product"
              body="Hundreds of synthetic personas — grounded in publicly available evidence about how real customers behave — argue your product across multiple rounds. Not surveys, not focus groups. A live debate."
            />
            <FeatureCard
              icon="↻"
              eyebrow="EVOLVE"
              headline="Opinions shift and converge"
              body="Personas challenge each other, shift positions, and form emergent consensus. Watch opinions move in real time — or watch genuine disagreement hold firm."
            />
            <FeatureCard
              icon="◆"
              eyebrow="PREDICT"
              headline="Market Reaction Report of the outcome"
              body="Get a Market Reaction Report — who ended up FOR, who stayed AGAINST, what argument was decisive, and where opinion is actually headed."
            />
          </div>
        </div>
      </section>

      {/* ─────────── SAMPLE REPORT PROOF TEASER ─────────── */}
      <section className="mx-auto max-w-5xl">
        <article className="relative overflow-hidden rounded-2xl border border-accent-border/40 bg-surface/80 p-8 shadow-[0_0_40px_-12px_rgba(170,255,0,0.25)] sm:p-12">
          <div
            aria-hidden
            className="bg-grid pointer-events-none absolute inset-0 -z-10 opacity-30"
          />
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1.4fr_1fr] lg:items-center">
            <div className="space-y-5">
              <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.28em] text-accent">
                <span
                  aria-hidden
                  className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
                />
                PROOF, NOT PROMISES
              </p>
              <h2 className="text-3xl font-bold leading-tight tracking-tight text-text-primary sm:text-4xl">
                See the report before you run one.
              </h2>
              <p className="text-base leading-relaxed text-text-body">
                Every simulation produces a Market Reaction Report —
                public sentiment, persuasion drivers, debate-shift
                markers, and a split-confidence verdict. Read a real
                run end-to-end before you submit a product brief.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link
                  href="/sample-report"
                  className="inline-flex items-center gap-2 rounded-md bg-accent px-5 py-3 text-sm font-semibold text-background transition-shadow hover:shadow-accent-glow"
                  data-testid="proof-cta-view-sample"
                >
                  View sample report <span aria-hidden>→</span>
                </Link>
                <Link
                  href="#submit-brief"
                  className="inline-flex items-center rounded-md border border-border bg-surface px-5 py-3 text-sm text-text-body transition-colors hover:border-accent-border hover:text-accent"
                >
                  Run your own
                </Link>
              </div>
            </div>
            <ul className="space-y-3 rounded-xl border border-border bg-surface-elevated/40 p-5 text-sm text-text-body">
              <ProofRow label="Public sentiment" value="bimodal · receptive ↔ resistant" />
              <ProofRow label="Top persuasion driver" value="screenless ≠ another notification source" />
              <ProofRow label="Top objection" value="$79 vs. existing Apple Watch breathe nudges" />
              <ProofRow label="Debate shift" value="9 of 24 personas moved" />
              <ProofRow label="Confidence" value="split · further testing recommended" />
            </ul>
          </div>
        </article>
      </section>

      {/* ──────────────── LIVE SIMULATION PREVIEW ──────────────── */}
      <section className="relative -mx-4 px-4 sm:-mx-6 sm:px-6 lg:-mx-12 lg:px-12">
        <div
          aria-hidden
          className="bg-grid pointer-events-none absolute inset-0 -z-10 opacity-40"
        />
        <div className="mx-auto max-w-5xl">
          <LiveSimulationPreview />
        </div>
      </section>

      {/* ──────────────── WHAT FOUNDERS LEARN ──────────────── */}
      <section className="mx-auto max-w-5xl space-y-8">
        <header className="space-y-3 text-center">
          <p className="flex items-center justify-center gap-2 font-mono text-xs uppercase tracking-[0.25em] text-text-muted">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
            />
            WHY FOUNDERS USE ASSEMBLY
          </p>
          <h2 className="text-4xl font-bold tracking-tight text-text-primary">
            What founders learn.
          </h2>
          <p className="mx-auto max-w-2xl text-sm leading-relaxed text-text-muted">
            One simulation. Five questions that usually cost months of
            interviews and ad spend to answer.
          </p>
        </header>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <LearnTile label="Who is receptive" body="Which audience segments lean in, and what story actually lands for them." />
          <LearnTile label="Who resists" body="Which segments push back, and whether the resistance is principled or solvable." />
          <LearnTile label="What objections matter" body="The objections that compound across the room — and the ones that quietly fade." />
          <LearnTile label="What proof changes minds" body="Which proof points (price, founder story, demo, social signal) actually move ballots." />
          <LearnTile label="What positioning breaks" body="The framings that crater — the ones not even your most receptive audience defends." />
        </div>
      </section>

      {/* ──────────────── SUBMIT A BRIEF ──────────────── */}
      <section id="submit-brief" className="mx-auto max-w-4xl space-y-6">
        <header className="space-y-3 text-center">
          <p className="flex items-center justify-center gap-2 font-mono text-xs uppercase tracking-[0.25em] text-text-muted">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
            />
            RUN YOUR OWN
          </p>
          <h2 className="text-4xl font-bold tracking-tight text-text-primary">
            Submit a product. See where the market pushes back.
          </h2>
          <p className="mx-auto max-w-2xl text-sm leading-relaxed text-text-muted">
            Describe your product below. Assembly builds a fresh
            evidence-grounded room of synthetic personas, runs the
            simulation, and returns your Market Reaction Report.
          </p>
        </header>

        <BriefForm
          onCreated={(resp) => {
            router.push(`/run/${resp.run_id}`);
          }}
        />

        <CaveatBanner compact />
      </section>

      {/* ──────────── MARKET REACTION REPORT (deliverable) ──────────── */}
      <section className="relative -mx-4 px-4 sm:-mx-6 sm:px-6 lg:-mx-12 lg:px-12">
        <div
          aria-hidden
          className="bg-grid pointer-events-none absolute inset-0 -z-10 opacity-30"
        />
        <div className="mx-auto max-w-6xl">
          <MetaReportSample />
        </div>
      </section>
    </div>
  );
}

function FeatureCard({
  icon,
  eyebrow,
  headline,
  body,
}: {
  icon: string;
  eyebrow: string;
  headline: string;
  body: string;
}) {
  return (
    <article className="group relative flex h-full flex-col gap-5 rounded-xl border border-border bg-surface/70 p-7 backdrop-blur-sm transition-all hover:border-accent-border/60 hover:bg-surface/85 hover:shadow-[0_0_28px_-12px_rgba(170,255,0,0.25)]">
      <span
        aria-hidden
        className="flex h-11 w-11 items-center justify-center rounded-md border border-accent-border bg-accent-soft font-mono text-xl text-accent"
      >
        {icon}
      </span>
      <div className="space-y-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-accent">
          {eyebrow}
        </p>
        <h3 className="text-xl font-bold leading-snug tracking-tight text-text-primary sm:text-[1.35rem]">
          {headline.toUpperCase()}
        </h3>
      </div>
      <p className="mt-auto text-sm leading-relaxed text-text-body">
        {body}
      </p>
    </article>
  );
}

function ProofRow({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex flex-col gap-1 border-b border-border/60 pb-2 last:border-b-0 last:pb-0 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
      <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-muted">
        {label}
      </span>
      <span className="text-right font-mono text-xs text-accent">
        {value}
      </span>
    </li>
  );
}

function LearnTile({ label, body }: { label: string; body: string }) {
  return (
    <article className="flex h-full flex-col gap-3 rounded-xl border border-border bg-surface/70 p-5 transition-colors hover:border-accent-border/60">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-accent">
        {label}
      </p>
      <p className="text-sm leading-relaxed text-text-body">{body}</p>
    </article>
  );
}
