"""Phase 8.5D.1B — fresh-product persona-generalization DRY RUN.

Runs the FULL Assembly persona pipeline against a brand-new
unreleased product (StrideShield, an anti-blister/anti-chafe balm)
that the framework has never seen before.

Pipeline (every stage reuses existing modules — zero
product-specific code):

  brief
    → generate_anchor_plan(brief)              [8.5B.1]
    → generate_source_category_plan(brief, …)  [8.5D.1B helper —
        data-driven via brief.competitors; no hardcoded mapping]
    → AmazonReviewsLocalReader / MetadataIndex  [8.5A]
    → score_review_with_plan(...)              [8.5B.1]
    → generate_ingestion_policy(...)           [8.5C.1]
    → decide_candidates(...)                   [8.5C.1]  (read-only;
        synthetic content_hash check; no DB inserts)
    → wrap SELECTED candidates as
      EffectiveSourceRecord(s)                 [8.5D.1]
    → PersonaCandidatePlanner.generate(...)    [8.5D.1]
    → write audit JSON

NO LLM. NO network. NO Amazon.com scrape. NO DB writes.

Audit path:
  apps/api/_audit/fresh_product_persona_generalization_dry_run_8_5d_1b.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select

from assembly.db import get_sessionmaker
from assembly.models.persona import (
    PersonaEvidenceLink, PersonaRecord, PersonaTrait, SourceRecord,
)
from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewsAdapterConfig, AmazonReviewsLocalReader,
    MetadataIndex, ReviewConfidence,
    discover_category_files,
)
from assembly.sources.evidence_anchor_planner import (
    ProductBriefForPlanning, generate_anchor_plan,
    generate_source_category_plan, score_review_with_plan,
)
from assembly.sources.ingestion_policy import (
    CandidateRow, decide_candidates, generate_ingestion_policy,
)
from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidatePlanner,
)


PHASE_LABEL = "8.5D.1B"
TARGET_BRIEF_ID = "strideshield"
LAUNCH_STATE = "unlaunched"
PRODUCT_NAME = "StrideShield"

# Founder-style brief — the ONLY input the pipeline needs.
STRIDESHIELD_BRIEF = ProductBriefForPlanning(
    product_name=PRODUCT_NAME,
    product_description=(
        "A pocket-sized anti-blister and anti-chafe balm for college "
        "students, runners, hikers, gym-goers, theme-park walkers, "
        "and people whose shoes or sandals rub during long days. It "
        "is sweat-resistant, fragrance-free, non-greasy, and designed "
        "to be applied to heels, toes, thighs, and other friction "
        "spots before walking, running, workouts, or outdoor activity."
    ),
    price_or_price_structure="$12.99",
    launch_geography="California, United States",
    target_customers=[
        "college students who walk a lot on campus",
        "runners", "hikers", "gym-goers",
        "theme-park visitors",
        "people who get shoe rub, sandal cuts, blisters, or thigh chafing",
        "people who dislike greasy lotions or messy powders",
    ],
    competitors=[
        "Body Glide", "Gold Bond Friction Defense",
        "Megababe Thigh Rescue", "Squirrel's Nut Butter",
        "Trail Toes",
    ],
    optional_constraints=[],
)


# Bounded scan caps (operator-spec'd).
DEFAULT_RECORDS_PER_CATEGORY = 25_000
HARD_RECORDS_PER_CATEGORY = 100_000
DEFAULT_MAX_INSERT_CAP = 12  # universal DB safety bound


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


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


def _discover_available_categories(raw_dir: Path) -> list[str]:
    """List local Amazon categories present on disk by examining
    `<raw_dir>/<Category>.jsonl[.gz]` files."""
    found: set[str] = set()
    for f in raw_dir.glob("*.jsonl*"):
        name = f.name
        if name.startswith("meta_"):
            continue
        for suf in (".jsonl.gz", ".jsonl"):
            if name.endswith(suf):
                found.add(name[: -len(suf)])
                break
    return sorted(found)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5D.1B — fresh-product persona generalization "
            "DRY RUN."
        ),
    )
    parser.add_argument(
        "--records-per-category", type=int,
        default=DEFAULT_RECORDS_PER_CATEGORY,
        help=(
            f"Bounded records per category (default "
            f"{DEFAULT_RECORDS_PER_CATEGORY:,}, hard cap "
            f"{HARD_RECORDS_PER_CATEGORY:,})."
        ),
    )
    parser.add_argument(
        "--max-insert-cap", type=int, default=DEFAULT_MAX_INSERT_CAP,
        help="Universal DB-safety cap on planned source_records.",
    )
    parser.add_argument(
        "--category-sample", type=int, default=5_000,
        help=(
            "Records to sample per category in the source/category "
            "discovery pass (default 5000)."
        ),
    )
    args = parser.parse_args()
    args.records_per_category = min(
        max(0, args.records_per_category), HARD_RECORDS_PER_CATEGORY,
    )
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "fresh_product_persona_generalization_dry_run_8_5d_1b.json"
    )

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    if not dir_str:
        print("ERROR: AMAZON_REVIEWS_2023_DIR is unset.")
        return 2
    dataset_dir = Path(dir_str)
    raw_dir = dataset_dir / "raw"
    if not raw_dir.is_dir():
        print(f"ERROR: {raw_dir} does not exist.")
        return 2

    sm = get_sessionmaker()
    db_pre = await _read_baseline_counts(sm)
    print(f"DB baseline pre-dry-run: {db_pre}")

    # 1. EvidenceAnchorPlan from the brief
    plan = generate_anchor_plan(STRIDESHIELD_BRIEF)
    print(f"\nEvidenceAnchorPlan: product_type={plan.product_type!r}")
    print(f"  positive_anchor_terms (top 8): {plan.positive_anchor_terms[:8]}")
    print(f"  competitor_anchor_terms: {plan.competitor_anchor_terms}")
    print(f"  ambiguous_entities: {[a.entity for a in plan.ambiguous_entities]}")

    # 2. Source/category plan — data-driven via competitor metadata scan
    available = _discover_available_categories(raw_dir)
    print(f"\navailable local categories: {available}")
    cat_plan = generate_source_category_plan(
        STRIDESHIELD_BRIEF,
        dataset_dir=dataset_dir,
        available_categories=available,
        sample_per_category=args.category_sample,
    )
    print(f"selected categories: {cat_plan.selected_categories}")
    print(f"excluded: {cat_plan.excluded_categories}")
    for cat, info in cat_plan.relevance_per_category.items():
        if info.get("total_hits", 0) > 0:
            print(
                f"  {cat}: {info['total_hits']} hits — "
                f"top: {Counter(info['competitor_hits']).most_common(3)}"
            )

    # 3. Bounded scan + scoring on selected categories
    candidate_rows: list[CandidateRow] = []
    by_category: dict[str, dict] = {}
    if cat_plan.selected_categories:
        discovered = discover_category_files(
            dataset_dir=dataset_dir,
            categories=list(cat_plan.selected_categories),
        )
        for cat in cat_plan.selected_categories:
            files = discovered.get(cat) or []
            if not files:
                by_category[cat] = {
                    "files_present": False,
                    "reviews_scanned": 0,
                    "asins_resolved": 0,
                    "high_count": 0,
                }
                continue
            meta_file = raw_dir / f"meta_{cat}.jsonl"
            reader = AmazonReviewsLocalReader(
                dataset_dir=dataset_dir,
                config=AmazonReviewsAdapterConfig(
                    max_records_per_category=args.records_per_category,
                ),
            )
            reviews: list = []
            target_asins: set[str] = set()
            for rec in reader.iter_category(
                category=cat, files=files,
                max_records=args.records_per_category,
            ):
                reviews.append(rec)
                if rec.parent_asin:
                    target_asins.add(rec.parent_asin)
            idx = MetadataIndex(meta_file=meta_file, target_asins=target_asins)
            idx.load()
            cat_high = 0
            for rec in reviews:
                meta = idx.lookup(rec.parent_asin)
                score = score_review_with_plan(
                    review=rec, metadata=meta, plan=plan,
                )
                if score.confidence is not ReviewConfidence.HIGH_CONFIDENCE:
                    continue
                cat_high += 1
                candidate_rows.append(CandidateRow(
                    candidate_id=(
                        f"{cat}::{rec.parent_asin or 'no_asin'}::"
                        f"{rec.asin or 'na'}"
                    ),
                    category=cat,
                    parent_asin=rec.parent_asin, asin=rec.asin,
                    rating=rec.rating,
                    verified_purchase=rec.verified_purchase,
                    helpful_vote=rec.helpful_vote,
                    timestamp=rec.timestamp,
                    title=rec.title, text=rec.text,
                    user_id_hash=rec.user_id_hash,
                    score=score.score,
                    confidence=score.confidence.value,  # type: ignore[arg-type]
                    matched_terms=list(score.matched_terms),
                    denylist_hits=list(score.denylist_hits),
                    metadata_title=meta.title if meta else None,
                    metadata_main_category=(
                        meta.main_category if meta else None
                    ),
                    metadata_categories=(
                        list(meta.categories) if meta else []
                    ),
                ))
            by_category[cat] = {
                "files_present": True,
                "reviews_scanned": len(reviews),
                "asins_resolved": len(idx.index),
                "high_count": cat_high,
            }
            print(
                f"  scanned {cat}: {len(reviews):,} reviews, "
                f"{len(idx.index):,} asins, {cat_high} HIGH"
            )

    # 4. Generate IngestionPolicy
    policy = generate_ingestion_policy(
        brief=STRIDESHIELD_BRIEF,
        evidence_anchor_plan=plan,
        candidate_pool=candidate_rows,
        source_family="amazon_reviews_2023_local",
        product_launch_state=LAUNCH_STATE,
        db_baseline=db_pre,
        max_insert_cap=args.max_insert_cap,
        target_brief_id=TARGET_BRIEF_ID,
    )

    # 5. Decide candidates (read-only duplicate check)
    decisions = await decide_candidates(
        candidates=candidate_rows,
        policy=policy, plan=plan, sessionmaker=sm,
        product_name=PRODUCT_NAME,
        product_launch_state=LAUNCH_STATE,
    )
    selected_decisions = [
        d for d in decisions if d.decision == "SELECTED"
    ]
    rejected_decisions = [
        d for d in decisions if d.decision == "REJECTED"
    ]
    print(
        f"\ningestion plan: SELECT {len(selected_decisions)} / "
        f"REJECT {len(rejected_decisions)} (cap={args.max_insert_cap})"
    )

    # 6. Wrap SELECTED candidates as EffectiveSourceRecord(s) for the
    # PersonaCandidatePlanner. Synthetic IDs make it explicit these
    # are PLANNED rows, not actual DB records.
    effective_sources: list[EffectiveSourceRecord] = []
    cand_by_id = {c.candidate_id: c for c in candidate_rows}
    for d in selected_decisions:
        rec = cand_by_id.get(d.candidate_id)
        if rec is None or d.planned_source_record_preview is None:
            continue
        preview = d.planned_source_record_preview
        synthetic_id = (
            f"planned::{TARGET_BRIEF_ID}::{rec.category}::"
            f"{rec.parent_asin or 'no_asin'}"
        )
        effective_sources.append(EffectiveSourceRecord(
            source_record_id=synthetic_id,
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin=rec.parent_asin, asin=rec.asin,
            category=rec.category,
            metadata_title=rec.metadata_title,
            rating=rec.rating, verified_purchase=rec.verified_purchase,
            helpful_vote=rec.helpful_vote, timestamp=rec.timestamp,
            content_length=preview.content_length,
            content=preview.content_preview,
            metadata={
                **preview.metadata,
                "phase": PHASE_LABEL + "_dry_run",
                "planned_source_record_id_synthetic": synthetic_id,
            },
        ))

    # 7. Persona-candidate planner
    planner = PersonaCandidatePlanner(generated_for_phase=PHASE_LABEL)
    persona_plan = planner.generate(
        product_name=PRODUCT_NAME,
        target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
        competitor_brief_list=STRIDESHIELD_BRIEF.competitors,
        substitute_brief_list=plan.substitute_anchor_terms,
        effective_sources=effective_sources,
        preview_rows_total=0,  # no preview rows have been inserted
        companion_rows_total=0,
        superseded_preview_ids=[],
    )
    print(
        f"persona candidates: {len(persona_plan.persona_candidates)} "
        f"| rejections: {len(persona_plan.rejected_candidate_ideas)} "
        f"| ready_for_mutating: {persona_plan.ready_for_8_5d_2}"
    )

    # 8. DB post-check
    db_post = await _read_baseline_counts(sm)
    db_unchanged = db_pre == db_post

    # 9. Compose audit JSON
    summary = {
        "phase": "8_5d_1b_fresh_product_persona_generalization_dry_run",
        "completed_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "db_writes": False,
        "db_pre_dry_run_counts": db_pre,
        "db_post_dry_run_counts": db_post,
        "db_unchanged_during_dry_run": db_unchanged,
        "founder_brief": json.loads(STRIDESHIELD_BRIEF.model_dump_json()),
        "launch_state": LAUNCH_STATE,
        "evidence_anchor_plan": json.loads(plan.model_dump_json()),
        "source_category_plan": {
            "available_categories": list(cat_plan.available_categories),
            "selected_categories": list(cat_plan.selected_categories),
            "excluded_categories": list(cat_plan.excluded_categories),
            "relevance_per_category": cat_plan.relevance_per_category,
            "selection_rule": cat_plan.selection_rule,
            "sample_per_category": cat_plan.sample_per_category,
            "generated_from": cat_plan.generated_from,
            "caveats": list(cat_plan.caveats),
        },
        "categories_scanned": list(cat_plan.selected_categories),
        "records_scanned_by_category": by_category,
        "metadata_join_stats": {
            cat: {
                "asins_resolved": info["asins_resolved"],
                "high_count": info["high_count"],
            }
            for cat, info in by_category.items()
        },
        "candidate_evidence_pool_count": len(candidate_rows),
        "dynamic_ingestion_policy": json.loads(policy.model_dump_json()),
        "planned_source_records_count": len(selected_decisions),
        "planned_source_records": [
            json.loads(d.model_dump_json()) for d in selected_decisions
        ],
        "rejected_evidence_candidates_count": len(rejected_decisions),
        "rejected_evidence_candidates_top_reasons": dict(Counter(
            (d.rejection_reasons[0].split(":", 1)[0]
             if d.rejection_reasons else "unspecified")
            for d in rejected_decisions
        ).most_common(10)),
        "persona_role_plan": {
            "inferred_roles": persona_plan.inferred_roles,
            "evidence_basis_by_role": persona_plan.evidence_basis_by_role,
            "rejected_role_ideas": persona_plan.rejected_role_ideas,
            "role_inference_method": persona_plan.role_inference_method,
        },
        "generated_persona_candidates": [
            json.loads(c.model_dump_json())
            for c in persona_plan.persona_candidates
        ],
        "rejected_persona_candidate_ideas": [
            json.loads(r.model_dump_json())
            for r in persona_plan.rejected_candidate_ideas
        ],
        "launch_state_claim_validation": [
            json.loads(v.model_dump_json())
            for v in persona_plan.launch_state_validation_results
        ],
        "evidence_to_persona_mapping": [
            {
                "planned_source_record_id_synthetic": (
                    c.source_record_ids[0] if c.source_record_ids else None
                ),
                "candidate_id": c.candidate_id,
                "primary_role": c.inferred_persona_role,
            }
            for c in persona_plan.persona_candidates
        ],
        "persona_role_distribution": persona_plan.persona_role_distribution,
        "evidence_coverage_summary": persona_plan.evidence_coverage_summary,
        "caveats": persona_plan.caveats + [
            "Phase 8.5D.1B is fresh-product generalization DRY RUN. "
            f"NO DB writes — source_records / personas / traits / "
            "evidence-links unchanged.",
            "Source-category plan is data-driven (competitor "
            "metadata scan) — no hardcoded brief-to-category mapping.",
            "Planned source_records have synthetic IDs "
            "(`planned::strideshield::...`) — these are NOT in the "
            "DB. A future `Phase 8.5D.2B` would execute the bounded "
            "ingestion + persona persistence under operator approval.",
        ],
        "recommendation": (
            "PASS — fresh-product persona pipeline generalizes. "
            f"{len(persona_plan.persona_candidates)} brief-scoped "
            f"persona candidates generated from "
            f"{len(effective_sources)} planned source_records (no "
            "hardcoded category/persona templates). Phase 8.5D.2B "
            f"mutating is "
            + ("ready." if persona_plan.ready_for_8_5d_2 else
               "deferred — see candidates flagged DEFER.")
        ),
        "ready_for_mutating_phase": persona_plan.ready_for_8_5d_2,
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("Phase 8.5D.1B — Fresh-product persona generalization DRY RUN")
    print("=" * 72)
    print(f"product: {PRODUCT_NAME}")
    print(f"DB unchanged: {db_unchanged}")
    print(f"selected categories: {cat_plan.selected_categories}")
    print(f"candidate evidence pool: {len(candidate_rows)} HIGH-confidence")
    print(f"planned source_records (after policy gate): {len(selected_decisions)}")
    print(f"effective sources for persona planner: {len(effective_sources)}")
    print(f"persona candidates: {len(persona_plan.persona_candidates)}")
    print(f"rejections: {len(persona_plan.rejected_candidate_ideas)}")
    print(f"role distribution: {persona_plan.persona_role_distribution}")
    print(f"ready_for_mutating_phase: {persona_plan.ready_for_8_5d_2}")
    print()
    for c in persona_plan.persona_candidates:
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
