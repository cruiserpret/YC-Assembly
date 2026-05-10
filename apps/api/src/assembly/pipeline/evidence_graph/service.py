"""Phase 6.75 — Phase-7-friendly helper service over the evidence graph.

Per Correction 5, Phase 7 must NOT manually traverse raw graph tables. It
calls these stable methods, gets back ready-to-render data structures with
ranked evidence + missing-evidence + claim traceability bundled together.

Internal complexity (graph topology, ranking weights, edge-type semantics)
stays inside `EvidenceRetriever`; this service is the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.embeddings.provider import EmbeddingProvider
from assembly.models.claim import Claim
from assembly.models.evidence import EvidenceItem
from assembly.pipeline.evidence_graph.retriever import (
    EvidenceRetriever,
    RankedEvidence,
    RetrievalResult,
)


@dataclass
class EvidenceBundle:
    """What every Phase 7 helper returns: ranked direct + analogical
    evidence on top, missing-evidence list separate, structured for
    direct rendering by the aggregator."""

    ranked: list[RankedEvidence] = field(default_factory=list)
    missing: list[EvidenceItem] = field(default_factory=list)


@dataclass
class MissingEvidenceSummary:
    by_node_class: dict[str, list[EvidenceItem]]
    total: int


@dataclass
class ClaimTraceability:
    claim: Claim
    source_evidence: EvidenceItem | None  # None only if FK was somehow nulled


class EvidenceGraphService:
    """Phase 7's interface over the graph. Stable, simple, opinionated.

    Every method is a thin wrapper over `EvidenceRetriever` plus an
    appropriate node-class filter; the retriever's full ranking applies."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._retriever = EvidenceRetriever(
            sessionmaker=sessionmaker, embedding_provider=embedding_provider,
        )

    @staticmethod
    def _bundle(result: RetrievalResult) -> EvidenceBundle:
        return EvidenceBundle(ranked=result.ranked, missing=result.missing)

    async def get_competitor_evidence(
        self, simulation_id: UUID, *, k: int = 12, cutoff_date: date | None = None,
    ) -> EvidenceBundle:
        return self._bundle(
            await self._retriever.for_competitor(
                simulation_id=simulation_id,
                competitor_name="",
                k=k,
                cutoff_date=cutoff_date,
            )
        )

    async def get_pricing_evidence(
        self, simulation_id: UUID, *, k: int = 12, cutoff_date: date | None = None,
    ) -> EvidenceBundle:
        return self._bundle(
            await self._retriever.for_price_sensitivity(
                simulation_id=simulation_id, k=k, cutoff_date=cutoff_date,
            )
        )

    async def get_trust_barrier_evidence(
        self, simulation_id: UUID, *, k: int = 12, cutoff_date: date | None = None,
    ) -> EvidenceBundle:
        return self._bundle(
            await self._retriever.for_market_acceptance_requirement(
                simulation_id=simulation_id, k=k, cutoff_date=cutoff_date,
            )
        )

    async def get_positioning_evidence(
        self, simulation_id: UUID, *, k: int = 12, cutoff_date: date | None = None,
    ) -> EvidenceBundle:
        return self._bundle(
            await self._retriever.for_positioning(
                simulation_id=simulation_id, k=k, cutoff_date=cutoff_date,
            )
        )

    async def get_market_acceptance_evidence(
        self, simulation_id: UUID, *, k: int = 12, cutoff_date: date | None = None,
    ) -> EvidenceBundle:
        # Same retrieval lane as trust_barrier but exposed as its own
        # method so Phase 7 can request it semantically without knowing
        # the retriever-internal node-class mapping.
        return await self.get_trust_barrier_evidence(
            simulation_id, k=k, cutoff_date=cutoff_date,
        )

    async def get_missing_evidence_summary(
        self, simulation_id: UUID,
    ) -> MissingEvidenceSummary:
        async with self._sessionmaker() as session:
            items = (
                await session.execute(
                    select(EvidenceItem)
                    .where(EvidenceItem.simulation_id == simulation_id)
                    .where(EvidenceItem.kind == "missing")
                )
            ).scalars().all()
        bucket: dict[str, list[EvidenceItem]] = {}
        for it in items:
            bucket.setdefault(it.node_class, []).append(it)
        return MissingEvidenceSummary(by_node_class=bucket, total=len(items))

    async def get_evidence_by_ids(
        self, simulation_id: UUID, evidence_ids: list[UUID],
    ) -> dict[UUID, EvidenceItem]:
        """Phase 8 — report-safe lookup of specific evidence rows by id.

        The frontend report page needs hydrated metadata for every UUID
        referenced in the persisted simulation_outputs (anchors,
        simulation_references, missing entries, claim sources). This
        method is the only path the report endpoint should use to fetch
        those rows; the API layer should never query `evidence_items`
        directly.

        Filters by `simulation_id` so a malformed report can't surface
        evidence from a different simulation.
        """
        if not evidence_ids:
            return {}
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(EvidenceItem)
                    .where(EvidenceItem.simulation_id == simulation_id)
                    .where(EvidenceItem.id.in_(evidence_ids))
                )
            ).scalars().all()
        return {row.id: row for row in rows}

    async def get_claim_traceability(
        self, simulation_id: UUID,
    ) -> list[ClaimTraceability]:
        """Return every claim in the simulation paired with its bound
        evidence_items row. Phase 7 renders this as the report's
        "evidence ledger" + claim → source links."""
        async with self._sessionmaker() as session:
            claims = (
                await session.execute(
                    select(Claim).where(Claim.simulation_id == simulation_id)
                )
            ).scalars().all()
            evidence_ids = list({c.source_evidence_id for c in claims})
            if not evidence_ids:
                return []
            rows = (
                await session.execute(
                    select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
                )
            ).scalars().all()
            evidence_by_id = {e.id: e for e in rows}

        out: list[ClaimTraceability] = []
        for c in claims:
            out.append(
                ClaimTraceability(
                    claim=c,
                    source_evidence=evidence_by_id.get(c.source_evidence_id),
                )
            )
        return out
