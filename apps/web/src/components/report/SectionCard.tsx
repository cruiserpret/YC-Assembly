"use client";

import { ReactNode, useState } from "react";

import type { EvidenceAnchorDetail } from "@/lib/schema";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { EvidenceAnchorList } from "./EvidenceAnchorList";
import { SimulationReferenceList } from "./SimulationReferenceList";

type Reference = { kind: string; target_id: string; note?: string | null };

export function SectionCard({
  id,
  title,
  summary,
  confidence,
  evidenceAnchors,
  simulationReferences,
  validatorNotes,
  details,
  children,
}: {
  id: string;
  title: string;
  summary: string;
  confidence?: string;
  evidenceAnchors?: string[];
  simulationReferences?: Reference[];
  validatorNotes?: string[];
  details?: Record<string, EvidenceAnchorDetail>;
  children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const isClipped = summary.length > 380;
  const visibleSummary = expanded || !isClipped ? summary : summary.slice(0, 380) + "…";

  return (
    <section id={id} className="space-y-3 scroll-mt-8">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-serif text-2xl tracking-tight">{title}</h2>
        {confidence && <ConfidenceBadge level={confidence} />}
      </div>

      <p className="prose-card whitespace-pre-line text-base">{visibleSummary}</p>
      {isClipped && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-ink-600 underline hover:text-ink-900"
        >
          {expanded ? "Show less" : "Read more"}
        </button>
      )}

      {children}

      <div className="flex flex-wrap gap-2 pt-2 text-xs">
        {evidenceAnchors && evidenceAnchors.length > 0 && (
          <EvidenceAnchorList anchors={evidenceAnchors} details={details ?? {}} />
        )}
        {simulationReferences && simulationReferences.length > 0 && (
          <SimulationReferenceList refs={simulationReferences} />
        )}
        {validatorNotes && validatorNotes.length > 0 && (
          <span className="rounded bg-warn-subtle px-2 py-1 text-warn">
            validator notes ({validatorNotes.length})
          </span>
        )}
      </div>
    </section>
  );
}
