"""Phase 8.2F — conservative grouping of source_records into candidate
persona shells.

Each candidate persona shell is the input to one trait-extraction call
and (if extraction yields ≥ 3 valid traits) one persona row. Grouping
discipline:

  1. Records classified as `context_only` or `reject_for_sensitive_or_identity_risk`
     are EXCLUDED from candidate shells. They never reach extraction.

  2. Records that share a non-null `user_handle_hash` are grouped into
     ONE shell. The handle hash is the strongest cross-record signal we
     have, since the redaction pipeline preserves it (salted) when an
     adapter knows it has the same source author.

  3. Records WITHOUT a handle hash are grouped ONLY when they share the
     EXACT same `source_url`. Same domain alone is NOT enough.

  4. Same query / same source_kind alone is NEVER enough to group.
     Phase 8.2F deliberately under-groups: a single strong-persona-
     signal record becomes its own shell. Cross-record merging is
     deferred to a future phase that can verify cross-record identity
     at the prose level.

Returns a list of `CandidatePersonaShell` dataclasses, each carrying:
  - shell_id (deterministic per-run hash)
  - source_records (the underlying records that seed this shell)
  - dominant_classification (always strong or weak — context-only is
    excluded earlier)
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from assembly.pipeline.persona_construction.source_classifier import (
    SourceClassification,
    SourceClassificationReport,
)


@dataclass(frozen=True)
class CandidateRecord:
    """Compact view of one source_record + its classification report."""
    record_id: UUID
    source_kind: str
    source_url: str | None
    user_handle_hash: str | None
    content: str
    metadata: dict[str, Any]
    classification: SourceClassificationReport


@dataclass(frozen=True)
class CandidatePersonaShell:
    shell_id: str
    record_ids: tuple[UUID, ...]
    dominant_classification: SourceClassification
    aggregated_content: str
    aggregated_metadata: dict[str, Any] = field(default_factory=dict)


def group_records_into_shells(
    records: Iterable[CandidateRecord],
) -> list[CandidatePersonaShell]:
    """Group classified records into candidate persona shells.

    Skips `context_only` and `reject_for_sensitive_or_identity_risk`.
    Groups by `user_handle_hash` first, then by exact `source_url`.
    Records that share neither signal each become their own shell.
    """
    items: list[CandidateRecord] = list(records)

    # 1) Filter: only strong / weak persona-signal records seed shells.
    eligible = [
        r for r in items
        if r.classification.classification in (
            SourceClassification.STRONG_PERSONA_SIGNAL,
            SourceClassification.WEAK_PERSONA_SIGNAL,
        )
    ]
    if not eligible:
        return []

    # 2) Bucket by group key.
    buckets: dict[str, list[CandidateRecord]] = {}
    for r in eligible:
        key = _group_key(r)
        buckets.setdefault(key, []).append(r)

    shells: list[CandidatePersonaShell] = []
    for key, members in buckets.items():
        # Stable order: by record_id so re-runs produce identical shells.
        members_sorted = sorted(members, key=lambda r: str(r.record_id))
        ids = tuple(r.record_id for r in members_sorted)
        shell_id = hashlib.sha256(
            ("|".join(str(rid) for rid in ids)).encode("utf-8"),
        ).hexdigest()[:16]
        # Dominant classification: STRONG if any member is strong,
        # else WEAK (we already filtered out context_only / rejects).
        dominant = (
            SourceClassification.STRONG_PERSONA_SIGNAL
            if any(
                r.classification.classification
                == SourceClassification.STRONG_PERSONA_SIGNAL
                for r in members_sorted
            )
            else SourceClassification.WEAK_PERSONA_SIGNAL
        )
        # Aggregate content with a clear `### record N` separator so the
        # extractor sees record-boundary structure.
        chunks: list[str] = []
        for i, r in enumerate(members_sorted, start=1):
            chunks.append(f"### record {i} (id={r.record_id})\n{r.content}")
        aggregated = "\n\n".join(chunks)
        # Aggregate metadata as a list of per-record summaries.
        agg_md = {
            "record_count": len(members_sorted),
            "source_kinds": sorted({r.source_kind for r in members_sorted}),
            "source_urls": [r.source_url for r in members_sorted],
            "queries": sorted({
                str(r.metadata.get("query"))
                for r in members_sorted
                if r.metadata.get("query")
            }),
        }
        shells.append(
            CandidatePersonaShell(
                shell_id=shell_id,
                record_ids=ids,
                dominant_classification=dominant,
                aggregated_content=aggregated,
                aggregated_metadata=agg_md,
            )
        )

    return shells


def _group_key(r: CandidateRecord) -> str:
    """Build the grouping key for a single record.

    Priority:
      1. user_handle_hash if present (one persona per handle within run)
      2. exact source_url if present (one persona per URL — handles a
         single review thread or comment cluster)
      3. record_id-only key (its own shell)
    """
    if r.user_handle_hash:
        return f"handle:{r.user_handle_hash}"
    if r.source_url:
        return f"url:{r.source_url}"
    return f"rid:{r.record_id}"
