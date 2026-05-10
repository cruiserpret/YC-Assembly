"""Phase 8.2F — dry-run report against existing Tavily source_records.

Loads every `source_records` row whose `source_kind='tavily_search_extract'`
and runs the persona construction worker in dry-run mode (no extractor,
no LLM calls, no DB writes). Prints a structured per-classification
breakdown plus URL-shape rationale for the strong/weak signal records.

This script is operator-only. It performs NO writes:
  - no persona_records
  - no persona_traits
  - no persona_evidence_links
  - no graph rows
  - no simulation rows
  - no Tavily live calls
  - no LLM calls
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from urllib.parse import urlparse

from sqlalchemy import select


async def _amain() -> int:
    from assembly.db import get_sessionmaker
    from assembly.models.persona import SourceRecord
    from assembly.pipeline.persona_construction import (
        run_persona_construction,
    )
    from assembly.pipeline.persona_construction.source_classifier import (
        SourceClassification,
        classify_source_record,
    )

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.source_kind == "tavily_search_extract"
                )
            )
        ).scalars().all()

    print("=" * 64)
    print("Phase 8.2F — persona construction DRY-RUN")
    print("=" * 64)
    print(f"source_records analyzed:                  {len(rows)}")

    summary = await run_persona_construction(
        sessionmaker=sessionmaker,
        source_records=rows,
        write_personas=False,  # dry-run
    )
    print(f"strong_persona_signal_records:            "
          f"{summary.strong_persona_signal_records}")
    print(f"weak_persona_signal_records:              "
          f"{summary.weak_persona_signal_records}")
    print(f"context_only_records:                     "
          f"{summary.context_only_records}")
    print(f"rejected_records (sensitive/identity):    "
          f"{summary.rejected_records}")
    print(f"candidate persona shells:                 "
          f"{summary.candidate_shells}")
    print(f"shells with would-have-3-valid-traits:    "
          f"{summary.shells_with_three_or_more_valid_traits}")
    print(f"personas_created (dry-run, expected 0):   "
          f"{summary.personas_created}")
    print()

    # Per-record breakdown — show which records went where + why.
    classification_breakdown: Counter[str] = Counter()
    domain_by_class: dict[str, Counter[str]] = {
        c.value: Counter() for c in SourceClassification
    }
    for r in rows:
        report = classify_source_record(
            content=r.content,
            source_url=r.source_url,
            metadata=r.metadata_,
            user_handle_hash=r.user_handle_hash,
        )
        classification_breakdown[report.classification.value] += 1
        domain = (
            urlparse(r.source_url or "").netloc.lower()
            if r.source_url else "<none>"
        )
        if domain.startswith("www."):
            domain = domain[4:]
        domain_by_class[report.classification.value][domain] += 1

    print("Classification breakdown:")
    for c in (
        SourceClassification.STRONG_PERSONA_SIGNAL,
        SourceClassification.WEAK_PERSONA_SIGNAL,
        SourceClassification.CONTEXT_ONLY,
        SourceClassification.REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK,
    ):
        n = classification_breakdown.get(c.value, 0)
        print(f"  {c.value}: {n}")
        if domain_by_class[c.value]:
            for d, k in domain_by_class[c.value].most_common(15):
                print(f"      - {d}: {k}")
    print()

    print("Skipped reasons (would-have-skipped under dry-run):")
    breakdown = summary.reason_breakdown()
    if breakdown:
        for reason, n in breakdown.items():
            print(f"  - {reason}: {n}")
    else:
        print("  <none recorded>")
    print()

    # Quality verdict
    estimated_creatable = summary.shells_with_three_or_more_valid_traits
    print("Verdict:")
    if estimated_creatable == 0:
        print(
            "  No personas would be created from the current 25 records "
            "without a real LLM extractor. Even with one, expect very few "
            "personas given the predominant context_only classification."
        )
    print("  Recommendation: ingest more strong-signal records "
          "(more diverse public commerce-discussion sources) before "
          "running write-mode persona construction.")
    print()

    print("Confirmation:")
    print("  - dry-run wrote no persona_records / persona_traits / "
          "persona_evidence_links")
    print("  - no Tavily live calls were issued")
    print("  - no LLM calls were issued (no extractor configured)")
    print("=" * 64)
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
