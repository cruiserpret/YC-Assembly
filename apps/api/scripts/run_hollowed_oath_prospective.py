"""Phase 16A — Hollowed Oath PROSPECTIVE accuracy run.

Locks ONE Assembly market-reaction prediction for the live Hollowed Oath
Kickstarter (an in-development heroic-fantasy MMORPG by God Mode Games) BEFORE the
campaign's outcome is known (campaign closes 2026-06-03; run on 2026-06-01).

This is a PROSPECTIVE forecast target: the brief deliberately EXCLUDES the
in-progress funding tally and the final outcome (no observed data, no leakage),
so the prediction is a cold market-reaction forecast to be scored later (partial,
buyer-anchor) against the realized final pledged total + backer count. The brief
is factual and neutral — it is NOT tuned toward any bucket result.

Run with the cost cap (this is a PAID run; ~$3-6 expected, capped at $12):

    cd apps/api && \
      ASSEMBLY_COST_HARD_USD=12 ASSEMBLY_LIVE_DISCUSSION_BASE_CAP_USD=10 \
      ASSEMBLY_ARTIFACT_ROOT=<stable path> \
      ./.venv/bin/python scripts/run_hollowed_oath_prospective.py
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

# Sources (read-only, public; sourced 2026-06-01): hollowedoath.com,
# massivelyop.com (2026-05-05/07/18/23), mmorpg.com, cogconnected.com,
# kicktraq (live: active, ends 2026-06-03). The brief is the PRODUCT only — NO
# funding tally, NO outcome (anti-leakage).
_BRIEF = {
    "product_name": "Hollowed Oath",
    "product_description": (
        "Hollowed Oath is an in-development heroic-fantasy MMORPG (massively "
        "multiplayer online role-playing game) for PC, currently being funded on "
        "Kickstarter. It is made by God Mode Games, a small indie studio whose team "
        "includes former developers of 'The Heroes Journey', a fan-run EverQuest "
        "emulator server.\n\n"
        "The game is set in Orrathis, a dark-fantasy world abandoned by its gods and "
        "slowly consumed by a reality-warping force called 'the Hollow'. Players are "
        "'Oathsworn' heroes who push back against the Hollow, and their choices about "
        "whom to protect shape the world around them.\n\n"
        "Core design: a flexible multiclass system that lets a player combine three "
        "classes from a roster of sixteen (hundreds of possible build combinations); "
        "combat built around status effects and combo-based ability interactions; "
        "dynamic personal loot; and a 'You Are Enough' philosophy intended to make "
        "both solo and group play viable. The studio markets it as 'an MMORPG that "
        "respects your time'.\n\n"
        "It is a pre-release product: the game is still in development and is not yet "
        "playable at scale. Backers are pledging to fund development in exchange for "
        "founder rewards and future access. It competes for the attention of a "
        "skeptical MMORPG audience that has seen many crowdfunded and indie MMOs "
        "over-promise and under-deliver."
    ),
    "price_or_price_structure": (
        "Crowdfunding pledge model on Kickstarter — no fixed retail price yet. Backers "
        "choose founder/supporter pledge tiers that grant future game access plus "
        "in-game and founder rewards; the studio revamped its pledge tiers and rewards "
        "partway through the campaign. The eventual live game's business model "
        "(subscription, buy-to-play, or free-to-play) is not finalized."
    ),
    "launch_geography": (
        "Global / online — an English-language PC MMORPG crowdfunded via Kickstarter, "
        "with a primarily US and European backer base."
    ),
    "target_customers": [
        "MMORPG players",
        "former EverQuest and old-school MMO fans",
        "indie-MMO crowdfunding backers",
        "theorycrafters who enjoy deep multiclass build systems",
        "solo-friendly MMO players",
        "PC gamers seeking a new persistent-world fantasy RPG",
    ],
    "competitors_or_alternatives": [
        "Pantheon: Rise of the Fallen",
        "Monsters & Memories",
        "Ashes of Creation",
        "classic / emulated EverQuest",
        "established live-service MMORPGs (World of Warcraft, Final Fantasy XIV)",
    ],
    "constraints": [],
    "launch_state": "unlaunched",
    "report_depth": "standard",
    "optional_context": (
        "PROSPECTIVE FORECAST TARGET (Phase 16A). This brief locks an Assembly "
        "market-reaction prediction BEFORE the outcome is known. The product is a "
        "live Kickstarter campaign closing 2026-06-03; the final pledged total and "
        "backer count are NOT yet determined and are deliberately excluded, as is the "
        "in-progress funding tally. Forecast the market's reaction to this MMORPG on "
        "its own merits — do not assume the campaign succeeds or fails. The locked "
        "prediction will later be compared (partial, buyer-anchor only) against the "
        "realized final pledged total and backer count at close."
    ),
}


async def main() -> int:
    sm = get_sessionmaker()
    run_id = uuid.uuid4()
    print(f"[hollowed_oath] starting run_id={run_id}")
    print(
        "[hollowed_oath] product=Hollowed Oath (in-dev heroic-fantasy MMORPG, "
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
    print(f"[hollowed_oath] result: {json.dumps(result, default=str)[:500]}")
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
