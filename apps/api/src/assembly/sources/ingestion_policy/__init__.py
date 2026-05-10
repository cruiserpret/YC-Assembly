"""Phase 8.5C.1 — dynamic ingestion-policy planner.

Generates an `IngestionPolicy` per (brief, evidence_anchor_plan,
candidate_pool, source_family, product_launch_state, db_baseline,
max_insert_cap). Deterministic — no LLM, no network.

Two-layer architecture:

  1. Dynamic product-specific layer — selection objectives, evidence
     quality dimensions, persona-construction value dimensions,
     selection rules, rejection rules — all derived from the brief +
     anchor plan + candidate pool. NEVER hardcoded per product.

  2. Universal safety/compliance layer — PII / fake-buyer /
     dataset-compliance / duplicate scanners + closed
     `UNIVERSAL_GUARDRAILS` list. Hardcoded because these rules
     are universal across every product.
"""

from assembly.sources.ingestion_policy.constants import (
    REQUIRED_SCANNERS,
    UNIVERSAL_GUARDRAILS,
)
from assembly.sources.ingestion_policy.diversity import (
    apply_diversity_aware_reranking,
)
from assembly.sources.ingestion_policy.policy import (
    decide_candidates,
    generate_ingestion_policy,
)
from assembly.sources.ingestion_policy.scanners import (
    PIIScanResult,
    UnlaunchedFakeBuyerScanResult,
    check_duplicate_content_hash,
    compute_content_hash,
    scan_dataset_compliance,
    scan_pii,
    scan_unlaunched_fake_buyer,
)
from assembly.sources.ingestion_policy.schemas import (
    CandidateDecision,
    CandidateRow,
    IngestionPolicy,
    PlannedSourceRecordPreview,
    PoolSummary,
    ProductLaunchState,
    RejectionRule,
    SelectionRule,
)

__all__ = [
    "REQUIRED_SCANNERS",
    "UNIVERSAL_GUARDRAILS",
    "CandidateDecision",
    "CandidateRow",
    "IngestionPolicy",
    "PIIScanResult",
    "PlannedSourceRecordPreview",
    "PoolSummary",
    "ProductLaunchState",
    "RejectionRule",
    "SelectionRule",
    "UnlaunchedFakeBuyerScanResult",
    "apply_diversity_aware_reranking",
    "check_duplicate_content_hash",
    "compute_content_hash",
    "decide_candidates",
    "generate_ingestion_policy",
    "scan_dataset_compliance",
    "scan_pii",
    "scan_unlaunched_fake_buyer",
]
