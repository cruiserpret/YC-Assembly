"""Phase 8.5D.1C — fresh-product persona-diversity + source-coverage
fix DRY RUN.

Same StrideShield brief as 8.5D.1B. Same pipeline architecture. Two
universal additions:

  1. Raised bounded scan caps:
       - default metadata sample per category: 25,000 (was 5,000)
       - default reviews per category: 50,000 (was 25,000)
       - hard cap reviews per category: 100,000

  2. Two new universal stages:
       - `apply_diversity_aware_reranking` (8.5D.1C ingestion-policy
         post-processor) — swaps cap-rejected fresh-role candidates
         into the SELECTED set when they would add a fresh role.
         Quality gates never relaxed.
       - `evaluate_persona_diversity` (8.5D.1C evaluator) — refuses
         to mark `ready_for_mutating_phase=true` when all candidates
         collapse to one role.

NO LLM. NO network. NO Amazon.com scrape. NO DB writes.

Audit path:
  apps/api/_audit/fresh_product_persona_diversity_fix_8_5d_1c.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
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
    CandidateRow, apply_diversity_aware_reranking,
    decide_candidates, generate_ingestion_policy,
)
from assembly.sources.persona_diversity_evaluator import (
    evaluate_persona_diversity,
)
from assembly.sources.persona_role_planner import (
    EffectiveSourceRecord, PersonaCandidatePlanner,
)


PHASE_LABEL = "8.5D.1C"
TARGET_BRIEF_ID = "strideshield"
LAUNCH_STATE = "unlaunched"
PRODUCT_NAME = "StrideShield"

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
        "college students who walk a lot on campus", "runners",
        "hikers", "gym-goers", "theme-park visitors",
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


# 8.5D.1C raised caps (still bounded; hard cap unchanged)
DEFAULT_RECORDS_PER_CATEGORY = 50_000
HARD_RECORDS_PER_CATEGORY = 100_000
DEFAULT_CATEGORY_SAMPLE = 25_000
HARD_CATEGORY_SAMPLE = 100_000
DEFAULT_MAX_INSERT_CAP = 12


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
            "Phase 8.5D.1C — fresh-product persona-diversity + "
            "source-coverage fix DRY RUN."
        ),
    )
    parser.add_argument(
        "--records-per-category", type=int,
        default=DEFAULT_RECORDS_PER_CATEGORY,
    )
    parser.add_argument(
        "--max-insert-cap", type=int, default=DEFAULT_MAX_INSERT_CAP,
    )
    parser.add_argument(
        "--category-sample", type=int, default=DEFAULT_CATEGORY_SAMPLE,
    )
    args = parser.parse_args()
    args.records_per_category = min(
        max(0, args.records_per_category), HARD_RECORDS_PER_CATEGORY,
    )
    args.category_sample = min(
        max(0, args.category_sample), HARD_CATEGORY_SAMPLE,
    )
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "fresh_product_persona_diversity_fix_8_5d_1c.json"
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

    # 1. EvidenceAnchorPlan
    plan = generate_anchor_plan(STRIDESHIELD_BRIEF)

    # 2. Source/category plan (raised metadata sample)
    available = _discover_available_categories(raw_dir)
    cat_plan = generate_source_category_plan(
        STRIDESHIELD_BRIEF,
        dataset_dir=dataset_dir,
        available_categories=available,
        sample_per_category=args.category_sample,
    )
    print(
        f"category plan: selected={cat_plan.selected_categories} "
        f"(sampled {args.category_sample} metadata records each)"
    )

    # 3. Bounded review scan
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
                    "reviews_scanned": 0, "asins_resolved": 0,
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
                f"  scanned {cat}: {len(reviews):,} reviews → "
                f"{cat_high} HIGH"
            )

    # Evidence-theme distribution: bucket HIGH candidates by their
    # primary competitor mention.
    theme_dist: Counter = Counter()
    for cr in candidate_rows:
        for m in cr.matched_terms:
            if (
                m.startswith("competitor:")
                and "(wrong-context)" not in m
            ):
                theme_dist[m] += 1
                break
        else:
            theme_dist["no_competitor"] += 1

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

    # 5. Decide candidates
    decisions = await decide_candidates(
        candidates=candidate_rows,
        policy=policy, plan=plan, sessionmaker=sm,
        product_name=PRODUCT_NAME,
        product_launch_state=LAUNCH_STATE,
    )

    # 6. NEW: diversity-aware reranking
    decisions_after_rerank, swap_log = apply_diversity_aware_reranking(
        decisions, target_min_unique_roles=4,
    )
    selected = [d for d in decisions_after_rerank if d.decision == "SELECTED"]
    rejected = [d for d in decisions_after_rerank if d.decision == "REJECTED"]
    print(
        f"\ningestion plan: SELECT {len(selected)} / "
        f"REJECT {len(rejected)} | swaps applied: {len(swap_log)}"
    )

    # 7. Wrap as EffectiveSourceRecord(s)
    effective_sources: list[EffectiveSourceRecord] = []
    for d in selected:
        if d.planned_source_record_preview is None:
            continue
        preview = d.planned_source_record_preview
        cid = d.candidate_id
        # Look up the original CandidateRow by candidate_id
        cand_row = next(
            (c for c in candidate_rows if c.candidate_id == cid),
            None,
        )
        if cand_row is None:
            continue
        synthetic_id = (
            f"planned::{TARGET_BRIEF_ID}::{cand_row.category}::"
            f"{cand_row.parent_asin or 'no_asin'}"
        )
        effective_sources.append(EffectiveSourceRecord(
            source_record_id=synthetic_id,
            effective_kind="preview_used_as_is",
            superseded_preview_source_record_id=None,
            parent_asin=cand_row.parent_asin, asin=cand_row.asin,
            category=cand_row.category,
            metadata_title=cand_row.metadata_title,
            rating=cand_row.rating,
            verified_purchase=cand_row.verified_purchase,
            helpful_vote=cand_row.helpful_vote,
            timestamp=cand_row.timestamp,
            content_length=preview.content_length,
            content=preview.content_preview,
            metadata={
                **preview.metadata,
                "phase": PHASE_LABEL + "_dry_run",
                "planned_source_record_id_synthetic": synthetic_id,
            },
        ))

    # 8. Persona-candidate planner
    persona_planner = PersonaCandidatePlanner(generated_for_phase=PHASE_LABEL)
    persona_plan = persona_planner.generate(
        product_name=PRODUCT_NAME, target_brief_id=TARGET_BRIEF_ID,
        launch_state=LAUNCH_STATE,
        competitor_brief_list=STRIDESHIELD_BRIEF.competitors,
        substitute_brief_list=plan.substitute_anchor_terms,
        effective_sources=effective_sources,
        preview_rows_total=0, companion_rows_total=0,
        superseded_preview_ids=[],
    )

    # 9. NEW: persona-diversity evaluation
    diversity_eval = evaluate_persona_diversity(
        brief=STRIDESHIELD_BRIEF,
        candidates=persona_plan.persona_candidates, plan=plan,
    )
    print(
        f"persona candidates: {len(persona_plan.persona_candidates)} | "
        f"unique_primary_roles: {len(diversity_eval.unique_primary_roles)} | "
        f"diversity_score: {diversity_eval.diversity_score} | "
        f"recommendation: {diversity_eval.mutating_persistence_recommendation}"
    )

    # 10. DB post-check
    db_post = await _read_baseline_counts(sm)
    db_unchanged = db_pre == db_post

    ready_for_mutating = (
        diversity_eval.mutating_persistence_recommendation == "READY"
        and persona_plan.ready_for_8_5d_2
        and db_unchanged
    )

    # 11. Compose audit JSON
    summary = {
        "phase": "8_5d_1c_fresh_product_persona_diversity_fix",
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
        "evidence_theme_distribution": dict(theme_dist),
        "dynamic_ingestion_policy": json.loads(policy.model_dump_json()),
        "diversity_aware_selection_policy": {
            "target_min_unique_roles": 4,
            "swaps_applied": swap_log,
            "swap_count": len(swap_log),
            "rule": (
                "promote cap-rejected candidates with fresh role keys "
                "into the SELECTED set when an over-represented "
                "cluster of >=2 same-role candidates exists. NEVER "
                "relax PII / fake-buyer / dataset-compliance / "
                "duplicate / strong-anchor / high-confidence gates."
            ),
        },
        "planned_source_records": [
            json.loads(d.model_dump_json()) for d in selected
        ],
        "rejected_evidence_candidates": [
            json.loads(d.model_dump_json()) for d in rejected[:50]
        ],
        "rejected_for_diversity_reasons": [
            json.loads(d.model_dump_json())
            for d in rejected
            if any(
                "diversity_rerank_demoted" in r
                for r in (d.rejection_reasons or [])
            )
        ],
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
        "persona_diversity_evaluation": json.loads(
            diversity_eval.model_dump_json()
        ),
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
            "Phase 8.5D.1C is a fresh-product DRY RUN with diversity "
            "fixes. NO DB writes. The diversity reranker NEVER relaxes "
            "quality gates — it only swaps cap-rejected candidates with "
            "fresh roles into the SELECTED set.",
            "ready_for_mutating_phase requires BOTH the persona "
            "planner's structural readiness AND the diversity "
            "evaluator's READY recommendation. A same-role cluster "
            "is never persisted as a society.",
        ],
        "recommendation": (
            f"PASS — diversity reranker applied {len(swap_log)} swap(s); "
            f"persona-diversity evaluator: "
            f"{diversity_eval.mutating_persistence_recommendation}. "
            f"Mutating-phase ready: {ready_for_mutating}."
        ),
        "ready_for_mutating_phase": ready_for_mutating,
    }
    out_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("Phase 8.5D.1C — Fresh-product persona DIVERSITY FIX DRY RUN")
    print("=" * 72)
    print(f"product: {PRODUCT_NAME}")
    print(f"DB unchanged: {db_unchanged}")
    print(f"selected categories: {cat_plan.selected_categories}")
    print(f"candidate evidence pool: {len(candidate_rows)} HIGH")
    print(f"ingestion plan: SELECT {len(selected)} / REJECT {len(rejected)}")
    print(f"diversity swaps: {len(swap_log)}")
    print(f"persona candidates: {len(persona_plan.persona_candidates)}")
    print(
        f"diversity: score={diversity_eval.diversity_score}, "
        f"unique_roles={len(diversity_eval.unique_primary_roles)}, "
        f"competitor_concentration={diversity_eval.competitor_concentration}"
    )
    print(
        f"recommendation: "
        f"{diversity_eval.mutating_persistence_recommendation}"
    )
    print(f"ready_for_mutating_phase: {ready_for_mutating}")
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
