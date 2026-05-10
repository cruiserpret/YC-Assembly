"""Phase 8.5A — Brave Search preflight (operator-only).

Default: NO live API call. Just reports key presence and the
intended bounded query set. Pass `--live` to actually run up to
3 queries × 5 results, redact URLs for audit, and write the
audit JSON.

NEVER prints the API key. NEVER writes the API key into the
audit JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from assembly.sources.brave import (
    BraveAdapterConfig, BraveSearchClient,
    build_brave_query_set, is_brave_key_present,
    redact_url_for_audit,
)


# Triton 8.5A initial discovery query examples (operator-spec'd).
TRITON_PRODUCT = "Triton Drinks"
TRITON_COMPETITORS = ["Red Bull", "Monster", "Celsius", "Prime", "Gatorade"]
TRITON_EXTRA_TERMS = [
    "energy drink caffeine safety gym",
    "Prime energy drink caffeine recall",
    "best energy drinks for college students",
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
        description="Phase 8.5A — Brave Search preflight.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help=(
            "Run up to 3 live Brave queries × 5 results. Default is "
            "dry-run (key-presence check only)."
        ),
    )
    args = parser.parse_args()
    _load_env()

    audit_root = Path(__file__).resolve().parent.parent / "_audit"
    audit_root.mkdir(exist_ok=True)
    out_path = audit_root / "brave_preflight_8_5a.json"

    key_present = is_brave_key_present()
    queries = build_brave_query_set(
        product_name=TRITON_PRODUCT,
        competitors=TRITON_COMPETITORS,
        extra_terms=TRITON_EXTRA_TERMS,
        max_queries=3,
    )

    print("=" * 72)
    print("Phase 8.5A — Brave Search PREFLIGHT")
    print("=" * 72)
    print(f"BRAVE_SEARCH_API_KEY present: {key_present}")
    print(f"intended queries (max 3): {queries}")
    print(f"max results per query: 5")
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN'}")

    summary: dict = {
        "phase": "8_5a_brave_preflight",
        "completed_at": datetime.now(UTC).isoformat(),
        "key_present": key_present,
        "mode": "live" if args.live else "dry_run",
        "intended_queries": queries,
        "max_queries": 3,
        "max_results_per_query": 5,
        "live_results": [],
        "compliance_note": (
            "Brave is DISCOVERY only. Snippets/URLs are CANDIDATE "
            "evidence and must flow through the existing redaction + "
            "sensitive-filter + dedup pipeline before any persona "
            "ever sees them. Phase 8.5A does NOT write source_records."
        ),
    }

    if args.live:
        if not key_present:
            print("ERROR: --live requested but BRAVE_SEARCH_API_KEY missing.")
            out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return 2
        client = BraveSearchClient(BraveAdapterConfig(
            max_queries=3, max_results_per_query=5,
        ))
        try:
            results = client.search(queries=queries)
        except Exception as e:
            print(f"ERROR during live search: {type(e).__name__}: {e}")
            return 1
        print(f"\nLIVE results: {len(results)} (after dedup)")
        live_rows = []
        for r in results[:50]:
            row = {
                "query": r.query,
                "title": r.title,
                "url": redact_url_for_audit(r.url),
                "domain": r.domain,
                "description_snippet": r.description[:300],
                "age": r.age,
            }
            live_rows.append(row)
            print(f"  [{r.domain}] {r.title[:80]}")
        summary["live_results"] = live_rows

    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n→ audit JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
