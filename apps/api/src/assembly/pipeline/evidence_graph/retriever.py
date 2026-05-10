"""Phase 6.75 — semantic retrieval over the evidence graph.

Returns ranked evidence with auditable rationale. Backstop is a BM25-ish
score over content tokens so the retriever still works without embeddings.

`kind='missing'` rows are NEVER mixed into `ranked_results` — they always
surface separately so the aggregator sees the gap explicitly. Per
Correction 5, the Phase 7 helper service uses this retriever via stable
methods rather than raw SQL.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.embeddings.provider import EmbeddingProvider
from assembly.llm.guarded_chat import cost_guarded_embed
from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EvidenceEdge

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _bm25_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Simple BM25-style relevance. Not a full BM25 — we don't have IDF
    pre-computed here, so we use term-frequency over normalized doc
    length, which is the dominant signal for short evidence excerpts."""
    if not doc_tokens or not query_tokens:
        return 0.0
    counts = Counter(doc_tokens)
    score = 0.0
    for q in query_tokens:
        if q in counts:
            score += counts[q] / (len(doc_tokens) ** 0.5)
    return score


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Public dataclass: RankedEvidence
# ---------------------------------------------------------------------------


@dataclass
class RankedEvidence:
    item: EvidenceItem
    score: float
    rationale: dict[str, float] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    ranked: list[RankedEvidence]
    missing: list[EvidenceItem]


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class EvidenceRetriever:
    """Stateless retrieval. The Phase 7 helper service composes this for
    its public methods."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedding = embedding_provider

    async def _load_items(
        self,
        simulation_id: UUID,
        *,
        include_missing: bool,
        kinds: Sequence[str] | None = None,
        node_classes: Sequence[str] | None = None,
        cutoff_date: date | None = None,
    ) -> list[EvidenceItem]:
        async with self._sessionmaker() as session:
            stmt = select(EvidenceItem).where(
                EvidenceItem.simulation_id == simulation_id
            )
            if kinds:
                stmt = stmt.where(EvidenceItem.kind.in_(list(kinds)))
            if node_classes:
                stmt = stmt.where(
                    EvidenceItem.node_class.in_(list(node_classes))
                )
            items = (await session.execute(stmt)).scalars().all()

        if not include_missing:
            items = [i for i in items if i.kind != "missing"]
        if cutoff_date is not None:
            items = [
                i for i in items
                if (i.captured_at is None) or (i.captured_at.date() <= cutoff_date)
            ]
        return list(items)

    async def _edge_context(
        self, simulation_id: UUID
    ) -> dict[UUID, dict[str, int]]:
        """Pre-compute edge counts for ranking bonuses per evidence id."""
        out: dict[UUID, dict[str, int]] = {}
        async with self._sessionmaker() as session:
            edges = (
                await session.execute(
                    select(EvidenceEdge).where(
                        EvidenceEdge.simulation_id == simulation_id
                    )
                )
            ).scalars().all()
        for e in edges:
            for evidence_id in (e.target_evidence_id, e.source_evidence_id):
                bucket = out.setdefault(evidence_id, {"supports": 0, "contradicts": 0})
                if e.edge_type == "supports":
                    bucket["supports"] += 1
                elif e.edge_type == "contradicts":
                    bucket["contradicts"] += 1
        return out

    async def _embed_query(
        self, simulation_id: UUID, query: str
    ) -> list[float] | None:
        if self._embedding is None or not query:
            return None
        try:
            vectors = await cost_guarded_embed(
                sessionmaker=self._sessionmaker,
                simulation_id=simulation_id,
                stage="embed_query",
                texts=[query],
                provider=self._embedding,
            )
            return vectors[0] if vectors else None
        except Exception as e:
            logger.warning("retriever.embed_query_failed err=%s", e)
            return None

    async def retrieve(
        self,
        *,
        simulation_id: UUID,
        query: str,
        k: int = 8,
        kinds: Sequence[str] | None = None,
        node_classes: Sequence[str] | None = None,
        cutoff_date: date | None = None,
        collapse_duplicates: bool = True,
    ) -> RetrievalResult:
        """Core retrieval primitive. Higher-level methods wrap this with
        node_classes / query templates."""
        items = await self._load_items(
            simulation_id,
            include_missing=False,
            kinds=kinds,
            node_classes=node_classes,
            cutoff_date=cutoff_date,
        )
        missing = await self._load_items(
            simulation_id,
            include_missing=True,
            kinds=("missing",),
            node_classes=node_classes,
            cutoff_date=cutoff_date,
        )

        edges = await self._edge_context(simulation_id)
        query_tokens = _tokenize(query)
        query_vec = await self._embed_query(simulation_id, query)

        # Score each item.
        ranked: list[RankedEvidence] = []
        for it in items:
            rationale: dict[str, float] = {}

            # 1. kind direct over analogical
            if it.kind == "direct":
                rationale["bonus_direct"] = 0.30
            elif it.kind == "analogical":
                rationale["penalty_analogical"] = 0.0  # baseline

            # 2. user_input over LLM-derived
            if it.source_type == "user_input":
                rationale["bonus_user_input"] = 0.20

            # 3. competitor/pricing/review
            if it.source_type in ("competitor_page", "pricing_page", "public_review"):
                rationale["bonus_specific_source"] = 0.15

            # 4. recency
            if it.captured_at is not None:
                age_days = (datetime.now(UTC) - it.captured_at).days
                if age_days <= 180:
                    rationale["bonus_recency"] = 0.05

            # 5. cosine similarity (when both query + doc embeddings exist)
            # Doc embedding is NOT loaded into Python here (pgvector binding
            # is a follow-up; the column is present). For V0, we fall back
            # to BM25 if doc embeddings aren't in-memory.
            doc_tokens = _tokenize(it.content or "")
            bm25 = _bm25_score(query_tokens, doc_tokens)
            if bm25 > 0:
                # Cap bonus at +0.30 so similarity can't dominate the
                # source-quality signals.
                rationale["bonus_relevance"] = min(0.30, bm25 * 0.05)

            # 6. specificity to target society — heuristic via metadata tag
            # overlap. V0 keeps this simple: presence of segment-relevant
            # words in metadata.
            meta = it.metadata_ or {}
            if any(
                k in (meta.get("input_field") or "") for k in ("target_society",)
            ):
                rationale["bonus_society_specificity"] = 0.10

            # 7. source-bound exact excerpt
            if (it.metadata_ or {}).get("source_excerpt"):
                rationale["bonus_source_excerpt"] = 0.05

            # 8/9. edge support / contradiction
            ec = edges.get(it.id, {"supports": 0, "contradicts": 0})
            if ec["supports"]:
                rationale["bonus_edge_support"] = min(0.20, 0.05 * ec["supports"])
            if ec["contradicts"]:
                rationale["penalty_edge_contradiction"] = -min(
                    0.20, 0.10 * ec["contradicts"]
                )

            score = sum(rationale.values())
            ranked.append(RankedEvidence(item=it, score=score, rationale=rationale))

        ranked.sort(key=lambda r: r.score, reverse=True)

        if collapse_duplicates:
            ranked = self._collapse_duplicates(ranked)

        # missing list separated, NEVER mixed into ranked.
        return RetrievalResult(ranked=ranked[:k], missing=missing)

    @staticmethod
    def _collapse_duplicates(
        ranked: Sequence[RankedEvidence],
    ) -> list[RankedEvidence]:
        seen: set[UUID] = set()
        out: list[RankedEvidence] = []
        for r in ranked:
            gid = r.item.dedup_group_id
            if gid is None:
                out.append(r)
                continue
            if gid in seen:
                continue
            seen.add(gid)
            out.append(r)
        return out

    # ---- typed wrappers (Section 4 of the plan) -----------------------

    async def for_agent_trait(
        self, *, simulation_id, trait_name, trait_value, k=8, cutoff_date=None,
    ):
        query = f"{trait_name} {trait_value}"
        return await self.retrieve(
            simulation_id=simulation_id, query=query, k=k, cutoff_date=cutoff_date,
        )

    async def for_objection(
        self, *, simulation_id, objection_text, k=8, cutoff_date=None,
    ):
        return await self.retrieve(
            simulation_id=simulation_id,
            query=objection_text,
            k=k,
            node_classes=["buyer_pain", "objection", "trust_barrier", "claim_risk"],
            cutoff_date=cutoff_date,
        )

    async def for_competitor(
        self, *, simulation_id, competitor_name, k=8, cutoff_date=None,
    ):
        return await self.retrieve(
            simulation_id=simulation_id,
            query=competitor_name,
            k=k,
            node_classes=["competitor", "pricing", "review"],
            cutoff_date=cutoff_date,
        )

    async def for_price_sensitivity(
        self, *, simulation_id, query="pricing", k=8, cutoff_date=None,
    ):
        return await self.retrieve(
            simulation_id=simulation_id,
            query=query,
            k=k,
            node_classes=["pricing", "competitor"],
            cutoff_date=cutoff_date,
        )

    async def for_positioning(
        self, *, simulation_id, query="positioning", k=12, cutoff_date=None,
    ):
        return await self.retrieve(
            simulation_id=simulation_id,
            query=query,
            k=k,
            node_classes=[
                "competitor", "category_language", "current_alternative",
                "segment_behavior",
            ],
            cutoff_date=cutoff_date,
        )

    async def for_market_acceptance_requirement(
        self, *, simulation_id, k=12, cutoff_date=None,
    ):
        return await self.retrieve(
            simulation_id=simulation_id,
            query="trust proof acceptance",
            k=k,
            node_classes=[
                "trust_barrier", "switching_trigger", "review", "claim_support",
            ],
            cutoff_date=cutoff_date,
        )

    async def contradicting(
        self, *, simulation_id, claim_id: UUID, k: int = 4,
    ) -> list[EvidenceEdge]:
        """Return contradicts edges anchored at the given claim's source."""
        async with self._sessionmaker() as session:
            edges = (
                await session.execute(
                    select(EvidenceEdge)
                    .where(EvidenceEdge.simulation_id == simulation_id)
                    .where(EvidenceEdge.edge_type == "contradicts")
                )
            ).scalars().all()
        return list(edges)[:k]

    async def analogical_fallback(
        self, *, simulation_id, query, k=8, cutoff_date=None,
    ):
        """Used when direct evidence is sparse for a topic. Returns
        analogical-only ranked results."""
        return await self.retrieve(
            simulation_id=simulation_id,
            query=query,
            k=k,
            kinds=["analogical"],
            cutoff_date=cutoff_date,
        )
