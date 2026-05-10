"""Phase 8.5A — Amazon Reviews 2023 LOCAL preflight (operator-only).

Reads ONLY local files under `AMAZON_REVIEWS_2023_DIR/raw/`. NO
network call, NO Amazon API call, NO Amazon.com scrape — those are
forbidden and drift-tested.

Reports:
  * directory presence
  * resolved category list (specific names, or `ALL`)
  * per-category file presence (or exact missing-file paths)
  * per-category record count up to 1000 from the first matched
    file, after low-quality filter + Triton search-term filter
  * audit JSON under apps/api/_audit/
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
    discover_category_files, matches_search_terms, resolve_categories,
)


TRITON_SEARCH_TERMS = [
    "energy drink", "caffeine", "caffeinated",
    "pre workout", "pre-workout",
    "electrolyte", "hydration", "sports drink",
    "sugar free", "low sugar", "crash", "flavor",
    "Red Bull", "Monster", "Celsius", "Prime", "Gatorade",
]


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
        description="Phase 8.5A — Amazon Reviews 2023 LOCAL preflight.",
    )
    parser.add_argument(
        "--records-per-category", type=int, default=1000,
        help="Maximum records to inspect per category file (default 1000).",
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "amazon_reviews_2023_preflight_8_5a.json"

    dir_str = os.environ.get("AMAZON_REVIEWS_2023_DIR")
    mode = os.environ.get("AMAZON_REVIEWS_2023_MODE", "off")
    raw_categories = os.environ.get("AMAZON_REVIEWS_2023_CATEGORIES")

    print("=" * 72)
    print("Phase 8.5A — Amazon Reviews 2023 LOCAL PREFLIGHT")
    print("=" * 72)
    print(f"AMAZON_REVIEWS_2023_DIR present: {bool(dir_str)}")
    print(f"AMAZON_REVIEWS_2023_MODE: {mode}")
    print(f"AMAZON_REVIEWS_2023_CATEGORIES: {raw_categories or '(unset)'}")

    summary: dict = {
        "phase": "8_5a_amazon_reviews_2023_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "dir_set": bool(dir_str),
        "mode": mode,
        "categories_setting": raw_categories,
        "categories_supported": ["specific", "ALL"],
        "records_per_category_cap": args.records_per_category,
        "compliance_note": (
            "Local file read only. NO Amazon API call. NO "
            "Amazon.com scrape. user_id is hashed (sha256/16). "
            "Image URLs are dropped. Phase 8.5A does NOT write "
            "source_records."
        ),
        "categories": {},
        "expected_local_paths": [],
        "missing_files": [],
    }

    if not dir_str:
        print(
            "AMAZON_REVIEWS_2023_DIR is unset; cannot proceed. "
            "Set the env var before retrying."
        )
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return 0

    dataset_dir = Path(dir_str)
    summary["dataset_dir"] = str(dataset_dir)
    summary["dataset_dir_exists"] = dataset_dir.is_dir()
    summary["dataset_raw_dir_exists"] = (dataset_dir / "raw").is_dir()
    print(f"dataset dir exists: {dataset_dir.is_dir()}")
    print(f"dataset raw/ dir exists: {(dataset_dir / 'raw').is_dir()}")

    cats = resolve_categories(
        raw_setting=raw_categories, dataset_dir=dataset_dir,
    )
    summary["resolved_categories_kind"] = (
        "ALL" if cats == "ALL" else "specific"
    )
    summary["resolved_categories"] = (
        cats if isinstance(cats, list) else "ALL"
    )

    discovered = discover_category_files(
        dataset_dir=dataset_dir, categories=cats,
    )
    print(f"\nresolved categories: {cats}")
    print(f"discovered category buckets: {len(discovered)}")

    # Operator-friendly missing-file report
    if isinstance(cats, list):
        for cat in cats:
            if not discovered.get(cat):
                # Suggest the canonical filename pattern
                expected = (dataset_dir / "raw" / f"{cat}.jsonl.gz").as_posix()
                summary["expected_local_paths"].append(expected)
                summary["missing_files"].append(cat)
                print(f"  MISSING: {cat} (expected {expected})")
    elif cats == "ALL" and not discovered:
        print(
            "  (ALL mode requested but raw/ dir empty or not present; "
            "no files to inspect.)"
        )

    if not discovered:
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            "\nNo category files found. Phase 8.5A still PASSes — "
            "preflight reports cleanly. Operator must download the "
            "dataset before any ingestion phase."
        )
        print(f"\n→ audit JSON: {out_path}")
        return 0

    reader = AmazonReviewsLocalReader(
        dataset_dir=dataset_dir,
        config=AmazonReviewsAdapterConfig(
            max_records_per_category=args.records_per_category,
        ),
    )

    for cat, files in discovered.items():
        if not files:
            summary["categories"][cat] = {
                "files_found": 0,
                "records_inspected": 0,
                "matched_records": 0,
            }
            continue
        records_inspected = 0
        matched_records = 0
        term_hits: Counter = Counter()
        sample_matches: list[dict] = []
        for rec in reader.iter_category(
            category=cat, files=files,
            max_records=args.records_per_category,
        ):
            records_inspected += 1
            matched = matches_search_terms(
                record=rec, search_terms=TRITON_SEARCH_TERMS,
            )
            if matched:
                matched_records += 1
                for t in matched:
                    term_hits[t] += 1
                if len(sample_matches) < 5:
                    sample_matches.append({
                        "rating": rec.rating,
                        "title": rec.title[:100],
                        "text_excerpt": rec.text[:240],
                        "matched_terms": matched,
                        "verified_purchase": rec.verified_purchase,
                    })
        summary["categories"][cat] = {
            "files_found": len(files),
            "files": [str(f) for f in files],
            "records_inspected": records_inspected,
            "matched_records": matched_records,
            "term_hit_counts": dict(term_hits),
            "sample_matches": sample_matches,
        }
        print(
            f"  category {cat}: files={len(files)} "
            f"inspected={records_inspected} matched={matched_records} "
            f"top_terms={[t for t, _ in term_hits.most_common(5)]}"
        )

    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
