// Tiny formatting helpers. Deliberately NO percentage / forecast formatters —
// the report should never render numbers as forecasts.

const PIPELINE_STAGES = [
  "pending",
  "parsing",
  "evidence_building",
  "evidence_graph_building",
  "society_building",
  "simulating",
  "aggregating",
  "reported",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export const ALL_PIPELINE_STAGES: readonly PipelineStage[] = PIPELINE_STAGES;

const STAGE_LABEL: Record<PipelineStage, string> = {
  pending: "Pending",
  parsing: "Parsing the brief",
  evidence_building: "Gathering evidence",
  evidence_graph_building: "Building the evidence graph",
  society_building: "Building the synthetic society",
  simulating: "Running the simulation",
  aggregating: "Synthesising the report",
  reported: "Report ready",
};

export function stageLabel(stage: string): string {
  return STAGE_LABEL[stage as PipelineStage] ?? stage;
}

export function isTerminal(status: string): boolean {
  return status === "reported" || status === "failed";
}

export function stageIndex(stage: string): number {
  const i = PIPELINE_STAGES.indexOf(stage as PipelineStage);
  return i < 0 ? -1 : i;
}

export function clip(text: string, max = 240): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}
