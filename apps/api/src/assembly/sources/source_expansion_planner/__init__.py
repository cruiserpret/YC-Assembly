"""Phase 8.5D.1D — dynamic source-expansion planner.

Replaces ad-hoc per-product query lists with a planner that derives a
bounded provider query plan from:

  * the founder brief (`ProductBriefForPlanning`),
  * the deterministic `EvidenceAnchorPlan`,
  * the prior-run `PersonaDiversityEvaluation` (8.5D.1C audit signal),
  * the set of available providers (`brave_search`, `youtube_data_api`).

Deterministic. NO LLM, NO network. Same inputs → same plan. Universal:
no per-product code path. Hard query / per-query / per-result caps.
"""

from assembly.sources.source_expansion_planner.planner import (
    generate_source_expansion_plan,
)
from assembly.sources.source_expansion_planner.schemas import (
    ExpansionQuery,
    ProviderName,
    ProviderQueryPlan,
    SourceExpansionPlan,
)

__all__ = [
    "ExpansionQuery",
    "ProviderName",
    "ProviderQueryPlan",
    "SourceExpansionPlan",
    "generate_source_expansion_plan",
]
