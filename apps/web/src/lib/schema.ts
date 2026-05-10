import { z } from "zod";

// ---------------------------------------------------------------------------
// Brief (request side)
// ---------------------------------------------------------------------------
// Mirrors apps/api/src/assembly/schemas/brief.py::SimulationBriefIn.
// Mins/regexes match the backend Pydantic constraints so users see fixable
// errors before submit.

export const competitorRefSchema = z.object({
  name: z.string().min(2).max(120),
  url: z.string().url().optional().or(z.literal("")),
  notes: z.string().optional().or(z.literal("")),
});

export const priceStructureSchema = z.object({
  model: z.string().min(2),
  amount: z.string().optional().or(z.literal("")),
  notes: z.string().optional().or(z.literal("")),
});

export const targetSocietySchema = z.object({
  description: z.string().min(16).max(2000),
  geography: z.string().optional().or(z.literal("")),
  income_level: z.string().optional().or(z.literal("")),
  known_segments: z.array(z.string().min(1)).default([]),
});

export const briefSchema = z.object({
  product_type: z.string().min(2),
  product_name: z.string().min(2).max(80),
  description: z
    .string()
    .min(64, "Describe the product in more than a one-line idea (≥64 chars)."),
  price_structure: priceStructureSchema,
  target_society: targetSocietySchema,
  competitors: z.array(competitorRefSchema).min(1, "List at least one competitor or current alternative."),
  product_url: z.string().url().optional().or(z.literal("")),
  additional_context: z.string().optional().or(z.literal("")),
  evidence_cutoff_date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, "Use YYYY-MM-DD")
    .optional()
    .or(z.literal("")),
});

export type Brief = z.infer<typeof briefSchema>;

export const simulationCreatedSchema = z.object({
  id: z.string().uuid(),
  status: z.string(),
  created_at: z.string(),
});

export type SimulationCreated = z.infer<typeof simulationCreatedSchema>;

// ---------------------------------------------------------------------------
// Status (polling response)
// ---------------------------------------------------------------------------

export const simulationStatusSchema = z.object({
  id: z.string().uuid(),
  status: z.string(),
  progress: z
    .object({
      stage: z.string().optional(),
      current_round: z.string().optional().nullable(),
      round_index: z.union([z.number(), z.string()]).optional().nullable(),
      agents_completed: z.union([z.number(), z.string()]).optional().nullable(),
      agents_total: z.union([z.number(), z.string()]).optional().nullable(),
    })
    .partial()
    .optional()
    .nullable(),
  failed_stage: z.string().nullable().optional(),
  error: z
    .object({
      kind: z.string().optional(),
      message: z.string().optional(),
    })
    .partial()
    .nullable()
    .optional(),
  total_cost_usd: z.union([z.number(), z.string()]).optional().nullable(),
  completed_at: z.string().optional().nullable(),
});

export type SimulationStatus = z.infer<typeof simulationStatusSchema>;

// ---------------------------------------------------------------------------
// Report (response side)
// ---------------------------------------------------------------------------
// Mirrors apps/api/src/assembly/api/reports.py::SimulationReport.

export const evidenceAnchorDetailSchema = z.object({
  evidence_id: z.string().uuid(),
  kind: z.string(),
  node_class: z.string(),
  source_type: z.string(),
  source_url: z.string().nullable(),
  source_excerpt: z.string().nullable(),
  content_preview: z.string().nullable(),
  captured_at: z.string().nullable(),
  node_class_confidence: z.number().nullable().optional(),
});

export type EvidenceAnchorDetail = z.infer<typeof evidenceAnchorDetailSchema>;

const sectionBaseShape = {
  summary: z.string(),
  evidence_anchors: z.array(z.string().uuid()).default([]),
  simulation_references: z
    .array(
      z.object({
        kind: z.string(),
        target_id: z.string().uuid(),
        note: z.string().nullable().optional(),
      })
    )
    .default([]),
  confidence: z.string().default("moderate"),
  validator_notes: z.array(z.string()).default([]),
};

const factualClaimSchema = z.object({
  text: z.string(),
  source_evidence_id: z.string().uuid(),
  source_excerpt: z.string(),
  claim_type: z.string(),
  basis: z.string(),
  confidence: z.number(),
});

const sentimentSchema = z.object(sectionBaseShape).passthrough();
const persuasionSubSchema = z
  .object({
    ...sectionBaseShape,
    factual_claims: z.array(factualClaimSchema).default([]),
  })
  .passthrough();

const persuasionAnalysisSchema = z.object({
  persuaded: persuasionSubSchema,
  not_persuaded: persuasionSubSchema,
});

const competitorMentionSchema = z.object({
  competitor_name: z.string(),
  comparison_summary: z.string(),
  evidence_anchors: z.array(z.string().uuid()).default([]),
  factual_claims: z.array(factualClaimSchema).default([]),
});

const competitorAnalysisSchema = z
  .object({
    ...sectionBaseShape,
    competitors: z.array(competitorMentionSchema).default([]),
  })
  .passthrough();

const recommendationsSchema = z.object({
  target_audience: z.object(sectionBaseShape).passthrough(),
  positioning: z
    .object({
      ...sectionBaseShape,
      factual_claims: z.array(factualClaimSchema).default([]),
    })
    .passthrough(),
  price_structure: z
    .object({
      ...sectionBaseShape,
      factual_claims: z.array(factualClaimSchema).default([]),
    })
    .passthrough(),
});

const debateShiftMarkerSchema = z.object({
  round_number: z.number(),
  from_stance: z.string(),
  to_stance: z.string(),
  count: z.number(),
  triggered_by: z.string().nullable().optional(),
  debate_turn_id: z.string().uuid().nullable().optional(),
  speaker_agent_id: z.string().uuid().nullable().optional(),
  target_agent_id: z.string().uuid().nullable().optional(),
  example_argument: z.string().nullable().optional(),
});

const debateShiftMarkersSchema = z
  .object({
    summary: z.string(),
    markers: z.array(debateShiftMarkerSchema).default([]),
    rounds_with_shifts: z.array(z.number()).default([]),
  })
  .passthrough();

const splitConfidenceSchema = z.object({
  largest_bucket_stance: z.string(),
  largest_bucket_count: z.number(),
  second_bucket_stance: z.string().nullable().optional(),
  second_bucket_count: z.number(),
  separation_ratio: z.number(),
  entropy_round_1: z.number(),
  entropy_round_7: z.number(),
  interpretation: z.string(),
});

const stanceCountSchema = z.object({
  stance: z.string(),
  count: z.number(),
});

const confidenceSectionSchema = z
  .object({
    summary: z.string(),
    split_confidence: splitConfidenceSchema,
    stance_distribution_by_round: z.array(z.array(stanceCountSchema)).default([]),
  })
  .passthrough();

const evidenceLedgerSchema = z
  .object({
    counts: z.object({
      direct_count: z.number(),
      analogical_count: z.number(),
      missing_count: z.number(),
    }),
    missing: z
      .array(
        z.object({
          evidence_id: z.string().uuid(),
          node_class: z.string(),
          summary: z.string(),
        })
      )
      .default([]),
    claim_traceability: z
      .array(
        z.object({
          claim_id: z.string().uuid(),
          claim_text: z.string(),
          source_evidence_id: z.string().uuid(),
          source_url: z.string().nullable().optional(),
          source_excerpt: z.string(),
          claim_type: z.string(),
          basis: z.string(),
        })
      )
      .default([]),
  })
  .passthrough();

export const simulationReportSchema = z.object({
  simulation_id: z.string().uuid(),
  status: z.string(),
  schema_version: z.string(),
  created_at: z.string(),

  public_opinion_sentiment: sentimentSchema,
  persuasion_analysis: persuasionAnalysisSchema,
  market_acceptance_requirement: persuasionSubSchema,
  product_trajectory: sentimentSchema,
  competitor_analysis: competitorAnalysisSchema,
  recommendations: recommendationsSchema,
  debate_shift_markers: debateShiftMarkersSchema,
  confidence: confidenceSectionSchema,
  evidence_ledger: evidenceLedgerSchema,

  validator_passed: z.boolean(),
  validator_notes: z.record(z.unknown()).default({}),

  evidence_anchor_details: z.record(evidenceAnchorDetailSchema).default({}),
});

export type SimulationReport = z.infer<typeof simulationReportSchema>;

// Discriminated union the API client returns from /report.
export type ReportResult =
  | { kind: "ready"; report: SimulationReport }
  | {
      kind: "report_not_ready";
      current_status: string;
      guidance: string;
    };
