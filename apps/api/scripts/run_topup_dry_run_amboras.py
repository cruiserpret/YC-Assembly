"""Phase 8.2I — pre-flight dry-run on Amboras.

Builds the run-scoped top-up plan WITHOUT touching Tavily and WITHOUT
writing personas. Prints the plan + safety assertions. The operator
reviews this output and decides whether to run the live loop.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def _amain() -> int:
    from assembly.db import get_sessionmaker
    from assembly.pipeline.run_scoped_topup import (
        execute_topup_loop_dry_run,
        render_run_scoped_topup_summary,
    )
    from assembly.pipeline.target_society import AMBORAS_BRIEF

    sm = get_sessionmaker()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
    )
    print(render_run_scoped_topup_summary(result))

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / "run_scoped_topup_dry_run_amboras.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n→ JSON written to: {json_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
