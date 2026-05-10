"""Phase 8.5F — deterministic founder-facing report generator.

Universal: takes a Phase 8.5E simulation audit + quality audit, produces
a founder-readable JSON + Markdown report. NO LLM, NO retrieval, NO
DB writes.

Pieces:
  * `aggregator.aggregate_founder_report` — deterministic synthesis.
  * `markdown_renderer.render_markdown_report` — JSON → markdown.
  * `quality_evaluator.evaluate_report_quality` — 9-dimension scoring.
  * `secret_scanner.scan_for_secrets` — universal API-key-pattern
    detector. Used both as a guard before writing files and as a test
    helper to enforce the security rule from the 8.5F spec.
"""

from assembly.sources.founder_report_generator.aggregator import (
    aggregate_founder_report,
)
from assembly.sources.founder_report_generator.markdown_renderer import (
    render_markdown_report,
)
from assembly.sources.founder_report_generator.quality_evaluator import (
    ReportQualityEvaluation, ReportReadyState,
    evaluate_report_quality,
)
from assembly.sources.founder_report_generator.schemas import (
    FounderReport, ObjectionEntry, PersuasionLeverEntry,
    PositioningRecommendation, ProofNeededEntry,
    SeverityLabel, TestRecommendation,
)
from assembly.sources.founder_report_generator.secret_scanner import (
    SecretScanResult, scan_for_secrets,
)

__all__ = [
    "FounderReport",
    "ObjectionEntry",
    "PersuasionLeverEntry",
    "PositioningRecommendation",
    "ProofNeededEntry",
    "ReportQualityEvaluation",
    "ReportReadyState",
    "SecretScanResult",
    "SeverityLabel",
    "TestRecommendation",
    "aggregate_founder_report",
    "evaluate_report_quality",
    "render_markdown_report",
    "scan_for_secrets",
]
