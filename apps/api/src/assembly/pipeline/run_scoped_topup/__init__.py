"""Phase 8.2I — run-scoped top-up loop.

Orchestrator that ties together:
  * Phase 8.2G  target-society planner
  * Phase 8.2H  audience retrieval (before)
  * Phase 8.2I  Tavily-backed top-up ingestion + persona write
  * Phase 8.2H  audience retrieval (after)
  * Phase 8.2I  before/after re-audit

Two paths:
  * `execute_topup_loop_dry_run`  — pure planning; no live calls
  * `execute_topup_loop_live`     — operator-only full loop

Public surface:
"""
from assembly.pipeline.run_scoped_topup.executor import (
    RUN_PURPOSE,
    TopUpComplianceCaveatUnresolved,
    TopUpReadinessAlreadySufficient,
    execute_topup_loop_dry_run,
    execute_topup_loop_live,
)
from assembly.pipeline.run_scoped_topup.ingestion_plan import (
    build_topup_plan_from_audience_retrieval,
    flatten_plan_to_query_to_category_map,
)
from assembly.pipeline.run_scoped_topup.query_refinement import (
    AMBORAS_REFINED_QUERIES_V1,
    REFINEMENT_VERSION,
    build_amboras_refined_topup_plan,
)
from assembly.pipeline.run_scoped_topup.persona_write import (
    execute_persona_write_for_topup,
)
from assembly.pipeline.run_scoped_topup.reaudit import compare_before_after
from assembly.pipeline.run_scoped_topup.schemas import (
    CategoryBeforeAfter,
    RunScopedReauditResult,
    RunScopedTopUpLoopResult,
    RunScopedTopUpPlan,
    TopUpExecutionResult,
    TopUpPersonaWriteResult,
)
from assembly.pipeline.run_scoped_topup.summary import (
    render_run_scoped_topup_summary,
    render_topup_plan_summary,
)


__all__ = [
    "AMBORAS_REFINED_QUERIES_V1",
    "CategoryBeforeAfter",
    "REFINEMENT_VERSION",
    "RUN_PURPOSE",
    "RunScopedReauditResult",
    "RunScopedTopUpLoopResult",
    "RunScopedTopUpPlan",
    "TopUpComplianceCaveatUnresolved",
    "TopUpExecutionResult",
    "TopUpPersonaWriteResult",
    "TopUpReadinessAlreadySufficient",
    "build_amboras_refined_topup_plan",
    "build_topup_plan_from_audience_retrieval",
    "compare_before_after",
    "execute_persona_write_for_topup",
    "execute_topup_loop_dry_run",
    "execute_topup_loop_live",
    "flatten_plan_to_query_to_category_map",
    "render_run_scoped_topup_summary",
    "render_topup_plan_summary",
]
