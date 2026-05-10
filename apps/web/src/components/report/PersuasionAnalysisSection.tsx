import type { EvidenceAnchorDetail, SimulationReport } from "@/lib/schema";
import { SectionCard } from "./SectionCard";

export function PersuasionAnalysisSection({
  id,
  analysis,
  details,
}: {
  id: string;
  analysis: SimulationReport["persuasion_analysis"];
  details: Record<string, EvidenceAnchorDetail>;
}) {
  return (
    <section id={id} className="space-y-6 scroll-mt-8">
      <SectionCard
        id={`${id}-persuaded`}
        title="2. What persuaded people"
        summary={analysis.persuaded.summary}
        confidence={analysis.persuaded.confidence}
        evidenceAnchors={analysis.persuaded.evidence_anchors}
        simulationReferences={analysis.persuaded.simulation_references}
        validatorNotes={analysis.persuaded.validator_notes}
        details={details}
      />
      <SectionCard
        id={`${id}-not-persuaded`}
        title="3. What did not persuade people"
        summary={analysis.not_persuaded.summary}
        confidence={analysis.not_persuaded.confidence}
        evidenceAnchors={analysis.not_persuaded.evidence_anchors}
        simulationReferences={analysis.not_persuaded.simulation_references}
        validatorNotes={analysis.not_persuaded.validator_notes}
        details={details}
      />
    </section>
  );
}
