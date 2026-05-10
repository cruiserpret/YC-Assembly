"""Phase 8.2I.1 — pre-flight dry-run on the Amboras refined query catalog.

Builds the refined plan and runs the executor's dry-run path. NO
Tavily call. NO persona writes.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


async def _amain() -> int:
    from assembly.db import get_sessionmaker
    from assembly.pipeline.run_scoped_topup import (
        build_amboras_refined_topup_plan,
        execute_topup_loop_dry_run,
        render_run_scoped_topup_summary,
    )
    from assembly.pipeline.target_society import AMBORAS_BRIEF

    sm = get_sessionmaker()
    refined = build_amboras_refined_topup_plan()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
        topup_plan_override=refined,
    )
    print(render_run_scoped_topup_summary(result))

    out_dir = Path(__file__).resolve().parent.parent / "_audit"
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / "run_scoped_topup_dry_run_amboras_refined.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n→ JSON: {json_path}")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
