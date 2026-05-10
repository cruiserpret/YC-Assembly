"""Phase 8.2F.7 — operator-only persona relevance audit runner.

Loads every persona that has at least one persona_evidence_link
pointing to an operator-ingested Tavily source_record, builds the
in-memory PersonaAuditInput list, scores each via the deterministic
auditor, and prints the human-readable report.

NO DB writes. NO LLM calls. NO network calls. NO mutation.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select


async def _amain() -> int:
    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.pipeline.persona_relevance import (
        EvidenceLinkView,
        PersonaAuditInput,
        TraitView,
        audit_personas,
        format_audit_report,
    )

    sessionmaker = get_sessionmaker()

    # 1) Find every persona that has at least one evidence_link to an
    # operator-ingested Tavily source_record. That's the Phase 8.2F
    # pilot cohort.
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(PersonaEvidenceLink)
                .join(SourceRecord, SourceRecord.id == PersonaEvidenceLink.source_record_id)
                .where(SourceRecord.source_kind == "tavily_search_extract")
                .where(SourceRecord.metadata_["operator_run"].astext == "true")
            )
        ).scalars().all()
        persona_ids: list[UUID] = sorted({pl.persona_id for pl in rows})
        print(f"Persona ids to audit (operator-tavily-bound): {len(persona_ids)}")

        if not persona_ids:
            print("No personas to audit. Exiting.")
            return 1

        # 2) Pull persona rows + traits + links + linked source metadata.
        personas = (
            await session.execute(
                select(PersonaRecord).where(PersonaRecord.id.in_(persona_ids))
            )
        ).scalars().all()
        persona_by_id = {p.id: p for p in personas}

        traits = (
            await session.execute(
                select(PersonaTrait).where(PersonaTrait.persona_id.in_(persona_ids))
            )
        ).scalars().all()
        traits_by_persona: dict[UUID, list[PersonaTrait]] = {}
        for t in traits:
            traits_by_persona.setdefault(t.persona_id, []).append(t)

        links = (
            await session.execute(
                select(PersonaEvidenceLink).where(
                    PersonaEvidenceLink.persona_id.in_(persona_ids)
                )
            )
        ).scalars().all()
        # Linked source metadata for the human-signal score.
        linked_source_ids = sorted({pl.source_record_id for pl in links})
        sources = (
            await session.execute(
                select(SourceRecord).where(SourceRecord.id.in_(linked_source_ids))
            )
        ).scalars().all()
        source_signal_by_id: dict[UUID, bool | None] = {
            s.id: (
                bool(s.metadata_.get("likely_human_signal_candidate"))
                if (s.metadata_ or {}).get("likely_human_signal_candidate") is not None
                else None
            )
            for s in sources
        }

        links_by_persona: dict[UUID, list[PersonaEvidenceLink]] = {}
        for ln in links:
            links_by_persona.setdefault(ln.persona_id, []).append(ln)

    # 3) Build PersonaAuditInput list.
    audit_inputs: list[PersonaAuditInput] = []
    for pid in persona_ids:
        p = persona_by_id[pid]
        ts = traits_by_persona.get(pid, [])
        ls = links_by_persona.get(pid, [])
        audit_inputs.append(PersonaAuditInput(
            persona_id=pid,
            display_name=p.display_name,
            traits=tuple(
                TraitView(
                    field_name=t.field_name,
                    support_level=t.support_level,
                    value=t.value,
                    confidence=float(t.confidence),
                    source_ids=tuple(t.source_ids or ()),
                    rationale=t.rationale,
                )
                for t in ts
            ),
            evidence_links=tuple(
                EvidenceLinkView(
                    persona_id=l.persona_id,
                    source_record_id=l.source_record_id,
                    contribution_kind=l.contribution_kind,
                    contribution_field=l.contribution_field,
                    excerpt=l.excerpt or "",
                    source_likely_human_signal=source_signal_by_id.get(l.source_record_id),
                )
                for l in ls
            ),
        ))

    # 4) Run the audit.
    result = audit_personas(audit_inputs)

    # 5) Print report.
    text = format_audit_report(result, top_n=5, weak_n=5)
    print(text)

    # 6) Optional JSON dump for archival under apps/api/_audit/.
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / "persona_relevance_audit_phase_8_2f_7.json"
    payload = {
        "personas_audited": result.personas_audited,
        "classification_counts": {
            k.value: v for k, v in result.classification_counts.items()
        },
        "average_scores": result.average_scores,
        "matched_categories": {
            k.value: v for k, v in result.matched_categories.items()
        },
        "missing_categories": [c.value for c in result.missing_categories],
        "duplicate_fingerprints": dict(result.duplicate_fingerprints),
        "per_persona": [
            {
                "persona_id": str(s.persona_id),
                "display_name": s.display_name,
                "total_score": s.total_score,
                "classification": s.classification.value,
                "matched_categories": [c.value for c in s.matched_stakeholder_categories],
                **{f: getattr(s, f) for f in [
                    "role_context_score", "pain_point_score",
                    "current_alternative_score", "price_budget_score",
                    "trust_objection_score", "source_strength_score",
                    "human_signal_score", "viewpoint_diversity_score",
                    "simulation_usefulness_score",
                ]},
                "rationale": list(s.rationale),
                "matched_keyword_counts": s.matched_keyword_counts,
            }
            for s in sorted(result.per_persona, key=lambda x: x.total_score, reverse=True)
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nFull audit JSON written to: {json_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
