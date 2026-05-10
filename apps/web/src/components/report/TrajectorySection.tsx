import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";

export function TrajectorySection({
  id,
  section,
  details,
}: {
  id: string;
  section: SimulationReport["product_trajectory"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <SectionCard
      id={id}
      title="5. Product trajectory"
      summary={section.summary}
      confidence={section.confidence}
      evidenceAnchors={section.evidence_anchors}
      simulationReferences={section.simulation_references}
      validatorNotes={section.validator_notes}
      details={details}
    />
  );
}
