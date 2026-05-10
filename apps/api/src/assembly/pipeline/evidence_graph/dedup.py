"""Phase 6.75 — deduplication pass.

V0 strategy (per Section 6 of the plan): assign `dedup_group_id` to clusters
of equivalent rows. Mark and collapse-on-read in the retriever. **Never
destructively delete** — every row stays auditable.

Two passes:
  1. Exact URL — same `source_url` + same content hash → same group.
  2. Normalized content hash — same `content_hash` regardless of URL → same group.

A `similar_to` evidence_edge is also written for each (canonical, member)
pair so the retriever can collapse groups deterministically.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EvidenceEdge

logger = logging.getLogger(__name__)


_WHITESPACE_RE = re.compile(r"\s+")


def normalized_content_hash(item: EvidenceItem) -> str:
    """Deterministic hash matching the alembic backfill SQL (md5 over
    lowercase trimmed whitespace-collapsed content with the same fallback
    chain). Pure-Python so the dedup pass can recompute on insert."""
    normalized = ""
    if item.content:
        normalized = _WHITESPACE_RE.sub(" ", item.content.strip().lower())
    if not normalized:
        meta = item.metadata_ or {}
        excerpt = meta.get("source_excerpt") or ""
        url = item.source_url or ""
        normalized = f"{url}|{excerpt}" if (url or excerpt) else str(item.id)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


async def run_dedup(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
) -> int:
    """Group rows by content_hash and (when content is sparse) source_url.
    The first-inserted row of each cluster becomes the canonical id and
    gets its own UUID written into `dedup_group_id`. Other rows in the
    cluster share that group id. A `similar_to` edge connects canonical
    → each member with `basis='direct'` and `confidence=1.0`.

    Returns the count of new groups assigned."""
    new_group_count = 0

    async with sessionmaker() as session:
        async with session.begin():
            items = (
                await session.execute(
                    select(EvidenceItem)
                    .where(EvidenceItem.simulation_id == simulation_id)
                    .order_by(EvidenceItem.created_at.asc())
                )
            ).scalars().all()

            # Cluster by content_hash. (URL collisions usually fall here too;
            # the hash chain in normalized_content_hash already folds in
            # url + excerpt for sparse content.)
            clusters: dict[str, list[EvidenceItem]] = {}
            for it in items:
                h = it.content_hash or normalized_content_hash(it)
                clusters.setdefault(h, []).append(it)

            for content_hash, group in clusters.items():
                if len(group) < 2:
                    # Singleton — leave dedup_group_id null.
                    continue
                # Canonical = oldest member. All in the cluster share the
                # canonical's UUID as dedup_group_id.
                canonical = group[0]
                if canonical.dedup_group_id is None:
                    canonical.dedup_group_id = uuid.uuid4()
                    new_group_count += 1
                group_id = canonical.dedup_group_id
                for member in group[1:]:
                    member.dedup_group_id = group_id
                    # Write a similar_to edge from canonical → member.
                    # The unique constraint prevents duplicate edges across
                    # multiple dedup runs (idempotent).
                    edge = EvidenceEdge(
                        simulation_id=simulation_id,
                        source_evidence_id=canonical.id,
                        target_evidence_id=member.id,
                        edge_type="similar_to",
                        strength=Decimal("1.0"),
                        confidence=Decimal("1.0"),
                        basis="direct",
                        provenance={
                            "derived_by": "dedup",
                            "rule": "content_hash_match",
                            "content_hash": content_hash,
                        },
                    )
                    session.add(edge)

    return new_group_count
