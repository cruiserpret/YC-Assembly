"""Phase 8.5B.1 — dynamic-anchor Amazon preflight.

Runs the dynamic `EvidenceAnchorPlan` pipeline for two products:

  1. Triton Drinks  — regression case from Phases 8.4 / 8.5A / 8.5B.
  2. Solara Shield  — brand-new imaginary product. The planner has
     NEVER seen sunscreen-specific anchors and will infer them
     entirely from the founder brief.

Both products go through the SAME code path:

  brief → generate_anchor_plan(brief)  → score_review_with_plan(...)

The audit JSON contains both plans + their scoring outcomes so the
operator can see whether the planner produced product-specific or
generic / sloppy output.

NO Amazon API. NO Amazon.com scrape. Reads ONLY local files. Writes
ZERO source_records / personas / traits / evidence-links / graph /
sim / UI rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewsAdapterConfig, AmazonReviewsLocalReader,
    MetadataIndex, ReviewConfidence,
    discover_category_files, resolve_categories,
)
from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
    generate_anchor_plan, score_review_with_plan,
)


# --- Founder briefs -------------------------------------------------


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
    ],
    competitors=["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"],
    optional_constraints=[],
)


SOLARA_BRIEF = ProductBriefForPlanning(
    product_name="Solara Shield",
    product_description=(
        "A portable mineral sunscreen stick designed for "
        "acne-prone college students and outdoor athletes who want "
        "daily face protection they can reapply during school, "
        "workouts, hikes, and outdoor sports without feeling greasy "
        "or causing breakouts."
    ),
    price_or_price_structure="$18.99",
    launch_geography="Arizona, United States",
    target_customers=[
        "college students", "outdoor runners", "hikers", "athletes",
        "acne-prone young adults",
        "people who avoid sunscreen because it feels greasy or "
        "inconvenient to reapply",
    ],
    competitors=[
        "Supergoop", "Neutrogena", "La Roche-Posay", "CeraVe", "Sun Bum",
    ],
    optional_constraints=[
        "Do not manually provide sunscreen anchors. "
        "Assembly must infer all anchors itself.",
    ],
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


def _plan_to_dict(plan: EvidenceAnchorPlan) -> dict:
    return json.loads(plan.model_dump_json())


def _run_for_product(
    *,
    label: str,
    brief: ProductBriefForPlanning,
    dataset_dir: Path,
    raw_dir: Path,
    discovered: dict,
    records_per_category: int,
) -> dict:
    plan = generate_anchor_plan(brief)
    print(f"\n{'=' * 72}")
    print(f"DYNAMIC PLAN — {label}: {brief.product_name}")
    print(f"{'=' * 72}")
    print(f"  product_type: {plan.product_type}")
    print(f"  positive_anchor_terms (top 8): {plan.positive_anchor_terms[:8]}")
    print(f"  competitor_anchor_terms: {plan.competitor_anchor_terms}")
    print(f"  use_case_anchor_terms (top 6): {plan.use_case_anchor_terms[:6]}")
    print(f"  objection_anchor_terms: {plan.objection_anchor_terms}")
    print(f"  ambiguous_entities: {[a.entity for a in plan.ambiguous_entities]}")
    print(f"  metadata_relevance_rules: {len(plan.metadata_relevance_rules)}")

    product_summary: dict = {
        "label": label,
        "brief": json.loads(brief.model_dump_json()),
        "plan": _plan_to_dict(plan),
        "categories": {},
    }

    for cat, files in discovered.items():
        if not files:
            product_summary["categories"][cat] = {"files_found": 0}
            continue
        meta_file = _meta_path_for(raw_dir, cat)
        reader = AmazonReviewsLocalReader(
            dataset_dir=dataset_dir,
            config=AmazonReviewsAdapterConfig(
                max_records_per_category=records_per_category,
            ),
        )
        # Pass 1: collect first N reviews + parent_asins
        reviews: list = []
        target_asins: set[str] = set()
        for rec in reader.iter_category(
            category=cat, files=files, max_records=records_per_category,
        ):
            reviews.append(rec)
            if rec.parent_asin:
                target_asins.add(rec.parent_asin)
        # Pass 2: stream metadata
        idx = MetadataIndex(meta_file=meta_file, target_asins=target_asins)
        idx.load()
        # Pass 3: score
        by_confidence: Counter = Counter()
        by_rejection: Counter = Counter()
        accepted_high: list[dict] = []
        rejected_wrong_context: list[dict] = []
        rejected_unqualified_generic: list[dict] = []
        rejected_other: list[dict] = []
        product_title_counter: Counter = Counter()
        for rec in reviews:
            meta = idx.lookup(rec.parent_asin)
            score = score_review_with_plan(
                review=rec, metadata=meta, plan=plan,
            )
            by_confidence[score.confidence.value] += 1
            if score.confidence is ReviewConfidence.REJECTED:
                by_rejection[score.rejection_reason or "unspecified"] += 1
            row = {
                "rating": rec.rating, "verified_purchase": rec.verified_purchase,
                "title": rec.title[:100], "text_excerpt": rec.text[:240],
                "matched_terms": list(score.matched_terms)[:8],
                "denylist_hits": list(score.denylist_hits),
                "score": score.score, "confidence": score.confidence.value,
                "rejection_reason": score.rejection_reason,
                "metadata_title": (meta.title[:120] if meta else None),
                "metadata_main_category": meta.main_category if meta else None,
                "metadata_categories": list(meta.categories) if meta else None,
            }
            if score.confidence is ReviewConfidence.HIGH_CONFIDENCE:
                if len(accepted_high) < 5:
                    accepted_high.append(row)
                if meta and meta.title:
                    product_title_counter[meta.title[:80]] += 1
            elif score.confidence is ReviewConfidence.REJECTED:
                if score.rejection_reason == "wrong_context_only":
                    if len(rejected_wrong_context) < 4:
                        rejected_wrong_context.append(row)
                elif score.rejection_reason == "unqualified_generic_only":
                    if len(rejected_unqualified_generic) < 4:
                        rejected_unqualified_generic.append(row)
                else:
                    if len(rejected_other) < 4:
                        rejected_other.append(row)

        accepted = sum(
            by_confidence[k] for k in (
                ReviewConfidence.HIGH_CONFIDENCE.value,
                ReviewConfidence.MEDIUM_CONFIDENCE.value,
                ReviewConfidence.LOW_CONFIDENCE.value,
            )
        )
        rejected = by_confidence[ReviewConfidence.REJECTED.value]
        product_summary["categories"][cat] = {
            "files_found": len(files),
            "records_inspected": len(reviews),
            "metadata_resolved_asins": len(idx.index),
            "metadata_lines_scanned": idx.lines_scanned,
            "by_confidence": dict(by_confidence),
            "accepted": accepted,
            "rejected": rejected,
            "by_rejection_reason": dict(by_rejection),
            "top_matched_products_by_title": dict(
                product_title_counter.most_common(8)
            ),
            "sample_accepted_high_confidence": accepted_high,
            "sample_rejected_wrong_context": rejected_wrong_context,
            "sample_rejected_unqualified_generic": rejected_unqualified_generic,
            "sample_rejected_other": rejected_other,
        }
        print(
            f"  {cat}: ACCEPT {accepted} (H={by_confidence[ReviewConfidence.HIGH_CONFIDENCE.value]} "
            f"M={by_confidence[ReviewConfidence.MEDIUM_CONFIDENCE.value]} "
            f"L={by_confidence[ReviewConfidence.LOW_CONFIDENCE.value]}) "
            f"REJECT {rejected} (reasons: {dict(by_rejection)})"
        )
    return product_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5B.1 — dynamic anchor planner Amazon preflight, "
            "Triton regression + Solara generalization."
        ),
    )
    parser.add_argument(
        "--records-per-category", type=int, default=1000,
        help="Maximum review records to inspect per category.",
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "amazon_reviews_2023_preflight_8_5b_1_dynamic.json"

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    raw_categories = os.environ.get("AMAZON_REVIEWS_2023_CATEGORIES")
    if not dir_str:
        print("ERROR: AMAZON_REVIEWS_2023_DIR is unset.")
        return 2
    dataset_dir = Path(dir_str)
    raw_dir = dataset_dir / "raw"
    if not raw_dir.is_dir():
        print(f"ERROR: {raw_dir} does not exist.")
        return 2

    cats = resolve_categories(
        raw_setting=raw_categories, dataset_dir=dataset_dir,
    )
    discovered = discover_category_files(
        dataset_dir=dataset_dir, categories=cats,
    )

    print("=" * 72)
    print("Phase 8.5B.1 — Dynamic Anchor Amazon PREFLIGHT")
    print("=" * 72)
    print(f"resolved categories: {cats}")
    print(f"records per category cap: {args.records_per_category}")

    summary: dict = {
        "phase": "8_5b_1_amazon_dynamic_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "dataset_dir": str(dataset_dir),
        "resolved_categories": cats if isinstance(cats, list) else "ALL",
        "records_per_category_cap": args.records_per_category,
        "compliance_note": (
            "Local file read only. NO Amazon API. NO Amazon.com scrape. "
            "user_id hashed (sha256/16). Image + video URLs dropped at "
            "parse time. Phase 8.5B.1 writes ZERO source_records, ZERO "
            "personas, ZERO traits, ZERO evidence-links."
        ),
        "products": {},
    }

    for label, brief in (("triton", TRITON_BRIEF),
                          ("solara_shield", SOLARA_BRIEF)):
        summary["products"][label] = _run_for_product(
            label=label, brief=brief, dataset_dir=dataset_dir,
            raw_dir=raw_dir, discovered=discovered,
            records_per_category=args.records_per_category,
        )

    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
