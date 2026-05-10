"""Phase 9D — cohort/cluster architecture for huge synthetic societies.

Builds run-scoped, brief-scoped, evidence-anchored cohort summaries
over an existing 9B 66-person society. Universal — no LumaLoop
hardcoding, no global cohorts, no permanent market segments.

NO new retrieval. NO mutation of any 9A/9B row. The output is three
additive tables (society_cohorts / society_cohort_evidence_links /
society_cohort_rollups) plus a founder-facing report.
"""
from assembly.sources.cohort_architecture.clusterer import (
    cluster_personas_into_cohorts,
)
from assembly.sources.cohort_architecture.evaluator import (
    evaluate_cohort_architecture_quality,
)
from assembly.sources.cohort_architecture.feature_builder import (
    build_cohort_feature_vectors,
)
from assembly.sources.cohort_architecture.report import (
    render_cohort_report_json,
    render_cohort_report_markdown,
)
from assembly.sources.cohort_architecture.representatives import (
    select_cohort_representatives,
)
from assembly.sources.cohort_architecture.rollup import (
    build_society_rollup,
)
from assembly.sources.cohort_architecture.summarizer import (
    summarize_cohort,
)


__all__ = [
    "build_cohort_feature_vectors",
    "build_society_rollup",
    "cluster_personas_into_cohorts",
    "evaluate_cohort_architecture_quality",
    "render_cohort_report_json",
    "render_cohort_report_markdown",
    "select_cohort_representatives",
    "summarize_cohort",
]
