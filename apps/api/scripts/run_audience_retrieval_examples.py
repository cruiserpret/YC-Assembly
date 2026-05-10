"""Phase 8.2H — operator-only audience-retrieval runner.

For each of the four fixture briefs:
  1. build the TargetSocietyPlan (Phase 8.2G)
  2. load every existing PersonaRecord + traits + evidence_links from the DB
  3. enrich evidence-link source-record IDs with their domain
  4. run `retrieve_personas_for_target_society`
  5. print the operator summary
  6. dump the full result to JSON under apps/api/_audit/

NO live Tavily call. NO new persona writes. NO graph / clusters /
simulation rows. Read-only DB access plus pure-function pipeline.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


async def _amain() -> int:
    from assembly.db import get_sessionmaker
    from assembly.models.persona import (
        PersonaEvidenceLink,
        PersonaRecord,
        PersonaTrait,
        SourceRecord,
    )
    from assembly.pipeline.audience_retrieval import (
        render_audience_retrieval_summary,
        render_operator_report,
        retrieve_personas_for_target_society,
    )
    from assembly.pipeline.persona_relevance.auditor import (
        EvidenceLinkView,
        PersonaAuditInput,
        TraitView,
    )
    from assembly.pipeline.target_society import (
        ALL_EXAMPLES,
        build_target_society_plan,
    )

    sessionmaker = get_sessionmaker()
    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Pre-load every persona + traits + links + source domains ONCE.
    # ------------------------------------------------------------------
    async with sessionmaker() as session:
        all_personas = (
            await session.execute(select(PersonaRecord))
        ).scalars().all()
        all_traits = (
            await session.execute(select(PersonaTrait))
        ).scalars().all()
        all_links = (
            await session.execute(select(PersonaEvidenceLink))
        ).scalars().all()
        all_sources = (
            await session.execute(
                select(SourceRecord.id, SourceRecord.source_url)
            )
        ).all()

    domain_map = {sid: _domain_of(url) for sid, url in all_sources}

    traits_by_persona: dict = {}
    for t in all_traits:
        traits_by_persona.setdefault(t.persona_id, []).append(t)
    links_by_persona: dict = {}
    for l in all_links:
        links_by_persona.setdefault(l.persona_id, []).append(l)
    likely_signal_by_record_id: dict = {}
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord.id, SourceRecord.metadata_)
            )
        ).all()
    for rid, md in rows:
        v = (md or {}).get("likely_human_signal_candidate")
        likely_signal_by_record_id[rid] = (
            None if v is None else bool(v)
        )

    audit_inputs: list[PersonaAuditInput] = []
    for p in all_personas:
        ts = traits_by_persona.get(p.id, [])
        ls = links_by_persona.get(p.id, [])
        audit_inputs.append(PersonaAuditInput(
            persona_id=p.id,
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
                    source_likely_human_signal=likely_signal_by_record_id.get(
                        l.source_record_id
                    ),
                )
                for l in ls
            ),
        ))

    print(f"Total existing personas in DB: {len(audit_inputs)}")
    print()

    # ------------------------------------------------------------------
    # Run each example brief through the full pipeline.
    # ------------------------------------------------------------------
    for key, brief in ALL_EXAMPLES:
        plan = build_target_society_plan(brief)
        result = retrieve_personas_for_target_society(
            brief=brief,
            plan=plan,
            personas=audit_inputs,
            domain_by_record_id=domain_map,
        )
        print()
        print(render_audience_retrieval_summary(result))
        print()
        print(f"operator_report({key}): {render_operator_report(result)}")
        out_path = out_dir / f"audience_retrieval_{key}.json"
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"→ JSON dumped to: {out_path}")
        print()
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
