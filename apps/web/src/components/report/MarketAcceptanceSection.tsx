import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";

export function MarketAcceptanceSection({
  id,
  section,
  details,
}: {
  id: string;
  section: SimulationReport["market_acceptance_requirement"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <SectionCard
      id={id}
      title="4. The one thing needed for market acceptance"
      summary={section.summary}
      confidence={section.confidence}
      evidenceAnchors={section.evidence_anchors}
      simulationReferences={section.simulation_references}
      validatorNotes={section.validator_notes}
      details={details}
    />
  );
}
