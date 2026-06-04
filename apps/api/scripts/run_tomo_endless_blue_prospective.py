"""Phase 16A — Tomo: Endless Blue PROSPECTIVE accuracy run (second lock).

Locks ONE Assembly market-reaction prediction for the live "Tomo: Endless Blue"
Kickstarter (an in-development voxel open-world RPG by Onibi) BEFORE the campaign's
outcome is known (campaign closes 2026-06-20; run on 2026-06-03).

Same anti-leakage discipline as the first lock: the brief EXCLUDES the in-progress
funding tally and the final outcome (no observed data, no leakage), so the
prediction is a cold market-reaction forecast to be scored later (partial,
buyer-anchor) against the realized final pledged total + backer count. Identical
run config (report_depth=standard, $12 hard / $10 discussion cap) to the Hollowed
Oath lock so the two prospective cases stay comparable. The brief is factual and
neutral — NOT tuned toward any bucket result.

Run with the cost cap (PAID run; ~$3-11 expected, capped at $12):

    cd apps/api && \
      ASSEMBLY_COST_HARD_USD=12 ASSEMBLY_LIVE_DISCUSSION_BASE_CAP_USD=10 \
      ASSEMBLY_ARTIFACT_ROOT=<stable path> \
      ./.venv/bin/python scripts/run_tomo_endless_blue_prospective.py
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

# Sources (read-only, public; sourced 2026-06-03): rpgamer.com, massivelyop.com
# (2026-05-26 / 06-01), gamespress, the Steam page (app 3301510). The brief is the
# PRODUCT only — NO funding tally, NO outcome (anti-leakage).
_BRIEF = {
    "product_name": "Tomo: Endless Blue",
    "product_description": (
        "Tomo: Endless Blue is an in-development voxel-based open-world RPG for PC, "
        "currently being funded on Kickstarter by the indie studio Onibi. It is "
        "marketed as a mash-up of creature-collecting and sandbox building — loosely "
        "'Pokemon meets Minecraft'.\n\n"
        "The game is set in the Endless Blue, a vast ocean realm dotted with islands "
        "built from the fragments of lost civilizations. The world is populated by "
        "creatures called Tomo that act as the player's companions in exploration and "
        "real-time combat. A core selling point is local AI generation: each player "
        "receives a procedurally generated version of the world — unique villages, "
        "NPCs, quests, cultures, and storylines — generated on the player's own device, "
        "playable offline.\n\n"
        "Core design: capture and train Tomo (using terrain, bait, traps, or combat); "
        "a physics-based building system for homes, ships, vehicles, and machines that "
        "move, break, and float; real-time action combat that blends fighting with "
        "on-the-fly building; and solo-or-co-op play.\n\n"
        "It is a pre-release product: the game is still in development and is planned "
        "to launch on PC later in the year. Backers are pledging to fund development "
        "in exchange for a future digital copy and rewards. It competes for the "
        "attention of an audience that has seen many ambitious crowdfunded and "
        "early-access games promise large open worlds and under-deliver, and it leans "
        "on on-device AI generation, a feature some players are skeptical of."
    ),
    "price_or_price_structure": (
        "Crowdfunding pledge model on Kickstarter. A pledge of at least roughly $24 "
        "(subject to change during the campaign) is stated to include a digital copy "
        "of the finished PC game plus backer rewards; the campaign's funding goal was "
        "$100,000. The final retail/storefront price and any post-launch monetization "
        "are not finalized."
    ),
    "launch_geography": (
        "Global / online — an English-language PC game crowdfunded via Kickstarter "
        "(planned for storefronts such as Steam), with a primarily US and European "
        "backer base."
    ),
    "target_customers": [
        "creature-collector fans (Pokemon-style players)",
        "sandbox / voxel builders (Minecraft / Terraria players)",
        "open-world survival-craft and exploration players",
        "indie-game crowdfunding backers",
        "players interested in procedural / AI-generated worlds",
        "co-op PC gamers",
    ],
    "competitors_or_alternatives": [
        "Minecraft",
        "Pokemon series",
        "Palworld",
        "Terraria",
        "Valheim",
        "early-access voxel / survival-craft games on Steam",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "PROSPECTIVE FORECAST TARGET (Phase 16A). This brief locks an Assembly "
        "market-reaction prediction BEFORE the outcome is known. The product is a live "
        "Kickstarter campaign closing 2026-06-20; the final pledged total and backer "
        "count are NOT yet determined and are deliberately excluded, as is the "
        "in-progress funding tally. Forecast the market's reaction to this voxel "
        "open-world RPG on its own merits — do not assume the campaign succeeds or "
        "fails. The locked prediction will later be compared (partial, buyer-anchor "
        "only) against the realized final pledged total and backer count at close."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[tomo_endless_blue] starting run_id={run_id}")
    print(
        "[tomo_endless_blue] product=Tomo: Endless Blue (in-dev voxel open-world RPG, "
        "live Kickstarter, prospective lock — no outcome in brief)"
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
    print(f"[tomo_endless_blue] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
