// Phase 10B.7 — Terms of use.
//
// Founder-stage terms — sets the expectation that Assembly output
// is synthetic, not a real market forecast or sales prediction,
// and makes the "use at your own discretion" stance explicit.

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms — Assembly",
  description:
    "Terms of use for the Assembly synthetic-society simulator.",
};

export default function TermsPage() {
  return (
    <article
      className="mx-auto max-w-3xl space-y-8 text-text-body"
      data-testid="terms-page"
    >
      <header className="space-y-3">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-accent">
          Terms
        </p>
        <h1 className="text-4xl font-bold tracking-tight text-text-primary">
          Terms of use
        </h1>
        <p className="font-mono text-xs text-text-muted">
          Last updated: May 2026
        </p>
      </header>

      <Section title="What Assembly is">
        <p>
          Assembly is a synthetic-society simulator. It takes a
          product brief, builds a run-scoped population of AI
          personas grounded in publicly available market evidence,
          and returns a structured report of the simulated reactions
          — receptive, uncertain, resistant — and the arguments that
          shaped them.
        </p>
      </Section>

      <Section title="What Assembly is not">
        <p>
          Assembly does not produce real market forecasts, sales
          predictions, or launch verdicts. Personas are synthetic
          and never represent specific real individuals. Output is
          intended to surface likely objections, proof needs, and
          audience reactions to test in the real world — not to
          replace real customer research.
        </p>
      </Section>

      <Section title="Your responsibilities">
        <p>
          You agree to provide accurate product information in
          briefs, to not submit content that infringes third-party
          rights, and to use Assembly's outputs as one signal among
          many — not as the sole basis for go-to-market, hiring,
          or capital-allocation decisions.
        </p>
      </Section>

      <Section title="Limitation of liability">
        <p>
          Assembly is provided as-is. The team behind Assembly is
          not liable for business outcomes arising from your use of
          the platform. Synthetic-society simulations are
          interpretive, not deterministic.
        </p>
      </Section>

      <Section title="Changes">
        <p>
          We may update these terms as Assembly evolves. Material
          changes will be announced via the product UI or via the
          email address you provided in any brief or contact-form
          submission.
        </p>
      </Section>

      <Section title="Contact">
        <p>
          Questions about these terms? Email{" "}
          <a
            href="mailto:team@assemblysimulator.com"
            className="font-mono text-accent transition-colors hover:underline"
          >
            team@assemblysimulator.com
          </a>
          .
        </p>
      </Section>
    </article>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-bold tracking-tight text-text-primary">
        {title}
      </h2>
      <div className="space-y-3 text-sm leading-relaxed">{children}</div>
    </section>
  );
}
