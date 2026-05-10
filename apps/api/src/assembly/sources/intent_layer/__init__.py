"""Phase 9E — simulated intent layer + society-wide debate package.

Universal building blocks for the intent inference + cross-cohort
argument propagation. NO LLM calls (deterministic). NO new retrieval.
NO mutation of any 9A/9B/9D row.
"""
from assembly.sources.intent_layer.argument_extractor import (
    extract_society_arguments,
)
from assembly.sources.intent_layer.evaluator import (
    evaluate_intent_and_debate_quality,
)
from assembly.sources.intent_layer.inference import (
    infer_simulated_intent,
)
from assembly.sources.intent_layer.propagation import (
    propagate_arguments_across_cohorts,
)
from assembly.sources.intent_layer.report import (
    render_intent_and_debate_report_json,
    render_intent_and_debate_report_markdown,
)
from assembly.sources.intent_layer.rollup import (
    build_intent_rollup,
)
from assembly.sources.intent_layer.schemas import (
    ArgumentDraft,
    PropagationDraft,
    SimulatedIntentDraft,
)


__all__ = [
    "ArgumentDraft",
    "PropagationDraft",
    "SimulatedIntentDraft",
    "build_intent_rollup",
    "evaluate_intent_and_debate_quality",
    "extract_society_arguments",
    "infer_simulated_intent",
    "propagate_arguments_across_cohorts",
    "render_intent_and_debate_report_json",
    "render_intent_and_debate_report_markdown",
]
