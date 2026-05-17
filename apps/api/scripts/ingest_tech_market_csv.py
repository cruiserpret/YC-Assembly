"""Phase 11D.2 — Tech-market CSV ingestion CLI.

Reads an operator-provided CSV of raw tech-market text snippets,
distills each row into a structured `DistilledTechSignal`, dedupes
against prior runs, and (in commit mode) inserts into the
`tech_market_signal` Postgres table.

DRY-RUN IS THE DEFAULT. Commit mode requires both `--commit` AND
local Postgres access. There is NO production-Railway codepath — the
runtime flag `ASSEMBLY_TECH_MARKET_SIGNALS_ENABLED` stays off until
operator-explicit; this CLI is a dev/local tool only.

Required CSV header column:
  text

Optional CSV header columns (handled cleanly if absent):
  company_or_product, competitor_name, buyer_type, market_context,
  source_timestamp, evidence_url, metadata_json

Example:

    uv run python scripts/ingest_tech_market_csv.py \\
        --csv-path /tmp/g2_synthetic.csv \\
        --source-provider g2_synthetic_csv \\
        --product-category ai_saas \\
        --market-context AI_tool \\
        --audit-out /tmp/tech_market_audit.json
    # default is --dry-run; add --commit only on a local dev DB.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Iterable


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Phase 11D.2 — ingest an operator-provided tech-market "
            "CSV into the tech_market_signal table. Dry-run by "
            "default; --commit only for local/dev DBs."
        ),
    )
    p.add_argument("--csv-path", required=True, help="path to input CSV")
    p.add_argument(
        "--source-provider", required=True,
        help="identifier for the upstream provider (e.g. 'g2_synthetic_csv')",
    )
    p.add_argument(
        "--source-category", default=None,
        help="provider's own category label (kept verbatim in DB)",
    )
    p.add_argument(
        "--product-category", required=True,
        help=(
            "Assembly-side product category "
            "(ai_saas | browser_extension | devtool_api | "
            "b2b_workflow_saas | consumer_mobile_app | marketplace | "
            "unknown)"
        ),
    )
    p.add_argument(
        "--market-context", default=None,
        help=(
            "default market context applied when a CSV row doesn't "
            "carry its own (B2C | B2B | prosumer | devtool | "
            "marketplace | AI_tool | unknown)"
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="default — distill + report only, no DB writes",
    )
    mode.add_argument(
        "--commit", dest="commit", action="store_true",
        help=(
            "write inserts to the local DB. NOT FOR PRODUCTION. "
            "Requires the local Postgres + applied alembic migrations."
        ),
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="cap how many CSV rows to scan (default: all)",
    )
    p.add_argument(
        "--audit-out", default=None,
        help="write audit JSON to this path; default prints to stdout",
    )
    return p


async def _async_main(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv_path).expanduser().resolve()
    if not csv_path.exists():
        print(f"error: csv not found: {csv_path}", file=sys.stderr)
        return 2
    if not csv_path.is_file():
        print(f"error: csv path is not a file: {csv_path}", file=sys.stderr)
        return 2

    # Determine effective dry_run. The default is True; `--commit`
    # flips it off. `--dry-run` is explicit but already the default.
    dry_run = not bool(getattr(args, "commit", False))

    # Lazy imports keep the module cheap when --help is the only
    # user intent.
    from assembly.sources.tech_market_provider.ingestion import (
        NullTechMarketPersister,
        build_audit_payload,
        ingest_csv_rows,
    )
    from assembly.sources.tech_market_provider.signal_types import (
        MARKET_CONTEXTS, PRODUCT_CATEGORIES,
    )

    if (
        args.market_context is not None
        and args.market_context not in MARKET_CONTEXTS
    ):
        print(
            f"error: --market-context must be one of "
            f"{list(MARKET_CONTEXTS)}; got {args.market_context!r}",
            file=sys.stderr,
        )
        return 2
    if args.product_category not in PRODUCT_CATEGORIES:
        # Warn but do not fail — operator may want to land freeform
        # product-category labels and accept the soft-vocab warning.
        print(
            f"warning: --product-category {args.product_category!r} "
            f"is not in the documented set {list(PRODUCT_CATEGORIES)}",
            file=sys.stderr,
        )

    if dry_run:
        persister = NullTechMarketPersister()
    else:
        # Production-shaped persister, but pointed at WHATEVER DB the
        # local environment provides. This is intentionally not a
        # production deploy path.
        from assembly.db import get_sessionmaker
        from assembly.sources.tech_market_provider.ingestion import (
            PostgresTechMarketPersister,
        )
        persister = PostgresTechMarketPersister(get_sessionmaker())

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "text" not in {
            (n or "").strip() for n in reader.fieldnames
        }:
            print(
                "error: CSV header missing required column 'text'",
                file=sys.stderr,
            )
            return 2
        # Stream rows — never hold the whole CSV in memory.
        stats = await ingest_csv_rows(
            _iter_rows(reader),
            persister=persister,
            source_provider=args.source_provider,
            source_category=args.source_category,
            product_category=args.product_category,
            market_context_hint=args.market_context,
            dry_run=dry_run,
            limit=args.limit,
            csv_path=str(csv_path),
        )

    audit = build_audit_payload(stats)
    if args.audit_out:
        out_path = Path(args.audit_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(audit, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"audit written to {out_path}")
    else:
        print(json.dumps(audit, indent=2, sort_keys=True))

    # Print a one-line human summary.
    counts = audit["counts"]  # type: ignore[index]
    mode = "DRY-RUN" if dry_run else "COMMIT"
    print(
        f"[{mode}] scanned={counts['rows_scanned']} "
        f"accepted={counts['rows_accepted']} "
        f"rejected={counts['rows_rejected']} "
        f"signals_generated={counts['signals_generated']} "
        f"signals_inserted={counts['signals_inserted']} "
        f"duplicates_skipped={counts['duplicates_skipped']} "
        f"runtime={stats.runtime_seconds}s",
        file=sys.stderr,
    )
    return 0


def _iter_rows(
    reader: csv.DictReader,
) -> Iterable[dict[str, str]]:
    """Adapter so the ingestion loop sees a plain iterator of dicts
    with string values (csv.DictReader already provides this, but
    None-valued cells exist when the row has fewer columns than the
    header — coerce those to empty strings)."""
    for row in reader:
        yield {k: (v or "") for k, v in row.items() if k is not None}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
