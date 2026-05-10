"""Phase 8.2F — persona persistence layer.

Single blessed write surface for `persona_records`, `persona_traits`,
and `persona_evidence_links`. Every write goes through
`persist_candidate_persona`; no other module in
`pipeline/persona_construction/` is allowed to construct those ORM
rows (the drift test asserts).

Discipline:

  - Each candidate trait is validated against
    `validate_persona_trait_payload` (Phase 8.2A) BEFORE it counts
    toward the ≥ 3 valid-trait requirement.

  - A persona is created only if ≥ 3 traits validate.

  - For every direct/inferred valid trait, the source_excerpt MUST
    appear verbatim in at least one of the shell's source_records'
    `content`. The matching record_id(s) become the trait's
    `source_ids` AND a `persona_evidence_link` row.

  - `display_name` is generated via Phase 8.2A's
    `generate_display_name`. It is the only random field. The seed
    is derived from the deterministic `shell_id` so re-runs are
    reproducible.

  - No raw handle, real name, profile URL, or identity-bearing field
    is carried into persona / trait / link rows. The redaction
    pipeline already runs at storage time for source_records;
    this layer enforces the persona-side firewall by validating each
    `value` and `rationale` via the persona validator.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.persona import (
    PersonaEvidenceLink,
    PersonaRecord,
    PersonaTrait,
    SourceRecord,
)
from assembly.pipeline.persona.anonymization import generate_display_name
from assembly.pipeline.persona.validator import (
    ValidationViolation,
    validate_persona_trait_payload,
)
from assembly.pipeline.persona_construction.extractor import TraitCandidate
from assembly.pipeline.persona_construction.grouping import (
    CandidatePersonaShell,
)
from assembly.pipeline.persona_construction.summary import SkippedShellReason


MIN_VALID_TRAITS_PER_PERSONA = 3


@dataclass(frozen=True)
class _RecordContent:
    """Lightweight record-content view used to bind excerpts → source_ids."""
    record_id: UUID
    content: str


@dataclass(frozen=True)
class PersistOutcome:
    """Result of attempting to persist one shell into a persona."""
    persona_id: UUID | None
    traits_persisted: int
    traits_rejected: int
    evidence_links_persisted: int
    skipped_reason: SkippedShellReason | None = None
    violations: tuple[ValidationViolation, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def persist_candidate_persona(
    *,
    sessionmaker: async_sessionmaker,
    shell: CandidatePersonaShell,
    candidates: Sequence[TraitCandidate],
    record_contents: Sequence[_RecordContent],
    write: bool,
) -> PersistOutcome:
    """Validate + (optionally) persist one candidate persona.

    Args:
      shell:           grouped persona shell (from `grouping.py`)
      candidates:      extractor-emitted trait candidates
      record_contents: list of (record_id, content) tuples for the
                       shell's underlying source_records — used to bind
                       trait `source_excerpt` to specific record IDs
      write:           when False, no DB writes happen; the function
                       still returns a `PersistOutcome` reporting how
                       many traits would have been valid

    Returns a `PersistOutcome`. The outcome's `persona_id` is None when:
      - the shell does not yield ≥ 3 valid traits, OR
      - `write=False` and the shell would have yielded ≥ 3 valid traits
        (the worker uses `traits_persisted` to count "would-have-created").
    """
    # 1) Validate every candidate via Phase 8.2A's validator.
    valid_pairs: list[tuple[TraitCandidate, list[UUID]]] = []
    rejected = 0
    accumulated_violations: list[ValidationViolation] = []
    seen_field_names: set[str] = set()

    for c in candidates:
        # Bind source_excerpt → source_ids first; the validator needs
        # source_ids on direct/inferred traits.
        source_ids = (
            _resolve_source_ids(c.source_excerpt, record_contents)
            if c.source_excerpt and c.support_level in ("direct", "inferred")
            else []
        )
        payload = {
            "field_name": c.field_name,
            "support_level": c.support_level,
            "value": c.value,
            "source_ids": source_ids,
            "confidence": float(c.confidence),
            "rationale": c.rationale,
        }
        result = validate_persona_trait_payload(payload)
        if not result.passed:
            rejected += 1
            accumulated_violations.extend(result.violations)
            continue
        if c.field_name in seen_field_names:
            # Drop second-or-later trait for the same field — the DB
            # UNIQUE (persona_id, field_name) would reject anyway.
            rejected += 1
            continue
        seen_field_names.add(c.field_name)
        valid_pairs.append((c, source_ids))

    # 2) Threshold check: ≥ 3 valid traits with at least one direct/inferred.
    direct_or_inferred = sum(
        1 for c, _ in valid_pairs
        if c.support_level in ("direct", "inferred")
    )
    if len(valid_pairs) < MIN_VALID_TRAITS_PER_PERSONA:
        return PersistOutcome(
            persona_id=None,
            traits_persisted=0,
            traits_rejected=rejected,
            evidence_links_persisted=0,
            skipped_reason=SkippedShellReason(
                shell_id=shell.shell_id,
                reason_code="FEWER_THAN_MIN_VALID_TRAITS",
                message=(
                    f"Shell yielded {len(valid_pairs)} valid traits; "
                    f"minimum is {MIN_VALID_TRAITS_PER_PERSONA}."
                ),
            ),
            violations=tuple(accumulated_violations),
        )
    if direct_or_inferred < 1:
        return PersistOutcome(
            persona_id=None,
            traits_persisted=0,
            traits_rejected=rejected,
            evidence_links_persisted=0,
            skipped_reason=SkippedShellReason(
                shell_id=shell.shell_id,
                reason_code="NO_SOURCE_BACKED_TRAIT",
                message=(
                    "Shell yielded ≥3 valid traits but none are "
                    "direct/inferred — at least one source-bound trait "
                    "is required to anchor the persona."
                ),
            ),
            violations=tuple(accumulated_violations),
        )

    # 3) Dry-run: report the would-have-counts but do not write.
    if not write:
        # Count evidence-links that WOULD be created.
        would_links = sum(
            len(sids) for _, sids in valid_pairs if sids
        )
        return PersistOutcome(
            persona_id=None,
            traits_persisted=len(valid_pairs),
            traits_rejected=rejected,
            evidence_links_persisted=would_links,
            skipped_reason=None,
            violations=tuple(accumulated_violations),
        )

    # 4) Write mode.
    return await _do_write(
        sessionmaker=sessionmaker,
        shell=shell,
        valid_pairs=valid_pairs,
        rejected=rejected,
        violations=accumulated_violations,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_source_ids(
    excerpt: str,
    record_contents: Sequence[_RecordContent],
) -> list[UUID]:
    """Find which source_records' content contains `excerpt` verbatim.
    Substring match — case-sensitive. The redaction pipeline already
    normalized the source content; we want exact matches so the
    persona evidence trail is not loosely tied."""
    if not excerpt:
        return []
    out: list[UUID] = []
    for rc in record_contents:
        if excerpt in rc.content:
            out.append(rc.record_id)
    return out


async def _do_write(
    *,
    sessionmaker: async_sessionmaker,
    shell: CandidatePersonaShell,
    valid_pairs: list[tuple[TraitCandidate, list[UUID]]],
    rejected: int,
    violations: list[ValidationViolation],
) -> PersistOutcome:
    """Single-transaction insert: persona_records + persona_traits +
    persona_evidence_links. Roll back atomically on any failure."""
    persona_id = uuid4()
    display_name = generate_display_name(shell.shell_id)
    now = datetime.now(UTC)

    traits_persisted = 0
    links_persisted = 0
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                PersonaRecord(
                    id=persona_id,
                    display_name=display_name,
                    segment_label=None,
                    origin_market_broad=None,
                    product_relevance_tags=[],
                    influence_score=None,
                    susceptibility=None,
                    population_weight=Decimal("1.0"),
                    source_strength_score=None,
                    refreshed_at=now,
                )
            )
            await session.flush()
            for cand, source_ids in valid_pairs:
                trait = PersonaTrait(
                    id=uuid4(),
                    persona_id=persona_id,
                    field_name=cand.field_name,
                    value=cand.value,
                    support_level=cand.support_level,
                    source_ids=source_ids,
                    confidence=Decimal(str(cand.confidence)),
                    rationale=cand.rationale,
                    last_updated_at=now,
                )
                session.add(trait)
                traits_persisted += 1
                # One persona_evidence_link per (persona, source_record,
                # field_name) — the table's UNIQUE enforces this.
                for sid in source_ids:
                    session.add(
                        PersonaEvidenceLink(
                            id=uuid4(),
                            persona_id=persona_id,
                            source_record_id=sid,
                            contribution_kind=cand.support_level,
                            contribution_field=cand.field_name,
                            excerpt=(cand.source_excerpt or "")[:1000],
                            excerpt_offset=None,
                            confidence=Decimal(str(cand.confidence)),
                        )
                    )
                    links_persisted += 1

    return PersistOutcome(
        persona_id=persona_id,
        traits_persisted=traits_persisted,
        traits_rejected=rejected,
        evidence_links_persisted=links_persisted,
        skipped_reason=None,
        violations=tuple(violations),
    )
