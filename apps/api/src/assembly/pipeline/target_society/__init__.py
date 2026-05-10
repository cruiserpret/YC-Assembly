"""Phase 8.2G — run-scoped target society planner.

Public surface:
  - `ProductBriefInput`            input model
  - `TargetSocietyPlan`            full plan output
  - `build_target_society_plan`    deterministic planner entry point
  - `validate_target_society_plan` structural validator
  - `render_target_society_plan_summary` operator-text formatter
  - `render_operator_summary`      one-paragraph summary
  - `explain_next_steps`           operator checklist
  - `ProductFamily`, `SimulationGoal`, `WarningSeverity`  closed enums
  - `ALL_EXAMPLES`                 4 fixture briefs (Amboras, water,
                                   iPhone 17, halal financing)
"""
from assembly.pipeline.target_society.constants import (
    ProductFamily,
    SimulationGoal,
    WarningSeverity,
)
from assembly.pipeline.target_society.examples import (
    ALL_EXAMPLES,
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    WATER_BOTTLE_BRIEF,
)
from assembly.pipeline.target_society.planner import (
    build_target_society_plan,
    detect_product_family,
    detect_sensitive_markers,
)
from assembly.pipeline.target_society.query_plan import (
    generate_competitor_queries,
    generate_geography_queries,
    generate_pricing_queries,
    generate_public_opinion_queries,
    generate_search_queries_for_category,
)
from assembly.pipeline.target_society.schemas import (
    CoverageRequirements,
    ExpectedOutputs,
    InterpretedBrief,
    PersonaRetrievalPlan,
    ProductBriefInput,
    SimulationReadinessGates,
    SocietyPlanWarning,
    SourceQueryPlan,
    StakeholderCategory,
    TargetSocietyPlan,
)
from assembly.pipeline.target_society.summary import (
    explain_next_steps,
    render_operator_summary,
    render_target_society_plan_summary,
)
from assembly.pipeline.target_society.validator import (
    ValidationResult,
    ValidationViolation,
    validate_target_society_plan,
)


__all__ = [
    "ALL_EXAMPLES",
    "AMBORAS_BRIEF",
    "CoverageRequirements",
    "ExpectedOutputs",
    "HALAL_FINANCING_BRIEF",
    "IPHONE_17_BRIEF",
    "InterpretedBrief",
    "PersonaRetrievalPlan",
    "ProductBriefInput",
    "ProductFamily",
    "SimulationGoal",
    "SimulationReadinessGates",
    "SocietyPlanWarning",
    "SourceQueryPlan",
    "StakeholderCategory",
    "TargetSocietyPlan",
    "ValidationResult",
    "ValidationViolation",
    "WATER_BOTTLE_BRIEF",
    "WarningSeverity",
    "build_target_society_plan",
    "detect_product_family",
    "detect_sensitive_markers",
    "explain_next_steps",
    "generate_competitor_queries",
    "generate_geography_queries",
    "generate_pricing_queries",
    "generate_public_opinion_queries",
    "generate_search_queries_for_category",
    "render_operator_summary",
    "render_target_society_plan_summary",
    "validate_target_society_plan",
]
