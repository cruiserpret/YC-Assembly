"""Phase 8.2E — local-dev Tavily live smoke test runner.

Performs the SINGLE capped live ingest the Phase 8.2E plan describes:

  1. Load TAVILY_API_KEY from .env into the process environment.
  2. Insert/update adapter_compliance_status for tavily_search_extract
     to status='approved' (local-dev only — NOT production approval).
  3. Run one TavilySearchExtractAdapter.ingest_live with the default
     5 commerce-merchant queries and the framework's hard caps.
  4. Print a structured summary — counts, accepted domains, rejected
     domains + reasons, two ≤80-char sanitized excerpts, runtime.
  5. Re-flip the row to status='review'.

This script is operator-only and never expected to run in CI. The key
is read ONLY from env. Nothing in this script's output contains the key.
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
    """Load .env from repo-root or apps/api/. We do NOT print the key."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",                 # apps/api/.env
        here.parent.parent.parent.parent / ".env",   # repo-root/.env
        Path.cwd() / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)


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
    adapter = TavilySearchExtractAdapter()
    queries = list(adapter.DEFAULT_QUERIES)

    # 1) Flip status to approved (local-dev only).
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="phase_8_2e_local_smoke",
        approved_at=datetime.now(UTC),
        notes="Phase 8.2E local-dev smoke test only; NOT production approval.",
    )

    # Capture the inserted source-record IDs by snapshotting before/after.
    async with sessionmaker() as session:
        existing_ids = {
            row.id
            for row in (
                await session.execute(
                    select(SourceRecord.id).where(
                        SourceRecord.source_kind == adapter.SOURCE_KIND
                    )
                )
            ).scalars().all()
        }

    # 2) Run the single live ingest.
    started = time.monotonic()
    try:
        summary = await adapter.ingest_live(
            sessionmaker=sessionmaker,
            salt="phase_8_2e_local_smoke",
            accepted_cap=adapter.MAX_ACCEPTED,
        )
    except Exception as e:  # pragma: no cover  defensive
        print(f"ERROR: ingest_live raised {type(e).__name__}: {e!r}")
        # Still flip back so we never leave the row in approved on failure.
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=adapter.NAME,
            status="review",
            memo_path=adapter.MEMO_PATH,
            approver=None,
            approved_at=None,
            notes="Phase 8.2E rolled back to review after smoke-test failure.",
        )
        return 1
    elapsed_s = time.monotonic() - started

    # 3) Read newly-inserted rows.
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
    rejected_by_code: Counter[str] = Counter()
    rejected_by_domain: Counter[str] = Counter()
    for rej in summary.rejection_reasons:
        rejected_by_code[rej.reason_code] += 1
        rejected_by_domain[_domain(rej.source_url)] += 1

    # 4) Two short sanitized excerpts (≤ 80 chars each, single line).
    excerpts: list[str] = []
    for r in new_rows[:2]:
        e = re.sub(r"\s+", " ", (r.content or "")).strip()[:80]
        excerpts.append(e)

    # 5) Flip back to review.
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="review",
        memo_path=adapter.MEMO_PATH,
        approver=None,
        approved_at=None,
        notes=(
            "Phase 8.2E smoke test completed; status reverted to review. "
            "Production approval still requires formal sign-off."
        ),
    )

    # 6) Print structured report.
    print("=" * 64)
    print("Phase 8.2E — Tavily live smoke-test report")
    print("=" * 64)
    print(f"adapter_name:         {summary.adapter_name}")
    print(f"source_kind:          {summary.source_kind}")
    print(f"compliance_status:    {summary.compliance_status}")
    print(f"live_network_used:    {summary.live_network_used}")
    print(f"runtime_seconds:      {elapsed_s:.2f}")
    print()
    print("Queries used:")
    for q in queries:
        print(f"  - {q}")
    print()
    print(f"fetched_count:        {summary.fetched_count}")
    print(f"accepted_count:       {summary.accepted_count}")
    print(f"rejected_count:       {summary.rejected_count}")
    print(f"deduped_count:        {summary.deduped_count}")
    print()
    print("Accepted source domains (count):")
    if accepted_domains:
        for d, n in accepted_domains.most_common():
            print(f"  - {d}: {n}")
    else:
        print("  <none>")
    print()
    print("Rejected reason codes:")
    if rejected_by_code:
        for c, n in rejected_by_code.most_common():
            print(f"  - {c}: {n}")
    else:
        print("  <none>")
    print()
    print("Rejected source domains:")
    if rejected_by_domain:
        for d, n in rejected_by_domain.most_common():
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
    print("Final compliance status: review (re-flipped after smoke test).")
    print("=" * 64)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
