"""Phase 8.2F.7 — persona relevance audit.

Deterministic audit over the personas Phase 8.2F constructed. Answers:

  Are these personas actually relevant, useful, and non-random for an
  Amboras-style commerce-founder simulation?

Public surface:
  - `RelevanceClassification`         closed enum
  - `PersonaRelevanceScore`           per-persona scorecard
  - `AggregateAuditResult`            aggregate run result
  - `score_persona`                   deterministic per-persona scorer
  - `audit_personas`                  aggregate audit
  - `format_audit_report`             human-readable report
  - `recommend_next_step`             A / B / C recommendation logic

The package contains NO LLM call sites. The drift test enforces it.
"""
from assembly.pipeline.persona_relevance.auditor import (
    AggregateAuditResult,
    EvidenceLinkView,
    PersonaAuditInput,
    PersonaRelevanceScore,
    TraitView,
    audit_personas,
    score_persona,
)
from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
    SCORE_FIELDS,
    SCORE_MAX_PER_FIELD,
    STAKEHOLDER_CATEGORIES,
    TOTAL_MAX,
    classify_total_score,
)
from assembly.pipeline.persona_relevance.summary import (
    NextStepRecommendation,
    format_audit_report,
    recommend_next_step,
)


__all__ = [
    "AggregateAuditResult",
    "CLASSIFICATION_THRESHOLDS",
    "EvidenceLinkView",
    "NextStepRecommendation",
    "PersonaAuditInput",
    "PersonaRelevanceScore",
    "RelevanceClassification",
    "SCORE_FIELDS",
    "SCORE_MAX_PER_FIELD",
    "STAKEHOLDER_CATEGORIES",
    "TOTAL_MAX",
    "TraitView",
    "audit_personas",
    "classify_total_score",
    "format_audit_report",
    "recommend_next_step",
    "score_persona",
]
