"""Phase 8.5B — Amazon Reviews 2023 LOCAL preflight with metadata
join + tightened filters + confidence scoring.

Reads ONLY local files. NO network. NO Amazon API. NO Amazon.com
scrape. Writes ZERO source_records / personas / traits /
evidence-links / graph / sim / UI rows.

Pipeline:
  1. Stream first N reviews per category (default N=1000), collect
     parent_asin set.
  2. Stream the matching metadata file ONCE; retain only entries
     whose parent_asin appears in the candidate set. Drop image /
     video URLs at parse time.
  3. Join review → metadata; run `score_review` (Phase 8.5B
     deterministic scorer).
  4. Bucket by `ReviewConfidence` (HIGH / MEDIUM / LOW / REJECTED)
     and write a structured comparison-with-8.5A audit JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewsAdapterConfig, AmazonReviewsLocalReader,
    MetadataIndex, ReviewConfidence,
    TIGHTENED_SEARCH_TERMS,
    discover_category_files, matches_search_terms,
    resolve_categories, score_review,
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 8.5B — Amazon Reviews 2023 LOCAL preflight with "
            "metadata join + tightened filters."
        ),
    )
    parser.add_argument(
        "--records-per-category", type=int, default=1000,
        help="Maximum review records to inspect per category (default 1000).",
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "amazon_reviews_2023_preflight_8_5b.json"

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
    print("Phase 8.5B — Amazon Reviews 2023 LOCAL PREFLIGHT (filtered + joined)")
    print("=" * 72)
    print(f"resolved categories: {cats}")
    print(f"records per category cap: {args.records_per_category}")

    # Try to load 8.5A audit JSON for before/after comparison.
    a_path = audit_root / "amazon_reviews_2023_preflight_8_5a.json"
    a_summary: dict | None = None
    if a_path.is_file():
        try:
            a_summary = json.loads(a_path.read_text(encoding="utf-8"))
        except Exception:
            a_summary = None

    summary: dict = {
        "phase": "8_5b_amazon_reviews_2023_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "dataset_dir": str(dataset_dir),
        "resolved_categories": cats if isinstance(cats, list) else "ALL",
        "records_per_category_cap": args.records_per_category,
        "compliance_note": (
            "Local file read only. NO Amazon API. NO Amazon.com scrape. "
            "user_id hashed (sha256/16). Image + video URLs dropped at "
            "parse time. Phase 8.5B writes ZERO source_records, ZERO "
            "personas, ZERO traits, ZERO evidence-links."
        ),
        "tightened_search_terms": list(TIGHTENED_SEARCH_TERMS),
        "categories": {},
        "comparison_8_5a_vs_8_5b": {},
    }

    for cat, files in discovered.items():
        if not files:
            summary["categories"][cat] = {
                "files_found": 0, "records_inspected": 0,
                "accepted": 0, "rejected": 0,
                "by_confidence": {}, "by_rejection_reason": {},
            }
            continue
        meta_file = _meta_path_for(raw_dir, cat)
        # ---- Pass 1: collect first N reviews + parent_asins ----------
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
        print(
            f"  {cat}: {len(reviews)} reviews scanned, "
            f"{len(target_asins)} unique parent_asin candidates"
        )
        # ---- Pass 2: stream metadata for those asins -----------------
        idx = MetadataIndex(meta_file=meta_file, target_asins=target_asins)
        idx.load()
        print(
            f"  {cat}: metadata index built — "
            f"{len(idx.index)}/{len(target_asins)} asins resolved "
            f"(scanned {idx.lines_scanned:,} metadata lines)"
        )
        # ---- Pass 3: score every review ------------------------------
        by_confidence: Counter = Counter()
        by_rejection: Counter = Counter()
        accepted_high: list[dict] = []
        accepted_medium: list[dict] = []
        accepted_low: list[dict] = []
        rejected_prime_shipping: list[dict] = []
        rejected_unqualified_flavor: list[dict] = []
        rejected_other: list[dict] = []
        product_title_counter: Counter = Counter()
        a85_term_hits: Counter = Counter()  # for parity with 8.5A reporting

        for rec in reviews:
            meta = idx.lookup(rec.parent_asin)
            score = score_review(review=rec, metadata=meta)
            by_confidence[score.confidence.value] += 1
            if score.confidence is ReviewConfidence.REJECTED:
                by_rejection[score.rejection_reason or "unspecified"] += 1
            # 8.5A-compatible term-hit counter for like-with-like comparison
            for t in matches_search_terms(
                record=rec, search_terms=list(TIGHTENED_SEARCH_TERMS),
            ):
                a85_term_hits[t] += 1
            # Bucket sample rows
            row = {
                "rating": rec.rating,
                "verified_purchase": rec.verified_purchase,
                "title": rec.title[:100],
                "text_excerpt": rec.text[:240],
                "matched_terms": list(score.matched_terms)[:8],
                "denylist_hits": list(score.denylist_hits),
                "score": score.score,
                "confidence": score.confidence.value,
                "rejection_reason": score.rejection_reason,
                "has_metadata": score.has_metadata,
                "metadata_title": (meta.title[:120] if meta else None),
                "metadata_main_category": meta.main_category if meta else None,
                "metadata_categories": list(meta.categories) if meta else None,
            }
            if score.confidence is ReviewConfidence.HIGH_CONFIDENCE:
                if len(accepted_high) < 5:
                    accepted_high.append(row)
                if meta and meta.title:
                    product_title_counter[meta.title[:80]] += 1
            elif score.confidence is ReviewConfidence.MEDIUM_CONFIDENCE:
                if len(accepted_medium) < 3:
                    accepted_medium.append(row)
                if meta and meta.title:
                    product_title_counter[meta.title[:80]] += 1
            elif score.confidence is ReviewConfidence.LOW_CONFIDENCE:
                if len(accepted_low) < 3:
                    accepted_low.append(row)
            elif score.confidence is ReviewConfidence.REJECTED:
                if score.rejection_reason == "prime_shipping_only":
                    if len(rejected_prime_shipping) < 5:
                        rejected_prime_shipping.append(row)
                elif "flavor" in (score.denylist_hits[0] if score.denylist_hits else ""):
                    if len(rejected_unqualified_flavor) < 5:
                        rejected_unqualified_flavor.append(row)
                else:
                    if len(rejected_other) < 5:
                        rejected_other.append(row)

        accepted = (
            by_confidence[ReviewConfidence.HIGH_CONFIDENCE.value]
            + by_confidence[ReviewConfidence.MEDIUM_CONFIDENCE.value]
            + by_confidence[ReviewConfidence.LOW_CONFIDENCE.value]
        )
        rejected = by_confidence[ReviewConfidence.REJECTED.value]
        summary["categories"][cat] = {
            "files_found": len(files),
            "records_inspected": len(reviews),
            "metadata_resolved_asins": len(idx.index),
            "metadata_lines_scanned": idx.lines_scanned,
            "by_confidence": dict(by_confidence),
            "accepted": accepted,
            "rejected": rejected,
            "by_rejection_reason": dict(by_rejection),
            "tightened_term_hits": dict(a85_term_hits),
            "top_matched_products_by_title": dict(
                product_title_counter.most_common(10)
            ),
            "sample_accepted_high_confidence": accepted_high,
            "sample_accepted_medium_confidence": accepted_medium,
            "sample_accepted_low_confidence": accepted_low,
            "sample_rejected_prime_shipping": rejected_prime_shipping,
            "sample_rejected_unqualified_flavor": rejected_unqualified_flavor,
            "sample_rejected_other": rejected_other,
        }
        print(
            f"  {cat}: ACCEPT {accepted} (H={by_confidence[ReviewConfidence.HIGH_CONFIDENCE.value]} "
            f"M={by_confidence[ReviewConfidence.MEDIUM_CONFIDENCE.value]} "
            f"L={by_confidence[ReviewConfidence.LOW_CONFIDENCE.value]}) "
            f"REJECT {rejected} (reasons: {dict(by_rejection)})"
        )

    # ---- Comparison block --------------------------------------------
    if a_summary is not None:
        cmp_block: dict = {}
        for cat, info in summary["categories"].items():
            a_cat = (a_summary.get("categories") or {}).get(cat) or {}
            cmp_block[cat] = {
                "8_5a_matched": a_cat.get("matched_records"),
                "8_5b_accepted": info["accepted"],
                "8_5b_rejected": info["rejected"],
                "8_5b_high_confidence": info["by_confidence"].get(
                    ReviewConfidence.HIGH_CONFIDENCE.value, 0,
                ),
                "8_5a_top_terms": list(
                    (a_cat.get("term_hit_counts") or {}).keys()
                )[:10],
                "8_5b_top_terms": list(info["tightened_term_hits"].keys())[:10],
            }
        summary["comparison_8_5a_vs_8_5b"] = cmp_block

    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
