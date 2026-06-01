"""Phase 15J — Validation Case Factory (candidate capture → review → promotion).

The operational system for collecting, reviewing, classifying, and ingesting
REAL external market-outcome cases into the validation ledger — so Assembly can
reach 20+ diverse reviewed cases, a clean holdout, and Tier-1/Tier-2 action
outcomes. It improves the TRUSTWORTHINESS of validation data; it changes no
forecast, applies no calibration, and invents no data.

A *candidate* is NOT a validation case: it is isolated (a distinct schema with
``extra="forbid"`` + a purpose marker), stored outside the manifest, and may
only become a ledger case after passing the human review + hard gates here.

See docs/PHASE_15J_VALIDATION_CASE_FACTORY.md.
"""
from __future__ import annotations

from assembly.validation_factory.candidate_factory import (
    CASE_FACTORY_VERSION,
    build_case_payload_from_candidate,
    candidate_fingerprint,
    evaluate_promotion_gates,
    factory_dashboard,
    find_duplicates,
)
from assembly.validation_factory.candidate_schema import (
    CANDIDATE_PURPOSE,
    CandidateCase,
    CandidateStatus,
    PromotionTarget,
    ReviewerChecklist,
)
from assembly.validation_factory.candidate_store import (
    DEFAULT_CANDIDATES_DIR,
    load_all_candidates,
    load_candidate,
    save_candidate,
)
from assembly.validation_factory.evidence_grading import (
    recommended_evidence_tier,
    tier_composition,
    validate_evidence_tier,
)

__all__ = [
    "CANDIDATE_PURPOSE",
    "CASE_FACTORY_VERSION",
    "CandidateCase",
    "CandidateStatus",
    "PromotionTarget",
    "ReviewerChecklist",
    "DEFAULT_CANDIDATES_DIR",
    "load_all_candidates",
    "load_candidate",
    "save_candidate",
    "build_case_payload_from_candidate",
    "candidate_fingerprint",
    "evaluate_promotion_gates",
    "factory_dashboard",
    "find_duplicates",
    "recommended_evidence_tier",
    "tier_composition",
    "validate_evidence_tier",
]
