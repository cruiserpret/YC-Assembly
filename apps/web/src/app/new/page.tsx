"use client";
// Dedicated /new submit page — uses the same polished BriefForm
// component as the homepage "Submit a product brief" section (mounted
// at `/#submit-brief`). Previous IntakeForm posted to the legacy
// /simulations endpoint and didn't go through the current Phase 10B+/
// 12A.10/12F.1 live pipeline; founders who landed here ended up on a
// dead-end form.

import { useRouter } from "next/navigation";

import { BriefForm } from "@/components/BriefForm";
import { CaveatBanner } from "@/components/CaveatBanner";

export default function NewSimulationPage() {
  const router = useRouter();

  return (
    <section
      id="submit-brief"
      className="mx-auto max-w-4xl space-y-6 py-8"
    >
      <header className="space-y-3 text-center">
        <p className="flex items-center justify-center gap-2 font-mono text-xs uppercase tracking-[0.25em] text-text-muted">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_8px_rgba(170,255,0,0.6)]"
          />
          RUN YOUR OWN
        </p>
        <h1 className="text-4xl font-bold tracking-tight text-text-primary">
          Submit a product. See where the market pushes back.
        </h1>
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
  );
}
