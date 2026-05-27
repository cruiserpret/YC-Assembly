// Phase 10B — types for the /assembly/runs/* API surface.
// Mirrors the FastAPI schemas in apps/api/src/assembly/schemas/.

export const ALLOWED_INTENT_LABELS = [
  "would_buy_now",
  "would_try_once",
  "would_join_waitlist",
  "would_consider_if_proven",
  "would_share_with_friend",
  "would_compare_to_current_brand",
  "loyal_to_current_alternative",
  "would_reject",
  "would_block",
] as const;
export type IntentLabel = (typeof ALLOWED_INTENT_LABELS)[number];

export type RunMode = "live_founder_brief" | "fixture_demo";

export type RunStatus =
  | "running"
  | "complete"
  | "failed"
  | "skeletal";

export interface FounderBriefIn {
  product_name: string;
  product_description: string;
  price_or_price_structure: string;
  launch_geography: string;
  target_customers: string[];
  competitors_or_alternatives: string[];
  launch_state: "unlaunched" | "soft_launched" | "launched";
  product_url?: string;
  category_hint?: string;
  optional_context?: string;
  constraints?: string[];
  preferred_society_size?: number;
  max_budget_usd?: number;
  report_depth?: "fast_demo" | "standard" | "deep";
}

export interface CreateRunRequest {
  mode: RunMode;
  brief: FounderBriefIn;
}

export interface CreateRunResponse {
  run_id: string;
  status: RunStatus;
  mode: RunMode;
  current_stage: string;
  estimated_steps: number;
  artifact_manifest: Record<string, string>;
}

export interface StageInfo {
  status: "pending" | "running" | "complete" | "failed" | "skipped";
  started_at: string | null;
  completed_at: string | null;
  description?: string;
}

export interface RunStatusResponse {
  run_id: string;
  mode: RunMode;
  status: RunStatus;
  current_stage: string;
  completed_stages: string[];
  failed_stage: string | null;
  progress_pct: number;
  stage_progress: Record<string, StageInfo>;
  artifact_links: Record<string, string>;
  error_message: string | null;
  caveat: string;
}

export interface FounderReport {
  schema_version: string;
  run_id: string;
  mode?: string;
  persona_source?: string;
  evidence_source?: string;
  product_brief: Record<string, unknown>;
  executive_summary: string[];
  synthetic_society_size: number;
  cohort_count: number;
  synthetic_intent_snapshot: {
    intent_distribution: Record<string, number>;
    switching_status_distribution: Record<string, number>;
    high_intent_segments_count: number;
    rejection_segments_count: number;
  };
  most_receptive_cohorts: unknown[];
  most_resistant_cohorts: unknown[];
  loyal_to_alternative_patterns: unknown[];
  top_objections: { bucket: string; weighted_score: number }[];
  proof_needed: { bucket: string; weighted_score: number }[];
  persuasion_levers: unknown[];
  competitor_or_alternative_comparison: unknown[];
  society_wide_debate_summary: {
    argument_count: number;
    propagation_count: number;
    argument_type_distribution: Record<string, number>;
    response_type_distribution: Record<string, number>;
  };
  arguments_that_spread: unknown[];
  arguments_that_were_resisted: unknown[];
  public_private_shift_summary: {
    pre_stance_distribution: Record<string, number>;
    final_stance_distribution: Record<string, number>;
  };
  recommended_next_tests: string[];
  confidence_dimensions: Record<string, string>;
  caveats: string[];
  evidence_traceability_summary: Record<string, unknown>;
  header_caveat?: string;
  appendix?: Record<string, unknown>;
}

export interface PersonasPayload {
  run_id: string;
  phase?: string;
  mode?: string;
  persona_source?: string;
  persona_count?: number;
  run_scope_id?: string;
  compressed_count?: number;
  evidence_strategy?: string;
  quality_gates_summary?: Record<string, boolean>;
}

export interface CohortsPayload {
  run_id: string;
  phase?: string;
  cohort_count?: number;
  cohort_sizes?: number[];
  clustering_audit?: Record<string, unknown>;
  every_persona_assigned_exactly_once?: boolean;
}

export interface DiscussionPayload {
  run_id: string;
  phase?: string;
  discussion_session_id?: string;
  persona_count?: number;
  group_count?: number;
  public_turn_count?: number;
  peer_response_turn_count?: number;
  pre_ballot_count?: number;
  reflection_count?: number;
  final_ballot_count?: number;
  memory_atom_count?: number;
  ballot_count_by_stage?: Record<string, number>;
  cost_summary?: Record<string, unknown>;
}

export interface IntentPayload {
  run_id: string;
  phase?: string;
  intent_record_count?: number;
  intent_distribution?: Record<string, number>;
  switching_status_distribution?: Record<string, number>;
}

export interface TranscriptTurn {
  turn_id: string;
  turn_number: number;
  speaker_persona_id: string;
  speaker_name: string;
  speaker_role: string;
  turn_type: string;
  stance: string | null;
  public_text: string;
  referenced_turn_ids: string[];
}

export interface TranscriptRound {
  round_number: number;
  round_label: string;
  turns: TranscriptTurn[];
}

export interface TranscriptPersona {
  persona_id: string;
  display_name: string;
  role: string;
}

export interface TranscriptGroup {
  group_index: number;
  personas: TranscriptPersona[];
  rounds: TranscriptRound[];
}

export interface PrivateBallotView {
  stance: string;
  reasoning: string;
  confidence: string;
  top_objection: string | null;
  top_proof_need: string | null;
  public_private_delta?: string | null;
  is_repaired: boolean;
}

export interface DiscussionTranscriptPayload {
  run_id: string;
  discussion_session_id: string | null;
  groups: TranscriptGroup[];
  private_ballots: Record<
    string,
    { pre?: PrivateBallotView; reflection?: PrivateBallotView; final?: PrivateBallotView }
  >;
  note?: string;
}

// ----- Phase 14A — 100-voter influence-overlay payload -----
//
// Returned by GET /assembly/runs/{run_id}/lightweight_voters. The four
// market buckets are buyer / receptive / uncertain / skeptical
// (matching VoterBucketDistribution in the backend voter_schema).
//
// `voter_overlay_available: false` is the empty-state shape returned
// for runs that pre-date the Phase 12C overlay or where the artifacts
// are missing. The frontend hides the panel in this case but the rest
// of the report still renders.

export interface VoterBucketDistribution {
  buyer: number;
  receptive: number;
  uncertain: number;
  skeptical: number;
  total_population_weight?: number;
  n_voters?: number;
}

export interface VoterCalibratedDistribution {
  distribution_percent: {
    buyer: number;
    receptive: number;
    uncertain: number;
    skeptical: number;
  };
  confidence_band_pp: number;
  used_prior_correction?: boolean;
  blend_weights?: { rich_24?: number; voter_100?: number };
  calibration_warnings?: string[];
}

export interface VoterInfluenceRound {
  round_idx: number;
  round_type?: string;
  voters_affected?: number;
  intent_changes?: number;
  bucket_changes?: number;
  bucket_distribution?: Partial<VoterBucketDistribution>;
  skeptic_transitions?: Record<string, number>;
  notes?: string | null;
}

export interface VoterClusterArguments {
  pro?: string[];
  con?: string[];
  proof?: string[];
  objection?: string[];
  [key: string]: unknown;
}

export interface VoterDiversityHealth {
  n_voters?: number;
  n_cohorts_represented?: number;
  n_segments_represented?: number;
  n_roles_represented?: number;
  max_role_concentration?: number;
  competitor_user_share?: number;
  n_edges?: number;
  avg_edges_per_voter?: number;
  intent_diversity_per_round?: Record<string, number>;
  intent_changes_count?: number;
  bucket_changes_count?: number;
  warnings?: string[];
}

export interface LightweightVoterSample {
  cohort_label?: string;
  intent_label?: string;
  bucket?: string;
  top_objection?: string;
  top_proof_need?: string;
  private_reasoning_excerpt?: string;
  [key: string]: unknown;
}

export interface LightweightVotersPayload {
  run_id: string;
  voter_overlay_available: boolean;
  voters_count?: number;
  final_distribution?: VoterBucketDistribution | null;
  calibrated_distribution?: VoterCalibratedDistribution | null;
  raw_24_distribution_percent?: Record<string, number> | null;
  influence_rounds?: VoterInfluenceRound[];
  cluster_arguments?: VoterClusterArguments | null;
  diversity_health?: VoterDiversityHealth | null;
  samples?: LightweightVoterSample[];
  reason?: string;
  source_notes?: Record<string, unknown>;
}
