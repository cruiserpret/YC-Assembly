"""Phase 6.75 — typed edge construction.

Two passes:
  1. Deterministic edges — derived from explicit data (same source_url
     means competes_with / priced_against; user-input competitor mapping;
     dedup similar_to is already produced in `dedup.py`).
  2. LLM-typed edges (optional) — supports / contradicts / causes_objection /
     reduces_objection / maps_to_*. Goes through `cost_guarded_chat`.
     Per Correction 4, all LLM-emitted edges carry `basis='inferred'`,
     `provenance.derived_by='graph_edge_builder'`, and `low_confidence=true`
     when below threshold.

Anti-hallucination: every edge target must reference a real `evidence_items`
row in the same simulation. Orphan edges are rejected before insert.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.llm.guarded_chat import cost_guarded_chat
from assembly.llm.provider import LLMMessage, LLMProvider
from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EDGE_TYPES, EvidenceEdge
from assembly.models.simulation import Simulation

logger = logging.getLogger(__name__)


# Edges with inferred basis below this confidence are flagged in provenance.
INFERRED_LOW_CONFIDENCE_THRESHOLD: Decimal = Decimal("0.7")


# ---------------------------------------------------------------------------
# Cutoff helper (Correction 3)
# ---------------------------------------------------------------------------


def _captured_at_eligible(item: EvidenceItem, cutoff: date | None) -> bool:
    """Per Correction 3, when cutoff_date is set, only allow `captured_at IS
    NULL` for user_input / kind=missing rows OR rows with explicit snapshot
    metadata. Retrieved web evidence with NULL captured_at is excluded."""
    if cutoff is None:
        return True
    if item.captured_at is not None:
        return item.captured_at.date() <= cutoff
    # captured_at IS NULL — only some sources are allowed:
    if item.source_type == "user_input":
        return True
    if item.kind == "missing":
        return True
    meta = item.metadata_ or {}
    if meta.get("snapshot_path"):
        return True
    if meta.get("snapshot_captured_at"):
        return True
    if meta.get("basis") == "assumption":
        return True
    return False


# ---------------------------------------------------------------------------
# Deterministic edge derivation
# ---------------------------------------------------------------------------


def _competitor_key(item: EvidenceItem) -> str | None:
    """Return a stable key for grouping competitor-related items."""
    if item.node_class == "competitor":
        if item.source_url:
            return item.source_url
        meta = item.metadata_ or {}
        if meta.get("competitor_name"):
            return f"name::{meta['competitor_name']}"
    return None


async def derive_deterministic_edges(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    cutoff_date: date | None,
) -> int:
    """Walk evidence_items and emit edges that follow deterministically:
      - competitor_page rows with the same source_url → competes_with
      - pricing_page row + a competitor row with the same source_url → priced_against
      - kind='missing' + user_input mapping the same field → maps_to_*
    Returns the count of edges newly inserted."""
    inserted = 0
    async with sessionmaker() as session:
        async with session.begin():
            items = (
                await session.execute(
                    select(EvidenceItem)
                    .where(EvidenceItem.simulation_id == simulation_id)
                )
            ).scalars().all()

            items = [i for i in items if _captured_at_eligible(i, cutoff_date)]

            # Group competitors by stable key.
            competitor_groups: dict[str, list[EvidenceItem]] = {}
            pricing_by_url: dict[str, list[EvidenceItem]] = {}
            for it in items:
                key = _competitor_key(it)
                if key is not None:
                    competitor_groups.setdefault(key, []).append(it)
                if it.node_class == "pricing" and it.source_url:
                    pricing_by_url.setdefault(it.source_url, []).append(it)

            # competes_with: any two distinct competitor items in the same
            # group (same competitor URL) get an edge between them. We
            # deduplicate by ordering the pair so the unique constraint
            # holds on re-runs.
            for group in competitor_groups.values():
                if len(group) < 2:
                    continue
                ordered = sorted(group, key=lambda x: str(x.id))
                for i in range(len(ordered) - 1):
                    for j in range(i + 1, len(ordered)):
                        if ordered[i].id == ordered[j].id:
                            continue
                        edge = EvidenceEdge(
                            simulation_id=simulation_id,
                            source_evidence_id=ordered[i].id,
                            target_evidence_id=ordered[j].id,
                            edge_type="competes_with",
                            strength=Decimal("1.0"),
                            confidence=Decimal("1.0"),
                            basis="direct",
                            provenance={
                                "derived_by": "edge_builder",
                                "rule": "same_competitor_key",
                            },
                        )
                        session.add(edge)
                        inserted += 1

            # priced_against: link pricing row → its competitor on same URL.
            for url, prices in pricing_by_url.items():
                comp_group = competitor_groups.get(url, [])
                for price in prices:
                    for comp in comp_group:
                        if price.id == comp.id:
                            continue
                        edge = EvidenceEdge(
                            simulation_id=simulation_id,
                            source_evidence_id=price.id,
                            target_evidence_id=comp.id,
                            edge_type="priced_against",
                            strength=Decimal("1.0"),
                            confidence=Decimal("1.0"),
                            basis="direct",
                            provenance={
                                "derived_by": "edge_builder",
                                "rule": "same_url",
                            },
                        )
                        session.add(edge)
                        inserted += 1

            # maps_to_competitor: user_input competitor descriptions → the
            # competitor evidence rows themselves. Lightweight and keyed
            # by name match in metadata.
            user_competitor_inputs = [
                i for i in items
                if i.source_type == "user_input"
                and (i.metadata_ or {}).get("input_field") == "competitors"
            ]
            for ui in user_competitor_inputs:
                # Naive: link to every competitor row in the same simulation.
                # The retriever can dedup on the user-input side; we err on
                # the side of more edges (each carries strength=1, basis=direct).
                for group in competitor_groups.values():
                    for comp in group:
                        if comp.id == ui.id:
                            continue
                        edge = EvidenceEdge(
                            simulation_id=simulation_id,
                            source_evidence_id=ui.id,
                            target_evidence_id=comp.id,
                            edge_type="maps_to_competitor",
                            strength=Decimal("0.8"),
                            confidence=Decimal("0.9"),
                            basis="direct",
                            provenance={
                                "derived_by": "edge_builder",
                                "rule": "user_input_competitor_field",
                            },
                        )
                        session.add(edge)
                        inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# LLM-typed edges
# ---------------------------------------------------------------------------


class _InferredEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    target_id: str
    edge_type: str
    strength: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None


class _EdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edges: list[_InferredEdge]


_EDGE_PROMPT = """You are deriving typed RELATIONSHIPS between evidence atoms in a market-reaction simulation.

CLOSED edge_type set:
{edge_types}

Hard rules:
- Only emit edges between evidence_ids in the supplied evidence list.
- Do NOT invent evidence rows. Do NOT use ids that are not in the list.
- Do NOT emit edges that the supplied data cannot support — if unclear, omit.
- All edges you emit will be marked as INFERRED. They assist retrieval/ranking,
  but they cannot back final-report claims by themselves.
- For 'contradicts' edges, require strong textual evidence on both sides.
- Confidence reflects YOUR certainty in the relationship; values < 0.5 will
  be suppressed entirely.

Forbidden:
- No invented competitors, pricing numbers, reviews, or quotes.
- No build/kill/pivot recommendations.
- No objective sentiment ("the market is X").
- No numeric forecasts.

Return ONLY JSON: {{"edges": [{{"source_id": "...", "target_id": "...", "edge_type": "...", "strength": 0..1, "confidence": 0..1, "rationale": "short"}}]}}"""


async def derive_inferred_edges(
    *,
    sessionmaker: async_sessionmaker,
    simulation_id: UUID,
    provider: LLMProvider,
    model: str,
    cutoff_date: date | None,
    stage: str = "graph_edge_builder",
    max_items_in_prompt: int = 30,
) -> int:
    """LLM-typed edge derivation. Skipped silently when fewer than 2 items
    pass the cutoff filter (nothing to relate). Returns the count of edges
    inserted (which may be smaller than the count emitted, since orphan or
    invalid edges are rejected)."""
    async with sessionmaker() as session:
        items = (
            await session.execute(
                select(EvidenceItem)
                .where(EvidenceItem.simulation_id == simulation_id)
                .where(EvidenceItem.kind != "missing")
                .order_by(EvidenceItem.created_at.asc())
            )
        ).scalars().all()

    eligible = [i for i in items if _captured_at_eligible(i, cutoff_date)]
    if len(eligible) < 2:
        return 0

    capped = eligible[:max_items_in_prompt]
    payload = [
        {
            "evidence_id": str(i.id),
            "kind": i.kind,
            "source_type": i.source_type,
            "node_class": i.node_class,
            "source_url": i.source_url,
            "content": (i.content or "")[:300],
        }
        for i in capped
    ]

    system = _EDGE_PROMPT.format(edge_types=", ".join(EDGE_TYPES))
    user = (
        "Derive INFERRED edges between the supplied evidence atoms.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\nReturn ONLY JSON."
    )
    response = await cost_guarded_chat(
        sessionmaker=sessionmaker,
        simulation_id=simulation_id,
        stage=stage,
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ],
        provider=provider,
        model=model,
        max_tokens=8192,
        temperature=0.1,
    )

    try:
        cleaned = response.text.strip()
        if cleaned.startswith("```"):
            nl = cleaned.find("\n")
            if nl != -1:
                cleaned = cleaned[nl + 1 :]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        parsed = _EdgeResponse.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("graph_edge_builder.parse_failed err=%s", e)
        return 0

    valid_ids = {str(i.id) for i in capped}
    inserted = 0
    async with sessionmaker() as session:
        async with session.begin():
            for ed in parsed.edges:
                if ed.edge_type not in EDGE_TYPES:
                    logger.warning(
                        "graph_edge_builder.invalid_type type=%s simulation=%s",
                        ed.edge_type, simulation_id,
                    )
                    continue
                if ed.source_id == ed.target_id:
                    continue
                if ed.source_id not in valid_ids or ed.target_id not in valid_ids:
                    # Anti-hallucination: orphan target.
                    logger.warning(
                        "graph_edge_builder.orphan_id source=%s target=%s simulation=%s",
                        ed.source_id, ed.target_id, simulation_id,
                    )
                    continue
                if ed.confidence < 0.5:
                    # Below the suppression threshold — skip entirely.
                    continue
                conf = Decimal(str(ed.confidence))
                strength = Decimal(str(ed.strength))
                provenance: dict = {
                    "derived_by": "graph_edge_builder",
                    "source_evidence_ids": [ed.source_id, ed.target_id],
                }
                if ed.rationale:
                    provenance["rationale"] = ed.rationale[:500]
                if conf < INFERRED_LOW_CONFIDENCE_THRESHOLD:
                    provenance["low_confidence"] = True
                edge = EvidenceEdge(
                    simulation_id=simulation_id,
                    source_evidence_id=UUID(ed.source_id),
                    target_evidence_id=UUID(ed.target_id),
                    edge_type=ed.edge_type,
                    strength=strength,
                    confidence=conf,
                    basis="inferred",
                    provenance=provenance,
                )
                try:
                    session.add(edge)
                    await session.flush()
                    inserted += 1
                except Exception as e:  # IntegrityError on duplicate
                    logger.info(
                        "graph_edge_builder.skip_dup type=%s err=%s",
                        ed.edge_type, type(e).__name__,
                    )
                    await session.rollback()
                    # Re-open transaction; gather more edges.
                    await session.begin()

    return inserted


def get_simulation_cutoff(simulation: Simulation) -> date | None:
    """Helper extractor — keeps the cutoff-date plumbing in one place."""
    return getattr(simulation, "evidence_cutoff_date", None)
