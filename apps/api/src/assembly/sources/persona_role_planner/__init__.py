"""Phase 8.5D.1 — dynamic, brief-scoped persona-candidate planner.

Generates `PersonaCandidate` rows (audit-only Pydantic objects) from
a (founder brief, lineage-aware effective source pool) input.
Deterministic — no LLM, no network. Mirrors the architectural
pattern of the Phase 8.4A.2 dynamic_market_entry_planner, the Phase
8.5B.1 evidence_anchor_planner, and the Phase 8.5C.1
ingestion_policy.

Discipline:

  * Persona candidates are BRIEF-SCOPED + RUN-SCOPED. They are not
    global personas, not reusable templates, never cached across
    products.
  * Every candidate cites at least one `source_record_id` and
    surfaces real evidence excerpts.
  * Every candidate has at least 2 evidence-supported traits unless
    explicitly justified.
  * Universal launch-state validator rejects any candidate claiming
    direct usage of an unlaunched target product.
  * Persona role labels are INFERRED from evidence + brief, not
    drawn from a hardcoded list of role names.
"""

from assembly.sources.persona_role_planner.planner import (
    LineageAwareSourceSelector,
    PersonaCandidatePlanner,
    select_effective_sources,
)
from assembly.sources.persona_role_planner.role_inference import (
    UNIVERSAL_ROLE_LEXICONS,
    infer_persona_roles_from_evidence,
)
from assembly.sources.persona_role_planner.schemas import (
    EffectiveSourceRecord,
    InferredPersonaTrait,
    LaunchStateClaimValidationResult,
    PersonaCandidate,
    PersonaCandidateConfidence,
    PersonaCandidateRejection,
    PersonaRolePlan,
    ProductLaunchState,
    RejectionReason,
)
from assembly.sources.persona_role_planner.validators import (
    UNLAUNCHED_DIRECT_USAGE_PATTERNS,
    validate_launch_state_claims,
)

__all__ = [
    "EffectiveSourceRecord",
    "InferredPersonaTrait",
    "LaunchStateClaimValidationResult",
    "LineageAwareSourceSelector",
    "PersonaCandidate",
    "PersonaCandidateConfidence",
    "PersonaCandidatePlanner",
    "PersonaCandidateRejection",
    "PersonaRolePlan",
    "ProductLaunchState",
    "RejectionReason",
    "UNIVERSAL_ROLE_LEXICONS",
    "UNLAUNCHED_DIRECT_USAGE_PATTERNS",
    "infer_persona_roles_from_evidence",
    "select_effective_sources",
    "validate_launch_state_claims",
]
