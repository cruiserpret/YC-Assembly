import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";

export function RecommendationsSection({
  id,
  recommendations,
  details,
}: {
  id: string;
  recommendations: SimulationReport["recommendations"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <section id={id} className="space-y-6 scroll-mt-8">
      <h2 className="font-serif text-2xl tracking-tight">7. Recommendations</h2>
      <p className="text-xs text-ink-400">
        These are observations about how the simulated society reacted — not directives.
      </p>

      <SectionCard
        id={`${id}-target`}
        title="Target audience"
        summary={recommendations.target_audience.summary}
        confidence={recommendations.target_audience.confidence}
        evidenceAnchors={recommendations.target_audience.evidence_anchors}
        simulationReferences={recommendations.target_audience.simulation_references}
        validatorNotes={recommendations.target_audience.validator_notes}
        details={details}
      />
      <SectionCard
        id={`${id}-positioning`}
        title="Positioning"
        summary={recommendations.positioning.summary}
        confidence={recommendations.positioning.confidence}
        evidenceAnchors={recommendations.positioning.evidence_anchors}
        simulationReferences={recommendations.positioning.simulation_references}
        validatorNotes={recommendations.positioning.validator_notes}
        details={details}
      />
      <SectionCard
        id={`${id}-price`}
        title="Price structure"
        summary={recommendations.price_structure.summary}
        confidence={recommendations.price_structure.confidence}
        evidenceAnchors={recommendations.price_structure.evidence_anchors}
        simulationReferences={recommendations.price_structure.simulation_references}
        validatorNotes={recommendations.price_structure.validator_notes}
        details={details}
      />
    </section>
  );
}
