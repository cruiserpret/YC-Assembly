import type { SimulationReport } from "../src/lib/schema";

const EID_1 = "11111111-1111-1111-1111-111111111111";
const EID_2 = "22222222-2222-2222-2222-222222222222";
const CLAIM_ID = "33333333-3333-3333-3333-333333333333";

export function buildSampleReport(overrides: Partial<SimulationReport> = {}): SimulationReport {
  return {
    simulation_id: "9bb2061b-c226-4c2e-a493-bdf894090ba1",
    status: "reported",
    schema_version: "v0.1",
    created_at: "2026-05-02T22:36:22.000Z",
    public_opinion_sentiment: {
      summary: "The simulated society moved through a noticeably negative arc.",
      evidence_anchors: [EID_1],
      simulation_references: [],
      confidence: "clear",
      validator_notes: [],
    },
    persuasion_analysis: {
      persuaded: {
        summary: "Agents who softened cited consolidation against plugin sprawl.",
        evidence_anchors: [EID_1],
        simulation_references: [],
        confidence: "moderate",
        validator_notes: [],
        factual_claims: [],
      },
      not_persuaded: {
        summary: "The strongest resistance came from agents portraying premium operators.",
        evidence_anchors: [EID_1],
        simulation_references: [],
        confidence: "clear",
        validator_notes: [],
        factual_claims: [],
      },
    },
    market_acceptance_requirement: {
      summary: "The society seemed to need verifiable proof of merchant control.",
      evidence_anchors: [EID_1],
      simulation_references: [],
      confidence: "clear",
      validator_notes: [],
      factual_claims: [],
    },
    product_trajectory: {
      summary: "Across seven rounds the population moved from curiosity to skepticism.",
      evidence_anchors: [EID_1],
      simulation_references: [],
      confidence: "clear",
      validator_notes: [],
    },
    competitor_analysis: {
      summary: "Agents reached for Shopify Magic as a baseline.",
      evidence_anchors: [EID_1],
      simulation_references: [],
      confidence: "moderate",
      validator_notes: [],
      competitors: [
        {
          competitor_name: "Shopify Magic",
          comparison_summary: "Agents framed it as the zero-cost native baseline.",
          evidence_anchors: [EID_1],
          factual_claims: [],
        },
      ],
    },
    recommendations: {
      target_audience: {
        summary: "Mid-volume merchants seemed most receptive in the simulation.",
        evidence_anchors: [EID_1],
        simulation_references: [],
        confidence: "clear",
        validator_notes: [],
      },
      positioning: {
        summary: "The product seemed to land as an autonomous-operator category.",
        evidence_anchors: [EID_1],
        simulation_references: [],
        confidence: "moderate",
        validator_notes: [],
        factual_claims: [],
      },
      price_structure: {
        summary: "The supplied starter price seemed to land favorably.",
        evidence_anchors: [EID_1],
        simulation_references: [],
        confidence: "moderate",
        validator_notes: [],
        factual_claims: [],
      },
    },
    debate_shift_markers: {
      summary: "5 shift cluster(s) recorded across 2 round(s).",
      markers: [
        {
          round_number: 3,
          from_stance: "curious_hesitant",
          to_stance: "skeptical",
          count: 1,
          triggered_by: "trust",
          debate_turn_id: null,
          speaker_agent_id: null,
          target_agent_id: null,
          example_argument: null,
        },
      ],
      rounds_with_shifts: [3],
    },
    confidence: {
      summary:
        "At the final round, the largest stance bucket was 'skeptical' with 5 of 6 agents.",
      split_confidence: {
        largest_bucket_stance: "skeptical",
        largest_bucket_count: 5,
        second_bucket_stance: "curious_hesitant",
        second_bucket_count: 1,
        separation_ratio: 0.833,
        entropy_round_1: 0.0,
        entropy_round_7: 0.65,
        interpretation: "narrow",
      },
      stance_distribution_by_round: [],
    },
    evidence_ledger: {
      counts: { direct_count: 8, analogical_count: 0, missing_count: 5 },
      missing: [
        {
          evidence_id: EID_2,
          node_class: "review",
          summary: "missing public review",
        },
      ],
      claim_traceability: [],
    },
    validator_passed: true,
    validator_notes: {},
    evidence_anchor_details: {
      [EID_1]: {
        evidence_id: EID_1,
        kind: "direct",
        node_class: "trust_barrier",
        source_type: "user_input",
        source_url: null,
        source_excerpt: "Founders worry about brand identity",
        content_preview: "Founders worry about brand identity damage from autonomous AI.",
        captured_at: null,
      },
      [EID_2]: {
        evidence_id: EID_2,
        kind: "missing",
        node_class: "review",
        source_type: "user_input",
        source_url: null,
        source_excerpt: null,
        content_preview: "missing public review",
        captured_at: null,
      },
    },
    ...overrides,
  };
}
