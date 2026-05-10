"""Phase 8.2F.6 — operator-only Tavily broader human-signal expansion runner.

Performs ONE capped live ingest of the broader human-signal query
catalog (15 queries × 10 results × 100 accepted records). Each
accepted row carries `target_missing_category` metadata identifying
the stakeholder gap the query was aimed at, so the post-run audit
can roll up coverage delta against the Phase 8.2F.7 missing-category
list.

Operator-only. Behaves identically to Phase 8.2F.5's runner:
  1. flips Tavily compliance status to `approved` (local-dev only)
  2. runs ONE ingest_live with the broader catalog
  3. re-flips status to `review`

NEVER prints or logs the TAVILY_API_KEY. NEVER calls any non-Tavily
live API. NEVER creates personas / traits / evidence_links / graph /
clusters / simulation rows.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import select


def _load_env() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",
        here.parent.parent.parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for c in candidates:
        if c.is_file():
            load_dotenv(c, override=False)


def _domain(url: str | None) -> str:
    if not url:
        return "<none>"
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return "<unparseable>"


async def _amain() -> int:
    _load_env()
    if not os.environ.get("TAVILY_API_KEY"):
        print("ERROR: TAVILY_API_KEY not set after loading .env. Aborting.")
        return 2

    from assembly.db import get_sessionmaker
    from assembly.models.persona import SourceRecord
    from assembly.pipeline.ingestion import (
        TavilySearchExtractAdapter,
        register_or_update_adapter_status,
    )

    sessionmaker = get_sessionmaker()
    adapter = TavilySearchExtractAdapter.for_broader_human_signal_expansion()
    queries = list(adapter._queries)
    catalog = adapter._query_to_category

    # 1) Approve.
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="phase_8_2f_6_local_broader_expansion",
        approved_at=datetime.now(UTC),
        notes="Phase 8.2F.6 broader human-signal expansion; local dev only.",
    )

    # 2) Snapshot pre-existing IDs so we report only the delta.
    # `select(SourceRecord.id).scalars().all()` returns a list of UUIDs
    # directly (not row objects).
    async with sessionmaker() as session:
        existing_ids = set(
            (
                await session.execute(
                    select(SourceRecord.id).where(
                        SourceRecord.source_kind == adapter.SOURCE_KIND
                    )
                )
            ).scalars().all()
        )

    # 3) Run.
    started = time.monotonic()
    try:
        summary = await adapter.ingest_live(
            sessionmaker=sessionmaker,
            salt="phase_8_2f_6_local_broader_expansion",
            accepted_cap=adapter.max_accepted,
        )
    except Exception as e:
        print(f"ERROR: ingest_live raised {type(e).__name__}: {e!r}")
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=adapter.NAME,
            status="review",
            memo_path=adapter.MEMO_PATH,
            approver=None, approved_at=None,
            notes="Phase 8.2F.6 rolled back to review after failure.",
        )
        return 1
    elapsed_s = time.monotonic() - started

    # 4) Read newly-inserted rows.
    async with sessionmaker() as session:
        new_rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == adapter.SOURCE_KIND
                )
            )
        ).scalars().all()
    new_rows = [r for r in new_rows if r.id not in existing_ids]

    accepted_domains = Counter(_domain(r.source_url) for r in new_rows)
    likely_signal = sum(
        1 for r in new_rows
        if (r.metadata_ or {}).get("likely_human_signal_candidate") is True
    )
    by_target_category = Counter(
        (r.metadata_ or {}).get("target_missing_category") or "<none>"
        for r in new_rows
    )
    rejected_codes: Counter[str] = Counter()
    rejected_domains: Counter[str] = Counter()
    for rej in summary.rejection_reasons:
        rejected_codes[rej.reason_code] += 1
        rejected_domains[_domain(rej.source_url)] += 1

    excerpts = []
    for r in new_rows[:2]:
        e = re.sub(r"\s+", " ", (r.content or "")).strip()[:80]
        excerpts.append(e)

    # 5) Re-flip to review.
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="review",
        memo_path=adapter.MEMO_PATH,
        approver=None, approved_at=None,
        notes=(
            "Phase 8.2F.6 broader expansion completed; status reverted "
            "to review. Production approval still requires formal sign-off."
        ),
    )

    # 6) Report.
    print("=" * 64)
    print("Phase 8.2F.6 — Tavily broader human-signal expansion report")
    print("=" * 64)
    print(f"adapter_name:                 {summary.adapter_name}")
    print(f"compliance_status:            {summary.compliance_status}")
    print(f"live_network_used:            {summary.live_network_used}")
    print(f"runtime_seconds:              {elapsed_s:.2f}")
    print()
    print("Queries used (with target_missing_category):")
    for q in queries:
        print(f"  - [{catalog.get(q, '<none>')}] {q}")
    print()
    print(f"fetched_count:                {summary.fetched_count}")
    print(f"accepted_count:               {summary.accepted_count}")
    print(f"  of which likely_human_signal_candidate=true:  {likely_signal}")
    print(f"rejected_count:               {summary.rejected_count}")
    print(f"deduped_count:                {summary.deduped_count}")
    print()
    print("Accepted by target_missing_category:")
    if by_target_category:
        for cat, n in by_target_category.most_common():
            print(f"  - {cat}: {n}")
    else:
        print("  <none>")
    print()
    print("Accepted source domains:")
    if accepted_domains:
        for d, n in accepted_domains.most_common():
            print(f"  - {d}: {n}")
    else:
        print("  <none>")
    print()
    print("Rejected reason codes:")
    if rejected_codes:
        for c, n in rejected_codes.most_common():
            print(f"  - {c}: {n}")
    else:
        print("  <none>")
    print()
    print("Rejected source domains:")
    if rejected_domains:
        for d, n in rejected_domains.most_common():
            print(f"  - {d}: {n}")
    else:
        print("  <none>")
    print()
    print("Two short sanitized excerpts (≤ 80 chars):")
    if excerpts:
        for i, e in enumerate(excerpts, start=1):
            print(f"  [{i}] {e}")
    else:
        print("  <no accepted records>")
    print()
    print("Final compliance status: review (re-flipped after expansion run).")
    print("=" * 64)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
