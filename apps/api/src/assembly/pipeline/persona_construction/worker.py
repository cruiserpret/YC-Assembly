"""Phase 8.2F — top-level persona construction orchestrator.

`run_persona_construction(...)` takes a sequence of `SourceRecord`s
plus a `TraitExtractor` and returns a structured
`PersonaConstructionRunSummary`. Default mode is `dry_run=True,
write_personas=False` — no `persona_*` rows are written.

Pipeline:

  1. Classify every source_record via `classify_source_record`.
  2. Group eligible records (strong + weak persona signal) into
     candidate persona shells via `group_records_into_shells`.
  3. For each shell, call `extractor.extract(shell)` to get trait
     candidates. (The dry-run path uses a `MockTraitExtractor` with
     empty candidates by default — extraction is OFF in dry-run unless
     the caller wires in their own extractor.)
  4. Validate + persist each candidate persona via
     `persist_candidate_persona`. The persistence layer enforces the
     ≥ 3-valid-traits rule; shells below the threshold are skipped
     with a structured reason code.
  5. Return the summary.

Behavioral mechanism integration is intentionally light here: the
worker accepts an optional `mechanism_audit_writer` callable. If
provided, it is called once per shell (after extraction) with a
structured audit-row payload — so the caller can decide what to
write into `mechanism_initialization_audit`. The worker itself never
constructs that row; the audit module remains the single blessed
write surface (Phase 8.2D rule).

Critical guards:
  - `write_personas=True` is the ONLY way persona rows reach the DB.
  - Extraction is OFF unless a non-default extractor is passed.
  - Mechanism priors NEVER override source evidence (the validator
    enforces; the worker carries the rule via documentation only —
    there is no code path that lets a mechanism prior synthesize a
    persona trait).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.persona import SourceRecord
from assembly.pipeline.persona_construction.extractor import (
    MockTraitExtractor,
    TraitExtractor,
    TraitExtractionResult,
)
from assembly.pipeline.persona_construction.grouping import (
    CandidateRecord,
    CandidatePersonaShell,
    group_records_into_shells,
)
from assembly.pipeline.persona_construction.persistence import (
    _RecordContent,
    persist_candidate_persona,
)
from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    classify_source_record,
)
from assembly.pipeline.persona_construction.summary import (
    PersonaConstructionRunSummary,
    SkippedShellReason,
)


MechanismAuditWriter = Callable[
    [CandidatePersonaShell, TraitExtractionResult],
    Awaitable[None],
]


async def run_persona_construction(
    *,
    sessionmaker: async_sessionmaker,
    source_records: Sequence[SourceRecord],
    extractor: TraitExtractor | None = None,
    write_personas: bool = False,
    dry_run: bool | None = None,
    mechanism_audit_writer: MechanismAuditWriter | None = None,
) -> PersonaConstructionRunSummary:
    """Execute the full classify → group → extract → validate →
    (optionally) persist pipeline.

    Args:
      sessionmaker:
        Required even in dry-run mode for transactional consistency
        of the underlying DB engine. Dry-run does NOT issue writes.
      source_records:
        Pre-loaded SourceRecord rows. Caller decides what to filter
        in (e.g. `source_kind='tavily_search_extract'`).
      extractor:
        TraitExtractor implementation. Defaults to a no-op
        `MockTraitExtractor` with zero candidates — i.e. classification
        + grouping happens, but no persona is ever created. Pass a
        real extractor (mock or `LLMTraitExtractor`) to actually
        attempt persona creation.
      write_personas:
        Set True to actually write persona / trait / evidence-link
        rows. Default False.
      dry_run:
        Convenience override. Default mirrors `not write_personas`.
        If both are set, `write_personas` wins.
      mechanism_audit_writer:
        Optional callback invoked once per shell after extraction.

    Returns: `PersonaConstructionRunSummary`.
    """
    if extractor is None:
        extractor = MockTraitExtractor()
    if dry_run is None:
        dry_run = not write_personas

    summary = PersonaConstructionRunSummary(
        dry_run=dry_run,
        wrote_personas=write_personas,
        source_records_seen=len(source_records),
    )

    # 1) Classify.
    classified: list[CandidateRecord] = []
    for r in source_records:
        report = classify_source_record(
            content=r.content,
            source_url=r.source_url,
            metadata=r.metadata_,
            user_handle_hash=r.user_handle_hash,
        )
        if report.classification == SourceClassification.STRONG_PERSONA_SIGNAL:
            summary.strong_persona_signal_records += 1
        elif report.classification == SourceClassification.WEAK_PERSONA_SIGNAL:
            summary.weak_persona_signal_records += 1
        elif report.classification == SourceClassification.CONTEXT_ONLY:
            summary.context_only_records += 1
        else:
            summary.rejected_records += 1
        classified.append(
            CandidateRecord(
                record_id=r.id,
                source_kind=r.source_kind,
                source_url=r.source_url,
                user_handle_hash=r.user_handle_hash,
                content=r.content,
                metadata=r.metadata_ or {},
                classification=report,
            )
        )

    # 2) Group into shells.
    shells = group_records_into_shells(classified)
    summary.candidate_shells = len(shells)

    # 3+4) Extract + validate + (optionally) persist per shell.
    record_content_lookup = {
        r.id: r.content for r in source_records
    }
    for shell in shells:
        extraction = await extractor.extract(shell)
        summary.shells_with_extraction_attempted += 1
        if mechanism_audit_writer is not None:
            await mechanism_audit_writer(shell, extraction)

        record_contents = [
            _RecordContent(record_id=rid, content=record_content_lookup[rid])
            for rid in shell.record_ids
            if rid in record_content_lookup
        ]
        outcome = await persist_candidate_persona(
            sessionmaker=sessionmaker,
            shell=shell,
            candidates=extraction.candidates,
            record_contents=record_contents,
            write=write_personas,
        )
        if outcome.skipped_reason is not None:
            summary.personas_skipped += 1
            summary.skipped_reasons.append(outcome.skipped_reason)
            summary.traits_rejected += outcome.traits_rejected
            continue

        # Either we just wrote (persona_id is set), or we'd have written
        # in non-dry-run mode (persona_id None, but traits_persisted ≥ 3).
        if outcome.persona_id is not None:
            summary.personas_created += 1
            summary.traits_created += outcome.traits_persisted
            summary.evidence_links_created += outcome.evidence_links_persisted
            summary.shells_with_three_or_more_valid_traits += 1
        else:
            # Dry-run with valid threshold met: track as
            # would-have-created.
            summary.shells_with_three_or_more_valid_traits += 1

        summary.traits_rejected += outcome.traits_rejected

    return summary
