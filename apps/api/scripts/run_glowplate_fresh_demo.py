"""Phase 10B.3 — GlowPlate fresh end-to-end run.

Runs the live_founder_brief pipeline against the GlowPlate brief
(smart heated meal plate + USB-C rechargeable warming base) so the
J-criteria from the 10B.3 spec can be verified end-to-end.
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
    "product_name": "GlowPlate",
    "product_description": (
        "GlowPlate is a smart heated meal plate with a "
        "rechargeable warming base. It is not a microwave, not a "
        "hot plate, not a cooking appliance, and not a food warmer "
        "tray.\n\n"
        "The GlowPlate kit includes one removable ceramic plate "
        "and one wireless rechargeable warming base. The plate "
        "is dishwasher-safe; the plate is microwave-safe when "
        "separated from the base. The base uses a USB-C "
        "rechargeable battery and provides a quiet, low-power "
        "warming surface that holds food in the 120°F–145°F range "
        "for up to 45 minutes per session.\n\n"
        "The product is designed for people who eat slowly, work "
        "from home and get pulled into calls during meals, parents "
        "feeding children at staggered times, and anyone whose "
        "food cools faster than they eat. The plate keeps soup, "
        "pasta, rice, stew, and similar foods at a useful "
        "serving temperature without re-cooking. It does not "
        "cook food, does not re-heat food from cold, and is not "
        "an alternative to a microwave."
    ),
    "price_or_price_structure": (
        "$79 for one ceramic plate plus rechargeable warming base. "
        "Two-plate bundle: $139."
    ),
    "launch_geography": (
        "United States, urban + suburban — first wave focused on "
        "remote-worker households, parents of young children, "
        "and slow eaters."
    ),
    "target_customers": [
        "remote workers who get pulled into calls during meals",
        "parents feeding children at staggered times",
        "slow eaters whose food gets cold mid-meal",
        "people without easy microwave access at home or work",
        "WFH professionals",
    ],
    "competitors_or_alternatives": [
        "Ember Mug",
        "Crock-Pot Lunch Crock Food Warmer",
        "HotLogic Mini Portable Oven",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "Core promise: keep your meal at a useful eating "
        "temperature for the whole time you're eating, even when "
        "you're called away. Wireless warming base (USB-C "
        "rechargeable). Removable ceramic plate is dishwasher-safe "
        "and microwave-safe when separated from the base. Holds "
        "120°F–145°F for up to 45 minutes per session. Does not "
        "cook, does not re-heat from cold, does not replace a "
        "microwave."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[glowplate] starting run_id={run_id}")
    print(
        "[glowplate] product=GlowPlate primary_price=$79 bundle=$139 "
        "warming=120-145°F runtime=45min charging=USB-C"
    )
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
    print(f"[glowplate] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
