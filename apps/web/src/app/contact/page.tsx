// Phase 10B.7 — Contact Us page.
//
// Single-card layout: hero copy on the left, form on the right.
// Form lives in `ContactForm` (client component) so this page can
// stay a server component and prerender clean.

import type { Metadata } from "next";
import { ContactForm } from "@/components/ContactForm";

export const metadata: Metadata = {
  title: "Contact — Assembly",
  description:
    "Reach the Assembly team — questions about agent simulations, custom runs, or YC-stage partnerships.",
};

export default function ContactPage() {
  return (
    <div
      className="mx-auto max-w-5xl"
      data-testid="contact-page"
    >
      <section className="grid grid-cols-1 gap-10 md:grid-cols-[1.1fr_1fr] md:items-start">
        <header className="space-y-5">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-accent">
            Contact us
          </p>
          <h1 className="text-4xl font-bold tracking-tight text-text-primary sm:text-5xl">
            Talk to the team.
          </h1>
          <p className="text-base leading-relaxed text-text-body">
            Questions about how Assembly works, custom runs, or
            something a YC reviewer would want to ask? Send us a
            note. We read every message.
          </p>
          <div className="space-y-2 rounded-lg border border-border bg-surface p-5 text-sm text-text-body">
            <p className="font-mono text-[11px] uppercase tracking-wider text-text-muted">
              Or email us directly
            </p>
            <a
              href="mailto:team@assemblysimulator.com"
              className="font-mono text-base text-accent transition-colors hover:underline"
            >
              team@assemblysimulator.com
            </a>
          </div>
        </header>

        <ContactForm />
      </section>
    </div>
  );
}
