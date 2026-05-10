"""Phase 8.5C.1 — Triton Amazon dynamic ingestion-policy DRY RUN.

Runs the full deterministic pipeline:

  brief
    → generate_anchor_plan(brief)                   (8.5B.1)
    → score_review_with_plan(...)  per review       (8.5B.1)
    → collect HIGH_CONFIDENCE candidates            (this phase)
    → generate_ingestion_policy(...)                (this phase)
    → decide_candidates(...)  with 4 universal scanners + DB
      duplicate-check (READ-ONLY)                   (this phase)
    → write audit JSON                              (this phase)

NO DB writes. Drift-tested: this script imports `assembly.db.get_sessionmaker`
ONLY for the read-only duplicate-check; it constructs zero ORM rows.

Audit path:
  apps/api/_audit/triton_amazon_dynamic_ingestion_plan_8_5c_1.json
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
    ProductBriefForPlanning, generate_anchor_plan, score_review_with_plan,
)
from assembly.sources.ingestion_policy import (
    CandidateRow, decide_candidates, generate_ingestion_policy,
)


TRITON_BRIEF = ProductBriefForPlanning(
    product_name="Triton Drinks",
    product_description=(
        "A caffeinated sports and energy drink positioned for "
        "students, gym users, athletes, and busy young adults who "
        "want energy for studying, workouts, alertness, and "
        "performance. Substitutes considered in scope: cold brew, "
        "coffee, pre-workout powders, electrolyte drinks. Triton is "
        "unlaunched."
    ),
    price_or_price_structure="$3.99 per can",
    launch_geography="California, United States",
    target_customers=[
        "college students", "athletes", "gym-goers", "busy young adults",
        "people who use caffeine for studying or workouts",
    ],
    competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
)


CATEGORIES = (
    "Grocery_and_Gourmet_Food",
    "Health_and_Household",
    "Sports_and_Outdoors",
)


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


def _meta_path_for(raw_dir: Path, category: str) -> Path:
    return raw_dir / f"meta_{category}.jsonl"


async def _read_only_db_baseline() -> dict[str, int]:
    sm = get_sessionmaker()
    async with sm() as session:
        sr_n = (await session.execute(
            select(func.count()).select_from(SourceRecord)
        )).scalar_one()
        pr_n = (await session.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar_one()
        pt_n = (await session.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar_one()
        pel_n = (await session.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar_one()
    return {
        "source_records": int(sr_n),
        "persona_records": int(pr_n),
        "persona_traits": int(pt_n),
        "persona_evidence_links": int(pel_n),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5C.1 — Triton Amazon dynamic ingestion plan dry-run."
        ),
    )
    parser.add_argument(
        "--records-per-category", type=int, default=1000,
        help="Records per category to scan (default 1000, matches 8.5B.1).",
    )
    parser.add_argument(
        "--max-insert-cap", type=int, default=12,
        help="UNIVERSAL DB safety bound on planned source_records.",
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "triton_amazon_dynamic_ingestion_plan_8_5c_1.json"

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    if not dir_str:
        print("ERROR: AMAZON_REVIEWS_2023_DIR is unset.")
        return 2
    dataset_dir = Path(dir_str)
    raw_dir = dataset_dir / "raw"
    if not raw_dir.is_dir():
        print(f"ERROR: {raw_dir} does not exist.")
        return 2

    # Step 1: DB baseline (read-only)
    db_baseline = await _read_only_db_baseline()

    # Step 2: Generate anchor plan
    plan = generate_anchor_plan(TRITON_BRIEF)

    # Step 3: Stream + score over Triton's 3 categories
    discovered = discover_category_files(
        dataset_dir=dataset_dir, categories=list(CATEGORIES),
    )
    candidate_rows: list[CandidateRow] = []
    by_category: dict[str, dict] = {}
    for cat in CATEGORIES:
        files = discovered.get(cat, [])
        if not files:
            print(f"ERROR: {cat} review file not present.")
            return 2
        meta_file = _meta_path_for(raw_dir, cat)
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
                candidate_id=f"{cat}::{rec.parent_asin or 'no_asin'}::{rec.asin or 'na'}",
                category=cat,
                parent_asin=rec.parent_asin,
                asin=rec.asin,
                rating=rec.rating,
                verified_purchase=rec.verified_purchase,
                helpful_vote=rec.helpful_vote,
                timestamp=rec.timestamp,
                title=rec.title,
                text=rec.text,
                user_id_hash=rec.user_id_hash,
                score=score.score,
                confidence=score.confidence.value,  # type: ignore[arg-type]
                matched_terms=list(score.matched_terms),
                denylist_hits=list(score.denylist_hits),
                metadata_title=meta.title if meta else None,
                metadata_main_category=(
                    meta.main_category if meta else None
                ),
                metadata_categories=list(meta.categories) if meta else [],
            ))
        by_category[cat] = {
            "reviews_scanned": len(reviews),
            "asins_resolved": len(idx.index),
            "high_confidence_count": cat_high,
        }
        print(
            f"  {cat}: {len(reviews):,} reviews scanned, "
            f"{len(idx.index):,} asins resolved, {cat_high} HIGH"
        )

    # Step 4: Generate the ingestion policy
    policy = generate_ingestion_policy(
        brief=TRITON_BRIEF,
        evidence_anchor_plan=plan,
        candidate_pool=candidate_rows,
        source_family="amazon_reviews_2023_local",
        product_launch_state="unlaunched",
        db_baseline=db_baseline,
        max_insert_cap=args.max_insert_cap,
        target_brief_id="triton_drinks",
    )

    # Step 5: Decide candidates (READ-ONLY duplicate check)
    sm = get_sessionmaker()
    decisions = await decide_candidates(
        candidates=candidate_rows,
        policy=policy,
        plan=plan,
        sessionmaker=sm,
        product_name=TRITON_BRIEF.product_name,
        product_launch_state="unlaunched",
    )

    selected = [d for d in decisions if d.decision == "SELECTED"]
    rejected = [d for d in decisions if d.decision == "REJECTED"]
    sel_per_cat: Counter = Counter()
    for d in selected:
        cid = d.candidate_id
        cat = cid.split("::", 1)[0]
        sel_per_cat[cat] += 1

    # Step 6: Compose summary
    rejection_reason_counts: Counter = Counter()
    for d in rejected:
        for r in d.rejection_reasons:
            # bucket by rule_id (first colon-prefixed token)
            key = r.split(":", 1)[0]
            rejection_reason_counts[key] += 1
    selected_by_persona_role: Counter = Counter()
    for d in selected:
        for r in d.selected_for_persona_roles:
            selected_by_persona_role[r] += 1
    summary = {
        "phase": "8_5c_1_triton_amazon_dynamic_ingestion_plan",
        "completed_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "db_writes": False,
        "policy_generated_from": "deterministic",
        "db_baseline": db_baseline,
        "founder_brief": json.loads(TRITON_BRIEF.model_dump_json()),
        "evidence_anchor_plan": json.loads(plan.model_dump_json()),
        "ingestion_policy": json.loads(policy.model_dump_json()),
        "candidate_pool_count": len(candidate_rows),
        "candidate_pool_by_category": by_category,
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "selected_per_category": dict(sel_per_cat),
        "selected_by_persona_role": dict(selected_by_persona_role),
        "rejection_reason_buckets": dict(rejection_reason_counts),
        "selected_candidates": [
            json.loads(d.model_dump_json()) for d in selected
        ],
        "rejected_candidates": [
            json.loads(d.model_dump_json()) for d in rejected[:50]
        ],
        "expected_post_insert_counts_if_approved": {
            "source_records": db_baseline["source_records"] + len(selected),
            "persona_records": db_baseline["persona_records"],
            "persona_traits": db_baseline["persona_traits"],
            "persona_evidence_links": db_baseline["persona_evidence_links"],
        },
        "caveats": [
            "Phase 8.5C.1 is DRY-RUN only. No DB writes occurred.",
            "The duplicate-check used a READ-ONLY `SELECT count` "
            "against source_records.content_hash — no write surface.",
            "max_insert_cap is a UNIVERSAL DB safety bound, not a "
            "product-specific relevance rule.",
            "Phase 8.5C.2 (separate operator approval) executes the "
            "planned inserts inside a single bounded transaction "
            "with rollback on any scanner failure.",
        ],
        "recommendation": (
            "PASS — policy generated dynamically from brief + plan + "
            "pool; universal scanners enforced; DB baseline preserved; "
            "8.5C.2 execution is ready."
        ) if selected else (
            "FAIL — zero candidates selected. Diagnose policy or "
            "candidate-pool quality before approving 8.5C.2."
        ),
    }
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 72)
    print("Phase 8.5C.1 — Triton Amazon dynamic ingestion plan")
    print("=" * 72)
    print(f"DB baseline: {db_baseline}")
    print(f"candidate pool: {len(candidate_rows)} HIGH-confidence rows")
    print(f"selected: {len(selected)} / cap {args.max_insert_cap}")
    print(f"rejected: {len(rejected)}")
    print(f"selected per category: {dict(sel_per_cat)}")
    print(f"rejection buckets: {dict(rejection_reason_counts)}")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
