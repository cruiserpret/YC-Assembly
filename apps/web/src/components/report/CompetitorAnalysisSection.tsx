import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";
import { EvidenceAnchorList } from "./EvidenceAnchorList";

export function CompetitorAnalysisSection({
  id,
  section,
  details,
}: {
  id: string;
  section: SimulationReport["competitor_analysis"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <SectionCard
      id={id}
      title="6. Competitor analysis"
      summary={section.summary}
      confidence={section.confidence}
      evidenceAnchors={section.evidence_anchors}
      simulationReferences={section.simulation_references}
      validatorNotes={section.validator_notes}
      details={details}
    >
      {section.competitors.length > 0 && (
        <ul className="mt-4 space-y-4">
          {section.competitors.map((c) => (
            <li
              key={c.competitor_name}
              className="rounded border border-ink-200 bg-ink-50 p-4"
            >
              <h3 className="font-serif text-lg">{c.competitor_name}</h3>
              <p className="prose-card mt-2 text-sm">{c.comparison_summary}</p>
              {c.evidence_anchors.length > 0 && (
                <div className="mt-3 text-xs">
                  <EvidenceAnchorList
                    anchors={c.evidence_anchors}
                    details={details}
                  />
                </div>
              )}
              {c.factual_claims.length > 0 && (
                <div className="mt-3 space-y-1 text-xs">
                  <p className="text-ink-400">Factual claims (verbatim, evidence-bound):</p>
                  <ul className="list-disc space-y-1 pl-5 text-ink-800">
                    {c.factual_claims.map((cl, i) => (
                      <li key={i}>
                        <span>{cl.text}</span>
                        <span className="ml-2 font-mono text-[11px] text-ink-400">
                          [{cl.basis} · {cl.claim_type}]
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
