"""Phase 10B.4 — PantryPulse fresh end-to-end run for verification."""
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
    "product_name": "PantryPulse",
    "product_description": (
        "PantryPulse is an unlaunched smart kitchen inventory "
        "scanner for renters, busy families, and meal-preppers "
        "who forget what groceries they already have. It is not a "
        "refrigerator, not a grocery delivery service, not a "
        "meal-kit company, and not a full smart-home appliance.\n\n"
        "The starter kit includes one slim magnetic pantry "
        "scanner, one fridge-door scanner, and eight reusable NFC "
        "food tags. The scanners mount with removable adhesive or "
        "magnets and use a tiny wide-angle camera plus barcode/NFC "
        "scanning to help users track what enters and leaves their "
        "kitchen. When a user scans groceries after shopping, "
        "PantryPulse automatically builds a pantry/fridge "
        "inventory, estimates expiration windows, and reminds the "
        "user before food goes bad.\n\n"
        "The product is designed to reduce wasted groceries, "
        "duplicate purchases, and 'I forgot I already had that' "
        "shopping mistakes. It can also suggest simple meal ideas "
        "based only on what is already in the user's pantry or "
        "fridge.\n\n"
        "Privacy is a core part of the product: PantryPulse does "
        "not record video, does not livestream, and does not "
        "identify people. It only captures still images of "
        "shelves/labels during scan events, and users can delete "
        "all images after inventory extraction. The device has a "
        "physical camera shutter and a visible LED when scanning."
    ),
    "price_or_price_structure": (
        "$149 one-time for starter kit: 1 magnetic pantry scanner "
        "+ 1 fridge-door scanner + 8 reusable NFC food tags. "
        "Optional subscription: $7.99/month for PantryPulse Plus. "
        "Accessory: $19.99 for a 12-pack of additional reusable "
        "NFC food tags."
    ),
    "launch_geography": "Austin, Texas metro area",
    "target_customers": [
        "urban renters",
        "busy parents",
        "college students",
        "young professionals",
        "meal-preppers",
        "households that frequently waste groceries",
    ],
    "competitors_or_alternatives": [
        "Samsung Family Hub refrigerator",
        "FridgeCam by Smarter",
        "AnyList grocery list app",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "Privacy facts: tiny wide-angle camera with physical "
        "shutter and visible scan LED. Captures still images "
        "only — does not record video, does not livestream, does "
        "not identify people. Input mechanisms: barcode scanning, "
        "NFC scanning, reusable NFC food tags."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[pantrypulse] starting run_id={run_id}")
    print(
        "[pantrypulse] product=PantryPulse primary=$149 "
        "subscription=$7.99/mo accessory=$19.99 (12-pack tags) "
        "has_camera=true records_video=false"
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
    print(f"[pantrypulse] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
