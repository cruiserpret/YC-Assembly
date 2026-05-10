"""Phase 8.5B.1 — dynamic evidence anchor planner.

Replaces 8.5B's hardcoded energy-drink anchor list with a planner
that derives evidence anchors from a founder-style product brief.
The planner is deterministic — no LLM call, no network — and works
for any product without product-category-specific code.

The planner output (`EvidenceAnchorPlan`) feeds into the dynamic
Amazon scorer (`score_review_with_plan`), which preserves Phase 8.5B's
4-bucket confidence enum but pulls all term lists from the plan
rather than module-level constants.

Triton remains supported as a regression case — its plan is generated
from its founder brief, not from any hardcoded category list.
"""

from assembly.sources.evidence_anchor_planner.category_planner import (
    SourceCategoryPlan,
    generate_source_category_plan,
)
from assembly.sources.evidence_anchor_planner.constants import (
    UNIVERSAL_AMBIGUITY_CONTEXTS,
    UNIVERSAL_GENERIC_MODIFIERS,
    UNIVERSAL_STOPWORDS,
)
from assembly.sources.evidence_anchor_planner.planner import (
    generate_anchor_plan,
)
from assembly.sources.evidence_anchor_planner.schemas import (
    AmbiguousEntity,
    EvidenceAnchorPlan,
    MetadataRelevanceRule,
    ProductBriefForPlanning,
)
from assembly.sources.evidence_anchor_planner.scorer import (
    score_review_with_plan,
)

__all__ = [
    "AmbiguousEntity",
    "EvidenceAnchorPlan",
    "MetadataRelevanceRule",
    "ProductBriefForPlanning",
    "SourceCategoryPlan",
    "UNIVERSAL_AMBIGUITY_CONTEXTS",
    "UNIVERSAL_GENERIC_MODIFIERS",
    "UNIVERSAL_STOPWORDS",
    "generate_anchor_plan",
    "generate_source_category_plan",
    "score_review_with_plan",
]
