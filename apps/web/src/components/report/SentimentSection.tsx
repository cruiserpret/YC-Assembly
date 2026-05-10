import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";

export function SentimentSection({
  id,
  section,
  details,
}: {
  id: string;
  section: SimulationReport["public_opinion_sentiment"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <SectionCard
      id={id}
      title="1. Subjective public opinion sentiment"
      summary={section.summary}
      confidence={section.confidence}
      evidenceAnchors={section.evidence_anchors}
      simulationReferences={section.simulation_references}
      validatorNotes={section.validator_notes}
      details={details}
    />
  );
}
