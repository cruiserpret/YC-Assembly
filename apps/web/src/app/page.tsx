"use client";
// Phase 10B.5+ — redesigned landing page.
//
// Hero (metallic [ ASSEMBLY ] wordmark + tagline + feature triad) →
// Live Simulation Preview (auto-scrolling TikTok-ban mock) →
// God's Eye View deliverable sample → then the existing BriefForm
// (untouched — same input fields, same validation, same backend
// payload) → CaveatBanner. Same locked palette, same dark-lab feel,
// just laid out with more storytelling around the form.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { BriefForm } from "@/components/BriefForm";
import { CaveatBanner } from "@/components/CaveatBanner";
import { LiveSimulationPreview } from "@/components/LiveSimulationPreview";
import { MetaReportSample } from "@/components/MetaReportSample";

export default function HomePage() {
  const router = useRouter();

  return (
    <div className="space-y-24">
      {/* ───────────────────── HERO ───────────────────── */}
      <section className="relative -mx-4 overflow-hidden px-4 pb-12 pt-6 sm:-mx-6 sm:px-6 lg:-mx-12 lg:px-12">
        {/* Subtle grid background */}
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

          {/* Sub-tagline */}
          <p className="mx-auto max-w-2xl text-center text-base leading-relaxed text-text-body">
            Spawn dozens of evidence-anchored AI personas. Watch them
            argue, shift, and converge &mdash; producing a Meta Report
            of where consensus is actually headed.
          </p>

          {/* Three feature cards — stronger / YC-demo styling */}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            <FeatureCard
              icon="◎"
              eyebrow="SIMULATE"
              headline="Real agents debate your question"
              body="Hundreds of AI agents with distinct real-world personas argue your question across multiple rounds — grounded in what people actually say online, not what they say in surveys."
            />
            <FeatureCard
              icon="↻"
              eyebrow="EVOLVE"
              headline="Opinions shift and converge"
              body="Agents challenge each other, shift positions, and form emergent consensus. Watch opinions move in real time — or watch genuine disagreement hold firm."
            />
            <FeatureCard
              icon="◆"
              eyebrow="PREDICT"
              headline="Meta Report of the outcome"
              body="Get a Meta Report — who ended up FOR, who stayed AGAINST, what argument was decisive, and where opinion is actually headed."
            />
          </div>

          {/* Secondary CTA — view sample report */}
          <div className="flex flex-wrap items-center justify-center gap-4 pt-4">
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
        </div>
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

      {/* ──────────────── SUBMIT A BRIEF (unchanged form) ──────────────── */}
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
            Submit a topic. Rehearse the future.
          </h2>
          <p className="mx-auto max-w-2xl text-sm leading-relaxed text-text-muted">
            Describe your product or topic below. Assembly builds a
            fresh synthetic society from live evidence, runs seven
            rounds of debate, and returns your Meta Report.
          </p>
        </header>

        {/* The BriefForm itself is untouched — same fields, validation,
            and backend payload. The redesign happens around it. */}
        <BriefForm
          onCreated={(resp) => {
            router.push(`/run/${resp.run_id}`);
          }}
        />

        <CaveatBanner compact />
      </section>

      {/* ──────────────── META REPORT (deliverable — last section) ──────────────── */}
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

