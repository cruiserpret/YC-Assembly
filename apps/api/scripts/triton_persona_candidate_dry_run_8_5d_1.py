"""Phase 8.5D.1 — Triton persona-candidate DRY RUN.

Reads the 8.5C.2 preview source_records + the 8.5C.4 full-text
companion source_records from the live DB (READ-ONLY), reads the
Phase 8.5C.3 audit JSON for sufficiency labels, performs lineage-
aware source selection, runs the deterministic persona-candidate
planner, and writes the audit JSON.

NO DB writes. NO LLM. NO external retrieval. NO Amazon.com scrape.

Drift-tested:
  * Script imports `assembly.db.get_sessionmaker` only for read.
  * Zero ORM-row construction (verified by drift tests).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.persona_role_planner import (
    PersonaCandidatePlanner, PersonaRolePlan, ProductLaunchState,
    select_effective_sources,
)


PHASE_LABEL = "8.5D.1"
TRITON_PRODUCT_NAME = "Triton Drinks"
TRITON_TARGET_BRIEF_ID = "triton_drinks"
TRITON_LAUNCH_STATE: ProductLaunchState = "unlaunched"

# Founder-style brief input — explicit fields only.
TRITON_COMPETITORS = [
    "Red Bull", "Monster", "Celsius", "Prime", "Gatorade",
]
# Substitutes are inferred from the founder brief's text + source
# evidence; we still pass the brief-derived substitute list (from
# the 8.5B.1 anchor plan, which itself derived these from the
# brief's "Substitutes considered in scope:" sentence).
TRITON_SUBSTITUTES = [
    "cold brew", "coffee", "pre-workout powders", "electrolyte drinks",
    "pre workout", "preworkout",
]

PREVIEW_INGESTED_BY = (
    "assembly_phase_8_5c_triton_amazon_dynamic_policy_bounded_ingest"
)
COMPANION_INGESTED_BY = (
    "assembly_phase_8_5c4_triton_amazon_fulltext_companion_ingest"
)


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


async def _read_source_rows_by_tag(sessionmaker, tag: str) -> list[dict]:
    """Read-only fetch of SourceRecord rows by ingested_by tag.
    Returns plain dicts so we don't keep ORM objects beyond session
    scope."""
    async with sessionmaker() as session:
        rows = (await session.execute(
            select(SourceRecord)
            .where(SourceRecord.ingested_by == tag)
            .order_by(SourceRecord.created_at.asc())
        )).scalars().all()
        return [
            {
                "id": str(r.id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "source_kind": r.source_kind,
                "source_url": r.source_url,
                "content": r.content,
                "metadata": dict(r.metadata_ or {}),
            }
            for r in rows
        ]


async def _read_baseline_counts(sessionmaker) -> dict[str, int]:
    async with sessionmaker() as session:
        sr = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    return {
        "source_records": int(sr), "persona_records": int(pr),
        "persona_traits": int(pt), "persona_evidence_links": int(pel),
    }


def _load_sufficiency_labels_from_8_5c_3(audit_path: Path) -> dict[str, str]:
    """Read the 8.5C.3 audit JSON and return {source_record_id:
    sufficiency_label}."""
    if not audit_path.is_file():
        return {}
    d = json.loads(audit_path.read_text(encoding="utf-8"))
    return {
        r["source_record_id"]: r["sufficiency_label"]
        for r in d.get("per_record_audit", [])
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5D.1 — Triton persona-candidate DRY RUN."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "triton_persona_candidate_dry_run_8_5d_1.json"
    audit_8_5c_3_path = audit_root / (
        "triton_amazon_source_record_content_integrity_8_5c_3.json"
    )

    sm = get_sessionmaker()

    # 1. Live DB baseline (pre-audit)
    db_pre = await _read_baseline_counts(sm)
    print(f"DB baseline pre-audit: {db_pre}")

    # 2. Read source_records
    preview_rows = await _read_source_rows_by_tag(sm, PREVIEW_INGESTED_BY)
    companion_rows = await _read_source_rows_by_tag(sm, COMPANION_INGESTED_BY)
    print(
        f"preview rows (8.5C.2): {len(preview_rows)} | "
        f"companion rows (8.5C.4): {len(companion_rows)}"
    )

    # 3. Read sufficiency labels from 8.5C.3 audit
    sufficiency_labels = _load_sufficiency_labels_from_8_5c_3(
        audit_8_5c_3_path,
    )
    if not sufficiency_labels:
        print(
            f"WARN: 8.5C.3 audit missing at {audit_8_5c_3_path}; "
            "falling back to default 'SUFFICIENT_AS_IS' for all preview rows."
        )

    # 4. Lineage-aware source selection
    effective_sources, superseded_ids, included_ids = select_effective_sources(
        preview_rows=preview_rows,
        companion_rows=companion_rows,
        sufficiency_labels_by_id=(
            sufficiency_labels or {
                r["id"]: "SUFFICIENT_AS_IS" for r in preview_rows
            }
        ),
    )
    print(
        f"effective sources: {len(effective_sources)} "
        f"(superseded: {len(superseded_ids)})"
    )

    # 5. Run the persona-candidate planner
    planner = PersonaCandidatePlanner(generated_for_phase=PHASE_LABEL)
    plan: PersonaRolePlan = planner.generate(
        product_name=TRITON_PRODUCT_NAME,
        target_brief_id=TRITON_TARGET_BRIEF_ID,
        launch_state=TRITON_LAUNCH_STATE,
        competitor_brief_list=TRITON_COMPETITORS,
        substitute_brief_list=TRITON_SUBSTITUTES,
        effective_sources=effective_sources,
        preview_rows_total=len(preview_rows),
        companion_rows_total=len(companion_rows),
        superseded_preview_ids=superseded_ids,
    )
    print(
        f"persona candidates generated: {len(plan.persona_candidates)} | "
        f"rejections: {len(plan.rejected_candidate_ideas)} | "
        f"ready_for_8_5d_2: {plan.ready_for_8_5d_2}"
    )

    # 6. Live DB baseline (post-audit) — must equal pre
    db_post = await _read_baseline_counts(sm)
    db_unchanged = db_pre == db_post

    # 7. Write audit JSON
    summary = {
        "phase": "8_5d_1_triton_persona_candidate_dry_run",
        "completed_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "db_writes": False,
        "db_pre_audit_counts": db_pre,
        "db_post_audit_counts": db_post,
        "db_unchanged_during_dry_run": db_unchanged,
        "founder_brief": {
            "product_name": TRITON_PRODUCT_NAME,
            "target_brief_id": TRITON_TARGET_BRIEF_ID,
            "launch_state": TRITON_LAUNCH_STATE,
            "competitors": TRITON_COMPETITORS,
            "substitutes_inferred": TRITON_SUBSTITUTES,
        },
        "source_selection": {
            "preview_rows_found": len(preview_rows),
            "companion_rows_found": len(companion_rows),
            "superseded_preview_rows_excluded": superseded_ids,
            "effective_source_records_count": len(effective_sources),
            "effective_source_record_ids": included_ids,
            "effective_kinds": [
                {
                    "source_record_id": s.source_record_id,
                    "effective_kind": s.effective_kind,
                    "supersedes_preview": s.superseded_preview_source_record_id,
                }
                for s in effective_sources
            ],
        },
        "dynamic_persona_role_plan": {
            "inferred_roles": plan.inferred_roles,
            "evidence_basis_by_role": plan.evidence_basis_by_role,
            "rejected_roles": plan.rejected_role_ideas,
            "role_generation_method": plan.role_inference_method,
        },
        "generated_persona_candidates": [
            json.loads(c.model_dump_json())
            for c in plan.persona_candidates
        ],
        "rejected_persona_candidate_ideas": [
            json.loads(r.model_dump_json())
            for r in plan.rejected_candidate_ideas
        ],
        "source_to_persona_mapping": [
            {
                "source_record_id": (
                    c.source_record_ids[0] if c.source_record_ids else None
                ),
                "candidate_id": c.candidate_id,
                "primary_role": c.inferred_persona_role,
            }
            for c in plan.persona_candidates
        ],
        "persona_role_distribution": plan.persona_role_distribution,
        "evidence_coverage_summary": plan.evidence_coverage_summary,
        "launch_state_claim_validation": [
            json.loads(v.model_dump_json())
            for v in plan.launch_state_validation_results
        ],
        "plan_id": plan.plan_id,
        "caveats": plan.caveats,
        "recommendation": plan.recommendation,
        "ready_for_8_5d_2": plan.ready_for_8_5d_2,
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    # Operator-facing summary
    print("\n" + "=" * 72)
    print("Phase 8.5D.1 — Triton persona-candidate DRY RUN")
    print("=" * 72)
    print(f"DB unchanged: {db_unchanged}")
    print(f"effective sources: {len(effective_sources)}")
    print(f"superseded preview rows: {len(superseded_ids)}")
    print(f"inferred roles: {len(plan.inferred_roles)}")
    print(f"persona candidates: {len(plan.persona_candidates)}")
    print(f"rejections: {len(plan.rejected_candidate_ideas)}")
    print(f"role distribution: {plan.persona_role_distribution}")
    print(f"ready_for_8_5d_2: {plan.ready_for_8_5d_2}")
    print()
    for c in plan.persona_candidates:
        print(
            f"  [{c.confidence:6s}] {c.candidate_id} "
            f"role={c.inferred_persona_role} "
            f"traits={len(c.inferred_traits)} "
            f"persistence={c.persistence_recommendation}"
        )
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
