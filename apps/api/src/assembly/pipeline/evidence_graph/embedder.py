"""Phase 6.75 — embedding pass.

Embeds eligible evidence_items rows and writes the vector via raw SQL
(SQLAlchemy's pgvector adapter is added in a later phase; for V0 we use
a parameterized UPDATE that pgvector accepts as a string-cast literal).

Cutoff-date eligibility (Correction 3):
  - rows with `captured_at IS NULL` are embedded ONLY for source_type
    in {user_input, analogical_market} or kind='missing' or rows with
    explicit snapshot metadata.
  - rows with `captured_at > cutoff_date` are skipped.

Every embedding call goes through `cost_guarded_embed`. Empty content
items are skipped (mock provider returns None for those anyway).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.embeddings.provider import EmbeddingProvider
from assembly.llm.guarded_chat import cost_guarded_embed
from assembly.models.evidence import EvidenceItem
from assembly.pipeline.evidence_graph.edge_builder import _captured_at_eligible

logger = logging.getLogger(__name__)


async def embed_eligible_items(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    embedding_provider: EmbeddingProvider,
    cutoff_date: date | None,
    batch_size: int = 32,
) -> int:
    """Embed every eligible item that has `embedding IS NULL`. Returns the
    count of vectors persisted."""
    async with sessionmaker() as session:
        # Select rows that need embedding. We can't easily filter on the
        # vector column being NULL via SQLAlchemy without a schema-level
        # mapping; use the embedded_at column as the proxy (it goes non-null
        # only when an embedding was written).
        items = (
            await session.execute(
                select(EvidenceItem)
                .where(EvidenceItem.simulation_id == simulation_id)
                .where(EvidenceItem.embedded_at.is_(None))
            )
        ).scalars().all()

    eligible = [
        i for i in items
        if i.content
        and i.kind != "missing"  # missing rows have no real content to embed
        and _captured_at_eligible(i, cutoff_date)
    ]
    if not eligible:
        return 0

    persisted = 0
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start : start + batch_size]
        texts = [(i.content or "")[:4000] for i in batch]
        vectors = await cost_guarded_embed(
            sessionmaker=sessionmaker,
            simulation_id=simulation_id,
            stage="embed_evidence",
            texts=texts,
            provider=embedding_provider,
        )
        # Persist via raw SQL. pgvector accepts a string literal of the form
        # '[0.1, 0.2, ...]'. SQLAlchemy parameters are bound positionally to
        # avoid a custom type adapter for V0.
        async with sessionmaker() as session:
            async with session.begin():
                now = datetime.now(UTC)
                for item, vec in zip(batch, vectors, strict=True):
                    if vec is None:
                        continue
                    vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
                    await session.execute(
                        text(
                            "UPDATE evidence_items "
                            "SET embedding = (:vec)::vector, "
                            "    embedding_model = :model, "
                            "    embedded_at = :now "
                            "WHERE id = :id"
                        ).bindparams(
                            bindparam("vec"),
                            bindparam("model"),
                            bindparam("now"),
                            bindparam("id"),
                        ),
                        {
                            "vec": vec_str,
                            "model": embedding_provider.name,
                            "now": now,
                            "id": item.id,
                        },
                    )
                    persisted += 1
    return persisted
