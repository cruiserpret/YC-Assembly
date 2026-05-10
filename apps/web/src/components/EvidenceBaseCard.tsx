"use client";
// Phase 10B+ refinement — Evidence base indicator card.
//
// Surfaces a compact set of "is this grounded in real evidence?"
// counts to reassure the founder that the synthetic society
// wasn't fabricated — accepted evidence items, signals extracted,
// personas built, and quality gates passed. Numbers come from
// the per-run audit endpoint; on failure we degrade gracefully
// to a simpler "personas + gates" view from data already on
// the page.

import { useEffect, useState } from "react";
import { getAssemblyAudit } from "@/lib/api";
import type { PersonasPayload } from "@/lib/types";

export interface EvidenceBaseCardProps {
  runId: string;
  personas?: PersonasPayload | null;
}

interface EvidenceCounts {
  acceptedEvidence: number | null;
  signalCount: number | null;
  personaCount: number | null;
  gatesPassed: number | null;
  gatesTotal: number | null;
}

export function EvidenceBaseCard({
  runId,
  personas,
}: EvidenceBaseCardProps) {
  const [counts, setCounts] = useState<EvidenceCounts>({
    acceptedEvidence: null,
    signalCount: null,
    personaCount: personas?.persona_count ?? null,
    gatesPassed: null,
    gatesTotal: null,
  });

  useEffect(() => {
    let cancelled = false;
    getAssemblyAudit(runId)
      .then((audit) => {
        if (cancelled || !audit) return;
        const eq = audit.evidence_quality as
          | { accepted_count?: number }
          | null
          | undefined;
        const es = audit.evidence_signals as
          | { total_signals_emitted?: number }
          | null
          | undefined;
        const pqg = audit.persona_quality_gates as
          | { gate_results?: Record<string, boolean>; compressed_count?: number }
          | null
          | undefined;
        const gates = pqg?.gate_results
          ? Object.values(pqg.gate_results)
          : [];
        setCounts((prev) => ({
          ...prev,
          acceptedEvidence: eq?.accepted_count ?? null,
          signalCount: es?.total_signals_emitted ?? null,
          personaCount:
            pqg?.compressed_count ?? prev.personaCount,
          gatesPassed: gates.length
            ? gates.filter(Boolean).length
            : prev.gatesPassed,
          gatesTotal: gates.length || prev.gatesTotal,
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Fallback gates from the personas payload's quality_gates_summary
  useEffect(() => {
    if (!personas?.quality_gates_summary) return;
    const entries = Object.values(personas.quality_gates_summary);
    setCounts((prev) => ({
      ...prev,
      gatesPassed:
        prev.gatesPassed ?? entries.filter(Boolean).length,
      gatesTotal: prev.gatesTotal ?? entries.length,
    }));
  }, [personas]);

  // Hide entirely if we couldn't get any signal at all.
  const hasAny =
    counts.acceptedEvidence !== null ||
    counts.signalCount !== null ||
    counts.personaCount !== null ||
    counts.gatesTotal !== null;
  if (!hasAny) return null;

  return (
    <section
      data-testid="evidence-base-card"
      className="rounded-md border border-border bg-surface p-5"
    >
      <header className="mb-3 flex items-baseline justify-between gap-4">
        <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
          Evidence base
        </h4>
        <p className="text-[11px] text-text-muted">
          What this synthetic society is grounded in
        </p>
      </header>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          value={counts.acceptedEvidence}
          label="Evidence items"
          hint="accepted from live retrieval"
        />
        <Stat
          value={counts.signalCount}
          label="Evidence signals"
          hint="extracted from accepted items"
        />
        <Stat
          value={counts.personaCount}
          label="Synthetic personas"
          hint="built from those signals"
        />
        <Stat
          value={
            counts.gatesPassed !== null && counts.gatesTotal
              ? `${counts.gatesPassed}/${counts.gatesTotal}`
              : null
          }
          label="Quality gates"
          hint="passed before persisting personas"
        />
      </ul>
    </section>
  );
}

function Stat({
  value,
  label,
  hint,
}: {
  value: number | string | null;
  label: string;
  hint: string;
}) {
  return (
    <li className="rounded-md border border-border bg-surface-elevated p-3">
      <p
        className="font-mono text-2xl text-accent leading-none"
        data-testid="evidence-stat-value"
      >
        {value !== null ? value : "—"}
      </p>
      <p className="mt-2 text-xs text-text-primary">{label}</p>
      <p className="mt-0.5 text-[10px] uppercase tracking-wider text-text-muted">
        {hint}
      </p>
    </li>
  );
}
