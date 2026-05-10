"""Phase 8.5D.1C — persona-diversity evaluator.

Detects role / source / trait collapse in a generated persona-
candidate set and produces a `PersonaDiversityEvaluation` audit
artifact. Universal — works for any product brief. Deterministic.
"""

from assembly.sources.persona_diversity_evaluator.evaluator import (
    evaluate_persona_diversity,
)
from assembly.sources.persona_diversity_evaluator.schemas import (
    DiversityRecommendation,
    PersonaDiversityEvaluation,
)

__all__ = [
    "DiversityRecommendation",
    "PersonaDiversityEvaluation",
    "evaluate_persona_diversity",
]
