"""Phase 8.2F — persona construction worker.

Turns sanitized `source_records` into anonymous, source-grounded
persona candidates. Three guarantees the package is built around:

  1. NEVER create a fake persona. Generic articles, pricing pages, and
     landing pages are explicitly classified as `context_only` and
     refused as persona seeds.

  2. NEVER override source evidence with mechanism priors. The trait
     extractor + persistence layer enforce that every direct/inferred
     trait carries `source_ids`; behavioral mechanism integration is
     limited to "hint" rows in the audit table — never persona facts.

  3. NEVER write personas without explicit `write_personas=True`. The
     default `worker.run_persona_construction(...)` is dry-run; it
     classifies + groups + simulates extraction + reports counts, but
     does not touch the persona-table writes.

Public surface:
  - `classify_source_record`              source-quality heuristic classifier
  - `SourceClassification`                closed-enum classification result
  - `group_records_into_shells`           conservative grouping
  - `CandidatePersonaShell`               grouped-records record
  - `MockTraitExtractor`                  deterministic test extractor
  - `LLMTraitExtractor`                   cost-guarded live extractor (not
                                          invoked in 8.2F's dry-run)
  - `run_persona_construction`            top-level orchestrator
  - `PersonaConstructionRunSummary`       structured run result

The drift test in `tests/test_no_drift_persona_construction.py`
asserts that this package contains no provider calls, no network
imports, and no `source_records` writes.
"""
from assembly.pipeline.persona_construction.extractor import (
    LLMTraitExtractor,
    MockTraitExtractor,
    TraitCandidate,
    TraitExtractionResult,
)
from assembly.pipeline.persona_construction.grouping import (
    CandidatePersonaShell,
    group_records_into_shells,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    classify_source_record,
)
from assembly.pipeline.persona_construction.summary import (
    PersonaConstructionRunSummary,
    SkippedShellReason,
)
from assembly.pipeline.persona_construction.worker import (
    run_persona_construction,
)


__all__ = [
    "CandidatePersonaShell",
    "LLMTraitExtractor",
    "MockTraitExtractor",
    "PersonaConstructionRunSummary",
    "SkippedShellReason",
    "SourceClassification",
    "TraitCandidate",
    "TraitExtractionResult",
    "classify_source_record",
    "group_records_into_shells",
    "run_persona_construction",
]
