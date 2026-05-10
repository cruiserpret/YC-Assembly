"""Phase 10B.2 — ClosetCloud fresh end-to-end run for verification.

Runs the live_founder_brief pipeline against the ClosetCloud
brief (electronic garment-refresh + moisture-control hanger
system) so we can verify all J-criteria from the 10B.2 spec.
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
    "product_name": "ClosetCloud",
    "product_description": (
        "ClosetCloud is a compact electronic garment-refresh and "
        "moisture-control hanger system for small apartments. It "
        "is not a washing machine, not a dryer, not a steamer, and "
        "not a dry-cleaning replacement.\n\n"
        "Each ClosetCloud kit includes one slim wall-mounted "
        "charging rail and three smart hangers. Each hanger has a "
        "quiet micro-fan, a removable activated-carbon odor "
        "filter, and a small humidity sensor. When a user hangs "
        "up lightly worn clothes, gym shirts, jackets, uniforms, "
        "or rain-damp outerwear, the hanger circulates air through "
        "the garment, absorbs odor, and tracks moisture levels "
        "until the item is dry enough to store.\n\n"
        "The product is designed for people who do not have "
        "in-unit laundry or who re-wear clothes between washes. "
        "It is meant to reduce musty closet smell, prevent damp "
        "clothes from sitting in a hamper, and extend the life of "
        "lightly worn garments. It does not use heat, steam, "
        "water, detergent, or UV light.\n\n"
        "The rail plugs into a normal wall outlet. The hangers "
        "charge magnetically when placed on the rail and run "
        "wirelessly for up to 6 hours per cycle. Users can press "
        "one button on the hanger or use the app to choose "
        "'quick refresh,' 'rain-damp dry,' or 'odor reset.' The "
        "app shows humidity status, filter life, and which "
        "garments are still damp."
    ),
    "price_or_price_structure": (
        "$119 for the starter kit:\n"
        "- 1 wall-mounted charging rail\n"
        "- 3 smart garment-refresh hangers\n"
        "- 3 activated-carbon filters\n\n"
        "Replacement filter pack: $14.99 for 6 filters."
    ),
    "launch_geography": (
        "New York City metro area, especially renters in "
        "Manhattan, Brooklyn, Queens, and Jersey City"
    ),
    "target_customers": [
        "urban apartment renters",
        "college students",
        "restaurant workers",
        "nurses",
        "gym-goers",
        "commuters",
    ],
    "competitors_or_alternatives": [
        "LG Styler",
        "Samsung AirDresser",
        "Dryel At-Home Dry Cleaner Kit",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "Core promise: keep lightly worn clothes fresh between "
        "washes, even if your apartment has no laundry room and "
        "no extra closet space. The hangers run wirelessly for "
        "up to 6 hours per cycle and charge magnetically on the "
        "rail. The product does not use heat, steam, water, "
        "detergent, or UV light."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[closetcloud] starting run_id={run_id}")
    print(f"[closetcloud] product=ClosetCloud primary_price=$119 "
          f"accessory=$14.99")
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
    print(f"[closetcloud] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
