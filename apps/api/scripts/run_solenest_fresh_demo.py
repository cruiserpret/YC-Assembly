"""Phase 10B.1 — SoleNest fresh end-to-end run for verification.

Runs the live_founder_brief pipeline against the SoleNest brief
(electronic shoe-drying + odor-control dock) so we can verify all
J-criteria from the 10B.1 spec on real LLM output.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

from assembly.db import get_sessionmaker
from assembly.models.assembly_run import AssemblyRun
from assembly.orchestration import run_live_founder_brief_pipeline


_BRIEF = {
    "product_name": "SoleNest",
    "product_description": (
        "An electronic shoe-drying and odor-control dock with two "
        "magnetic drying pods. The pods slip into wet or sweaty "
        "shoes; the dock charges them wirelessly. Features include "
        "gentle warm airflow, moisture sensing, timed UV-C "
        "sanitation, quiet night mode (under 30 dB), and an LED "
        "dryness indicator. Designed for small apartments, dorms, "
        "gyms, and rainy cities."
    ),
    "price_or_price_structure": (
        "$69.99 starter dock with two drying pods; "
        "$24.99 replacement pod pair"
    ),
    "launch_geography": "Seattle, Washington metro",
    "target_customers": [
        "urban commuters",
        "gym-goers",
        "college students in dorms",
        "parents of active kids",
    ],
    "competitors_or_alternatives": [
        "PEET Original Electric Shoe and Boot Dryer",
        "DryGuy Force Dry",
        "SteriShoe UV Shoe Sanitizer",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "Two magnetic drying pods. The pods charge wirelessly in "
        "the dock. Quiet night mode is below 30 dB."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[solenest] starting run_id={run_id}")
    print(f"[solenest] product=SoleNest price=$69.99")
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
    result = await run_live_founder_brief_pipeline(run_id)
    print(f"[solenest] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
