"""Phase 8.2I.1 — operator-only live refined top-up runner for Amboras.

Calls `execute_topup_loop_live` with `topup_plan_override` set to
`build_amboras_refined_topup_plan()`. Executor:
  1. flips Tavily compliance to approved
  2. runs the 30-query Amboras refined ingest (cap 120 records)
  3. re-flips Tavily compliance to review (always, in finally)
  4. runs persona construction on strong-signal shells only (cap 50)
  5. re-runs Phase 8.2H audience retrieval
  6. returns a structured before/after re-audit

Loads ANTHROPIC_API_KEY + TAVILY_API_KEY from .env. NEVER prints
either key.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


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


async def _amain() -> int:
    _load_env()
    if not os.environ.get("TAVILY_API_KEY"):
        print("ERROR: TAVILY_API_KEY not set after .env load. Aborting.")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set after .env load. Aborting.")
        return 2

    from assembly.db import get_sessionmaker
    from assembly.pipeline.run_scoped_topup import (
        TopUpComplianceCaveatUnresolved,
        TopUpReadinessAlreadySufficient,
        build_amboras_refined_topup_plan,
        execute_topup_loop_live,
        render_run_scoped_topup_summary,
    )
    from assembly.pipeline.target_society import AMBORAS_BRIEF

    sm = get_sessionmaker()
    refined_plan = build_amboras_refined_topup_plan()
    started = time.monotonic()
    try:
        result = await execute_topup_loop_live(
            sessionmaker=sm,
            brief=AMBORAS_BRIEF,
            brief_label="amboras",
            approver_label="phase_8_2i_1_amboras_refined_topup",
            approve_sensitive_topup=False,
            refuse_if_already_ready=False,
            topup_plan_override=refined_plan,
        )
    except TopUpComplianceCaveatUnresolved as e:
        print(f"ERROR: compliance caveat unresolved: {e}")
        return 1
    except TopUpReadinessAlreadySufficient as e:
        print(f"ERROR: top-up not needed: {e}")
        return 1
    elapsed = time.monotonic() - started

    print(render_run_scoped_topup_summary(result))
    print(f"\nTotal runtime: {elapsed:.1f}s")

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / "run_scoped_topup_live_amboras_refined.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n→ Full result JSON: {json_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
