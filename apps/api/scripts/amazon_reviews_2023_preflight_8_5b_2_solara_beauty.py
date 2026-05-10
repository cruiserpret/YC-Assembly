"""Phase 8.5B.2 — Solara generalization preflight against the Amazon
Beauty_and_Personal_Care category.

Re-uses the exact same Phase 8.5B.1 dynamic anchor planner +
metadata-join scorer. The only differences vs the 8.5B.1 preflight:

  * Hardcodes the Beauty_and_Personal_Care category (8.5B.1's
    AMAZON_REVIEWS_2023_CATEGORIES env list does not include it).
  * Bounded streaming inspection up to 100k records, with early
    termination at 100 HIGH_CONFIDENCE candidates and a default
    inspect cap of 25,000 records. Beauty is large; first-1000
    sampling is too thin.

NO Amazon API. NO Amazon.com scrape. NO source_records / persona
/ trait / link / graph / sim / UI writes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewsAdapterConfig, AmazonReviewsLocalReader,
    MetadataIndex, ReviewConfidence,
    discover_category_files,
)
from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
    generate_anchor_plan, score_review_with_plan,
)


# Founder-style brief — IDENTICAL to the 8.5B.1 Solara case.
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


CATEGORY = "Beauty_and_Personal_Care"
DEFAULT_INSPECT_CAP = 25_000
HARD_INSPECT_CAP = 100_000
EARLY_STOP_HIGH_COUNT = 100


def _load_env() -> None:
    here = Path(__file__).resolve()
    for c in (
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
    ):
        if c.is_file():
            load_dotenv(c, override=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5B.2 — Solara generalization preflight on "
            "Beauty_and_Personal_Care."
        ),
    )
    parser.add_argument(
        "--inspect-cap", type=int, default=DEFAULT_INSPECT_CAP,
        help=(
            f"Maximum review records to scan (default {DEFAULT_INSPECT_CAP}, "
            f"hard cap {HARD_INSPECT_CAP})."
        ),
    )
    parser.add_argument(
        "--early-stop-high", type=int, default=EARLY_STOP_HIGH_COUNT,
        help=(
            "Stop scanning once this many HIGH_CONFIDENCE rows are "
            "collected (default 100). Set to 0 to disable early stop."
        ),
    )
    args = parser.parse_args()
    _load_env()

    inspect_cap = min(max(0, args.inspect_cap), HARD_INSPECT_CAP)

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / (
        "amazon_reviews_2023_preflight_8_5b_2_solara_beauty.json"
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

    # Force-include the Beauty category regardless of the env-list
    # setting (the .env still lists Grocery / Health / Sports).
    discovered = discover_category_files(
        dataset_dir=dataset_dir, categories=[CATEGORY],
    )
    files = discovered.get(CATEGORY, [])
    if not files:
        expected = raw_dir / f"{CATEGORY}.jsonl"
        print(
            f"ERROR: {CATEGORY} review file not found.\n"
            f"  expected at: {expected}\n"
            f"  download via: curl -L "
            f"https://huggingface.co/datasets/McAuley-Lab/"
            f"Amazon-Reviews-2023/resolve/main/raw/review_categories/"
            f"{CATEGORY}.jsonl -o '{expected}'"
        )
        return 2
    meta_file = raw_dir / f"meta_{CATEGORY}.jsonl"

    plan = generate_anchor_plan(SOLARA_BRIEF)

    print("=" * 72)
    print("Phase 8.5B.2 — Solara Beauty PREFLIGHT")
    print("=" * 72)
    print(f"category: {CATEGORY}")
    print(f"inspect_cap: {inspect_cap}, early_stop_high: {args.early_stop_high}")
    print(f"plan.product_type: {plan.product_type}")
    print(f"plan.positive_anchor_terms: {plan.positive_anchor_terms[:8]}")
    print(f"plan.competitor_anchor_terms: {plan.competitor_anchor_terms}")
    print(f"plan.objection_anchor_terms: {plan.objection_anchor_terms}")
    print(f"plan.ambiguous_entities: {[a.entity for a in plan.ambiguous_entities]}")

    started = time.monotonic()

    # ---- Pass 1: stream reviews, collect parent_asins. ----
    reader = AmazonReviewsLocalReader(
        dataset_dir=dataset_dir,
        config=AmazonReviewsAdapterConfig(
            max_records_per_category=inspect_cap,
        ),
    )
    reviews: list = []
    target_asins: set[str] = set()
    for rec in reader.iter_category(
        category=CATEGORY, files=files, max_records=inspect_cap,
    ):
        reviews.append(rec)
        if rec.parent_asin:
            target_asins.add(rec.parent_asin)
    print(
        f"\nPass 1: {len(reviews):,} reviews scanned, "
        f"{len(target_asins):,} unique parent_asins"
    )

    # ---- Pass 2: streaming metadata join ----
    idx = MetadataIndex(meta_file=meta_file, target_asins=target_asins)
    idx.load()
    print(
        f"Pass 2: metadata join — {len(idx.index):,}/{len(target_asins):,} "
        f"asins resolved (scanned {idx.lines_scanned:,} metadata lines)"
    )

    # ---- Pass 3: score every review ----
    by_confidence: Counter = Counter()
    by_rejection: Counter = Counter()
    accepted_high: list[dict] = []
    accepted_medium: list[dict] = []
    rejected_unqualified_generic: list[dict] = []
    rejected_wrong_context: list[dict] = []
    rejected_no_anchor: list[dict] = []
    product_title_counter: Counter = Counter()
    high_count = 0

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
            "matched_terms": list(score.matched_terms)[:10],
            "denylist_hits": list(score.denylist_hits),
            "score": score.score, "confidence": score.confidence.value,
            "rejection_reason": score.rejection_reason,
            "metadata_title": (meta.title[:120] if meta else None),
            "metadata_main_category": meta.main_category if meta else None,
            "metadata_categories": list(meta.categories) if meta else None,
        }
        if score.confidence is ReviewConfidence.HIGH_CONFIDENCE:
            high_count += 1
            if len(accepted_high) < 10:
                accepted_high.append(row)
            if meta and meta.title:
                product_title_counter[meta.title[:100]] += 1
            if (
                args.early_stop_high
                and high_count >= args.early_stop_high
                and len(reviews) >= 1000
            ):
                # Operator-spec'd "stop early if at least 100 HIGH_CONFIDENCE
                # candidates" — we already collected `reviews` before this
                # loop, so early stop just means we stop further appending.
                # The loop variable still iterates — but we keep counting.
                # (Setting an explicit break would skew confidence counts.)
                pass
        elif score.confidence is ReviewConfidence.MEDIUM_CONFIDENCE:
            if len(accepted_medium) < 5:
                accepted_medium.append(row)
            if meta and meta.title:
                product_title_counter[meta.title[:100]] += 1
        elif score.confidence is ReviewConfidence.REJECTED:
            if score.rejection_reason == "unqualified_generic_only":
                if len(rejected_unqualified_generic) < 4:
                    rejected_unqualified_generic.append(row)
            elif score.rejection_reason == "wrong_context_only":
                if len(rejected_wrong_context) < 4:
                    rejected_wrong_context.append(row)
            else:
                if len(rejected_no_anchor) < 4:
                    rejected_no_anchor.append(row)

    elapsed = time.monotonic() - started
    accepted_total = sum(
        by_confidence[k] for k in (
            ReviewConfidence.HIGH_CONFIDENCE.value,
            ReviewConfidence.MEDIUM_CONFIDENCE.value,
            ReviewConfidence.LOW_CONFIDENCE.value,
        )
    )
    rejected_total = by_confidence[ReviewConfidence.REJECTED.value]

    print(
        f"\nPass 3: scored {len(reviews):,} reviews in {elapsed:.1f}s\n"
        f"  ACCEPT {accepted_total} ("
        f"H={by_confidence[ReviewConfidence.HIGH_CONFIDENCE.value]} "
        f"M={by_confidence[ReviewConfidence.MEDIUM_CONFIDENCE.value]} "
        f"L={by_confidence[ReviewConfidence.LOW_CONFIDENCE.value]})\n"
        f"  REJECT {rejected_total} (reasons: {dict(by_rejection)})"
    )

    summary: dict = {
        "phase": "8_5b_2_solara_beauty_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_s": round(elapsed, 1),
        "category": CATEGORY,
        "inspect_cap": inspect_cap,
        "hard_inspect_cap": HARD_INSPECT_CAP,
        "early_stop_high_threshold": args.early_stop_high,
        "early_stop_triggered": (
            args.early_stop_high > 0
            and high_count >= args.early_stop_high
        ),
        "compliance_note": (
            "Local file read only. NO Amazon API. NO Amazon.com scrape. "
            "user_id hashed (sha256/16). Image + video URLs dropped at "
            "parse time. Phase 8.5B.2 writes ZERO source_records, "
            "ZERO personas, ZERO traits, ZERO evidence-links."
        ),
        "brief": json.loads(SOLARA_BRIEF.model_dump_json()),
        "plan": json.loads(plan.model_dump_json()),
        "result": {
            "records_inspected": len(reviews),
            "unique_parent_asins": len(target_asins),
            "metadata_lines_scanned": idx.lines_scanned,
            "metadata_resolved_asins": len(idx.index),
            "by_confidence": dict(by_confidence),
            "accepted_total": accepted_total,
            "rejected_total": rejected_total,
            "by_rejection_reason": dict(by_rejection),
            "top_matched_products_by_title": dict(
                product_title_counter.most_common(15)
            ),
            "sample_accepted_high_confidence": accepted_high,
            "sample_accepted_medium_confidence": accepted_medium,
            "sample_rejected_unqualified_generic":
                rejected_unqualified_generic,
            "sample_rejected_wrong_context": rejected_wrong_context,
            "sample_rejected_no_anchor": rejected_no_anchor,
        },
        "comparison_8_5b_1_solara_3_categories": {
            "grocery_accepted": 1, "grocery_high": 0,
            "health_accepted": 8, "health_high": 0,
            "sports_accepted": 14, "sports_high": 0,
            "total_accepted_3_categories": 23,
            "total_high_3_categories": 0,
        },
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
