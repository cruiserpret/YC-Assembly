"""Phase 10A.1 — live_founder_brief orchestration package.

Walks the 13-stage pipeline (validate → plan → retrieve → score →
build_personas → enrich_psychology → individual_sim → group_discussion
→ repair → cohorts → intent → propagation → report) for arbitrary
founder briefs.
"""
from assembly.orchestration.live_founder_brief import (
    PIPELINE_STAGES,
    LiveFounderBriefOrchestrator,
    estimate_pipeline_cost,
    run_live_founder_brief_pipeline,
)


__all__ = [
    "PIPELINE_STAGES",
    "LiveFounderBriefOrchestrator",
    "estimate_pipeline_cost",
    "run_live_founder_brief_pipeline",
]
