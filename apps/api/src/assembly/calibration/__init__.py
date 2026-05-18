"""Phase 12A.1 — Market calibration harness.

This package compares Assembly's predicted market distribution
(`buyer / receptive / uncertain / skeptical`) against a real-world
observed distribution gathered POST-prediction. The goal is to
measure whether Assembly is well-calibrated, not whether any single
persona was "right."

This is the scoring infrastructure that will eventually run against
a held-out blind-case library. Phase 12A.1 is scaffold-only: it
defines the bucket vocabulary, distribution metrics, blind-case
schema, and a report extractor for existing Assembly artifacts. It
does NOT yet contain any real-world outcome data, does NOT run any
LLM calls, and does NOT scrape anything.
"""
from __future__ import annotations

from assembly.calibration.blind_case_schema import (
    BlindCase,
    HiddenRealWorldOutcome,
    PreLaunchInput,
    ScoringMetadata,
)
from assembly.calibration.candidate_metadata_intake import (
    IntakeRecord,
    IntakeValidationResult,
    ScoredOperatorCandidate,
    convert_metadata_to_case_candidate,
    parse_operator_candidate_metadata,
    score_operator_candidates,
    summarize_operator_candidate_batch,
    validate_operator_candidate_metadata,
)
from assembly.calibration.candidate_shortlist_examples import (
    preliminary_unverified_shortlist,
)
from assembly.calibration.operator_candidate_template import (
    TemplateShapeValidation,
    build_empty_operator_candidate_template,
    candidate_metadata_help_text,
    candidate_metadata_optional_fields,
    candidate_metadata_required_fields,
    render_operator_candidate_request,
    validate_candidate_template_shape,
)
from assembly.calibration.case_candidate_selection import (
    CandidateRecommendation,
    CandidateRiskFlag,
    CaseCandidate,
    candidate_risk_flags,
    candidate_scorecard,
    evaluate_candidate_suitability,
    rank_case_candidates,
)
from assembly.calibration.case_pack_loader import (
    BlindCaseLoadError,
    CasePack,
    load_blind_case_from_dict,
    load_blind_case_from_json_path,
    load_case_pack_from_directory,
    summarize_case_pack,
    validate_case_pack_blindness,
)
from assembly.calibration.case_scoring import (
    CaseScoringResult,
    score_blind_case_against_prediction,
    score_case_pack,
    summarize_case_pack_scores,
)
from assembly.calibration.distribution_metrics import (
    bucket_absolute_errors,
    calibration_summary,
    max_bucket_error,
    mean_absolute_bucket_error,
    total_variation_distance,
)
from assembly.calibration.market_buckets import (
    ASSEMBLY_LABEL_TO_BUCKET,
    BUCKET_NAMES,
    MarketBucket,
    map_assembly_intent_to_market_bucket,
    normalize_distribution,
    validate_bucket_distribution,
)
from assembly.calibration.report_extractor import (
    BucketCounts,
    extract_bucket_counts_from_founder_report,
    extract_bucket_counts_from_intent_distribution,
)


__all__ = [
    # market_buckets
    "ASSEMBLY_LABEL_TO_BUCKET",
    "BUCKET_NAMES",
    "MarketBucket",
    "map_assembly_intent_to_market_bucket",
    "normalize_distribution",
    "validate_bucket_distribution",
    # distribution_metrics
    "bucket_absolute_errors",
    "calibration_summary",
    "max_bucket_error",
    "mean_absolute_bucket_error",
    "total_variation_distance",
    # blind_case_schema
    "BlindCase",
    "HiddenRealWorldOutcome",
    "PreLaunchInput",
    "ScoringMetadata",
    # report_extractor
    "BucketCounts",
    "extract_bucket_counts_from_founder_report",
    "extract_bucket_counts_from_intent_distribution",
    # case_pack_loader (Phase 12A.2)
    "BlindCaseLoadError",
    "CasePack",
    "load_blind_case_from_dict",
    "load_blind_case_from_json_path",
    "load_case_pack_from_directory",
    "summarize_case_pack",
    "validate_case_pack_blindness",
    # case_scoring (Phase 12A.2)
    "CaseScoringResult",
    "score_blind_case_against_prediction",
    "score_case_pack",
    "summarize_case_pack_scores",
    # case_candidate_selection (Phase 12A.3)
    "CandidateRecommendation",
    "CandidateRiskFlag",
    "CaseCandidate",
    "candidate_risk_flags",
    "candidate_scorecard",
    "evaluate_candidate_suitability",
    "rank_case_candidates",
    # candidate_shortlist_examples (Phase 12A.3)
    "preliminary_unverified_shortlist",
    # candidate_metadata_intake (Phase 12A.4)
    "IntakeRecord",
    "IntakeValidationResult",
    "ScoredOperatorCandidate",
    "convert_metadata_to_case_candidate",
    "parse_operator_candidate_metadata",
    "score_operator_candidates",
    "summarize_operator_candidate_batch",
    "validate_operator_candidate_metadata",
    # operator_candidate_template (Phase 12A.5)
    "TemplateShapeValidation",
    "build_empty_operator_candidate_template",
    "candidate_metadata_help_text",
    "candidate_metadata_optional_fields",
    "candidate_metadata_required_fields",
    "render_operator_candidate_request",
    "validate_candidate_template_shape",
]
