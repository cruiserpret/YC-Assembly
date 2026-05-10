"""Phase 10A.3 — final verification checkpoint.

Runs a brand-new fresh end-to-end live_founder_brief pipeline against
the fictional product AquaSnap. Used to confirm that a from-scratch
fresh run emits every Phase 10A.3 artifact (persona_quality_gates,
final_ballot_repair, fresh_live_artifact_wording_audit,
user_facing_language_audit) on a clean run.

Usage:
    .venv/bin/python scripts/run_aquasnap_fresh_demo.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun
from assembly.orchestration import run_live_founder_brief_pipeline


_BRIEF = {
    "product_name": "AquaSnap",
    "product_description": (
        "A reusable magnetic clip-on hydration reminder for office "
        "workers and students. Attaches to water bottles and glows "
        "softly when it is time to drink."
    ),
    "price_or_price_structure": "$24",
    "launch_geography": "United States",
    "target_customers": [
        "office workers",
        "college students",
        "hybrid-remote desk workers",
    ],
    "competitors_or_alternatives": [
        "Hidrate Spark",
        "Ulla water bottle reminder",
        "phone reminder apps",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": "",
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[aquasnap] starting fresh run_id={run_id}")
    print(f"[aquasnap] product={_BRIEF['product_name']} "
          f"price={_BRIEF['price_or_price_structure']}")

    # Create the AssemblyRun row (mirrors what POST /assembly/runs does)
    now = datetime.now(UTC)
    async with sm() as session:
        async with session.begin():
            session.add(AssemblyRun(
                id=run_id,
                user_id=None,
                mode="live_founder_brief",
                product_brief=_BRIEF,
                status="running",
                current_stage="validating_brief",
                stage_progress={},
                artifact_manifest={},
                error_message=None,
                linked_run_scope_id=None,
                updated_at=now,
            ))

    # Walk the 13-stage pipeline synchronously (default fresh mode,
    # no _dev_reuse_existing_society)
    result = await run_live_founder_brief_pipeline(run_id)
    print(f"[aquasnap] pipeline result: {json.dumps(result, default=str)[:400]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
