"""Phase 8.2F.5 — operator-only Tavily human-signal expansion runner.

Performs ONE capped live ingest of human-signal-focused queries:

  - 10 review/forum/comment-targeted queries (HUMAN_SIGNAL_QUERIES)
  - max 10 results per query
  - max 75 accepted source_records
  - max 4000 chars per record
  - 30s timeout
  - operator_run=True / test_fixture=False on every accepted row
  - run_purpose='phase_8_2f_5_human_signal_expansion'
  - likely_human_signal_candidate flag set per result

Does NOT:
  - call any non-Tavily live API
  - create persona_records / persona_traits / persona_evidence_links
  - build social graph / clusters / simulation rows
  - run the Phase 8.2F write-mode worker

Like the Phase 8.2E smoke test, this script flips the Tavily compliance
status to `approved` (local-dev only), runs ONE ingest, then re-flips
to `review`. The TAVILY_API_KEY is read only via os.environ and is
NEVER printed.
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
    adapter = TavilySearchExtractAdapter.for_human_signal_expansion()
    queries = list(adapter._queries)

    # 1) Approve.
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="approved",
        memo_path=adapter.MEMO_PATH,
        approver="phase_8_2f_5_local_expansion",
        approved_at=datetime.now(UTC),
        notes="Phase 8.2F.5 human-signal expansion; local dev only.",
    )

    # 2) Snapshot pre-existing IDs so we report the delta only.
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

    # 3) Run.
    started = time.monotonic()
    try:
        summary = await adapter.ingest_live(
            sessionmaker=sessionmaker,
            salt="phase_8_2f_5_local_expansion",
            accepted_cap=adapter.max_accepted,
        )
    except Exception as e:
        print(f"ERROR: ingest_live raised {type(e).__name__}: {e!r}")
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=adapter.NAME,
            status="review",
            memo_path=adapter.MEMO_PATH,
            approver=None,
            approved_at=None,
            notes="Phase 8.2F.5 rolled back to review after expansion failure.",
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
    rejected_codes: Counter[str] = Counter()
    rejected_domains: Counter[str] = Counter()
    for rej in summary.rejection_reasons:
        rejected_codes[rej.reason_code] += 1
        rejected_domains[_domain(rej.source_url)] += 1

    # Two short sanitized excerpts (≤ 80 chars each).
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
        approver=None,
        approved_at=None,
        notes=(
            "Phase 8.2F.5 expansion run completed; status reverted to "
            "review. Production approval still requires formal sign-off."
        ),
    )

    # 6) Report.
    print("=" * 64)
    print("Phase 8.2F.5 — Tavily human-signal expansion report")
    print("=" * 64)
    print(f"adapter_name:                 {summary.adapter_name}")
    print(f"compliance_status:            {summary.compliance_status}")
    print(f"live_network_used:            {summary.live_network_used}")
    print(f"runtime_seconds:              {elapsed_s:.2f}")
    print()
    print("Queries used:")
    for q in queries:
        print(f"  - {q}")
    print()
    print(f"fetched_count:                {summary.fetched_count}")
    print(f"accepted_count:               {summary.accepted_count}")
    print(f"  of which likely_human_signal_candidate=true:  {likely_signal}")
    print(f"rejected_count:               {summary.rejected_count}")
    print(f"deduped_count:                {summary.deduped_count}")
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
