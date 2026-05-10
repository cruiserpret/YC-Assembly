"""Phase 8.5E — load run-scoped persisted personas + their traits +
evidence links + linked source records by `run_scope_id`.

Universal: matches PersonaRecord rows whose
`product_relevance_tags` ARRAY contains the supplied
`run_scope_id:<id>` tag. Never hardcoded by brief or product.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)


@dataclass(frozen=True)
class RunScopedAgentContext:
    """Everything the simulation engine needs to run one persona as
    an agent. Pure-data shape — no DB session held."""

    persona_id: UUID
    display_name: str
    segment_label: str
    product_relevance_tags: list[str]
    # Tag-derived metadata (parsed from product_relevance_tags)
    target_brief: str
    product_name: str
    launch_state: str
    run_scope_id: str
    normalized_primary_role: str
    evidence_theme: str
    source_provider_family: str
    compressed_candidate_id: str
    not_global_persona: bool

    traits: list[dict[str, Any]] = field(default_factory=list)
    evidence_links: list[dict[str, Any]] = field(default_factory=list)
    source_records: list[dict[str, Any]] = field(default_factory=list)

    def evidence_excerpts(self, *, max_excerpts: int = 6) -> list[str]:
        """Return up to `max_excerpts` evidence excerpts (≤300 chars each)."""
        seen: set[str] = set()
        out: list[str] = []
        for link in self.evidence_links:
            ex = (link.get("excerpt") or "").strip()
            if not ex:
                continue
            key = ex[:100]
            if key in seen:
                continue
            seen.add(key)
            out.append(ex[:300])
            if len(out) >= max_excerpts:
                break
        return out


def _parse_tag_value(
    tags: list[str], key: str, default: str = "",
) -> str:
    """Parse `key:value` out of a `product_relevance_tags` ARRAY."""
    prefix = f"{key}:"
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):]
    return default


async def load_run_scoped_agents(
    *,
    session: AsyncSession,
    run_scope_id: str,
) -> list[RunScopedAgentContext]:
    """Load every PersonaRecord whose product_relevance_tags include
    `run_scope_id:<id>`, plus all their traits + evidence_links +
    linked source_records.

    Pure read — never mutates anything. Same `run_scope_id` →
    deterministic ordering by persona_id.
    """
    tag = f"run_scope_id:{run_scope_id}"
    # PostgreSQL `ARRAY @> ARRAY[...]` — "tags array contains [tag]"
    persona_rows = (await session.execute(
        select(PersonaRecord)
        .where(PersonaRecord.product_relevance_tags.contains([tag]))
        .order_by(PersonaRecord.id)
    )).scalars().all()

    if not persona_rows:
        return []

    persona_ids = [p.id for p in persona_rows]

    trait_rows = (await session.execute(
        select(PersonaTrait)
        .where(PersonaTrait.persona_id.in_(persona_ids))
        .order_by(PersonaTrait.persona_id, PersonaTrait.field_name)
    )).scalars().all()

    link_rows = (await session.execute(
        select(PersonaEvidenceLink)
        .where(PersonaEvidenceLink.persona_id.in_(persona_ids))
        .order_by(
            PersonaEvidenceLink.persona_id,
            PersonaEvidenceLink.contribution_field,
        )
    )).scalars().all()

    # Collect every source_record_id referenced by either evidence
    # links or trait `source_ids` ARRAYs.
    source_ids: set[UUID] = set()
    for l in link_rows:
        source_ids.add(l.source_record_id)
    for t in trait_rows:
        for sid in t.source_ids or []:
            source_ids.add(sid)

    source_rows = []
    if source_ids:
        source_rows = (await session.execute(
            select(SourceRecord)
            .where(SourceRecord.id.in_(source_ids))
        )).scalars().all()

    sources_by_id: dict[UUID, dict[str, Any]] = {}
    for s in source_rows:
        sources_by_id[s.id] = {
            "source_record_id": str(s.id),
            "source_kind": s.source_kind,
            "source_url": s.source_url,
            "language": s.language,
            "metadata": dict(s.metadata_ or {}),
            "compliance_tag": s.compliance_tag,
            "captured_at": (
                s.captured_at.isoformat() if s.captured_at else None
            ),
            "content": s.content[:1000],  # cap for prompt safety
        }

    traits_by_persona: dict[UUID, list[dict[str, Any]]] = {}
    for t in trait_rows:
        traits_by_persona.setdefault(t.persona_id, []).append({
            "trait_id": str(t.id),
            "field_name": t.field_name,
            "value": t.value,
            "support_level": t.support_level,
            "confidence": float(t.confidence),
            "rationale": t.rationale,
            "source_ids": [str(sid) for sid in (t.source_ids or [])],
        })

    links_by_persona: dict[UUID, list[dict[str, Any]]] = {}
    for l in link_rows:
        links_by_persona.setdefault(l.persona_id, []).append({
            "link_id": str(l.id),
            "source_record_id": str(l.source_record_id),
            "contribution_kind": l.contribution_kind,
            "contribution_field": l.contribution_field,
            "excerpt": l.excerpt,
            "confidence": float(l.confidence),
        })

    agents: list[RunScopedAgentContext] = []
    for p in persona_rows:
        tags = list(p.product_relevance_tags or [])
        # Build the source_records list for THIS persona only — i.e.
        # the unique set referenced by its links + traits.
        ref_sids: set[UUID] = set()
        for l in links_by_persona.get(p.id, []):
            ref_sids.add(UUID(l["source_record_id"]))
        for t in traits_by_persona.get(p.id, []):
            for sid in t.get("source_ids") or []:
                ref_sids.add(UUID(sid))
        agents.append(RunScopedAgentContext(
            persona_id=p.id,
            display_name=p.display_name,
            segment_label=p.segment_label or "",
            product_relevance_tags=tags,
            target_brief=_parse_tag_value(tags, "target_brief"),
            product_name=_parse_tag_value(tags, "product_name"),
            launch_state=_parse_tag_value(tags, "launch_state"),
            run_scope_id=_parse_tag_value(tags, "run_scope_id"),
            normalized_primary_role=_parse_tag_value(
                tags, "normalized_primary_role",
            ),
            evidence_theme=_parse_tag_value(tags, "evidence_theme"),
            source_provider_family=_parse_tag_value(
                tags, "source_provider_family",
            ),
            compressed_candidate_id=_parse_tag_value(
                tags, "compressed_candidate_id",
            ),
            not_global_persona=(
                _parse_tag_value(tags, "not_global_persona") == "true"
            ),
            traits=list(traits_by_persona.get(p.id, [])),
            evidence_links=list(links_by_persona.get(p.id, [])),
            source_records=[
                sources_by_id[sid] for sid in ref_sids
                if sid in sources_by_id
            ],
        ))
    return agents
