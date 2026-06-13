# AMFB-v1 baseline prompt — tomo_endless_blue_onibi_ks_2026

`prompt_hash = sha256:0061256ccdc093f834500a8cdca16788841d9819bf5ad116e0bf2e65e1d3548b`

The identical prompt below is sent to every plain-LLM baseline (GPT / Claude / Gemini). It reveals no Assembly prediction and no outcome.

```
You are forecasting how a market will react to a product. You will be given a frozen product brief and must output a calibrated four-bucket market-reaction prediction.

RULES (read carefully):
- Use ONLY the information in the INPUT BUNDLE below. Do NOT use outside knowledge,
  memory of this specific campaign, the web, or any current/in-progress campaign status.
- Do NOT estimate or infer the current funding amount, backer count, or final outcome.
  Those are deliberately excluded and must not influence your answer.
- Predict the market's reaction to the product ON ITS MERITS, as of the bundle's frozen
  date. Do not assume the campaign succeeds or fails.
- Output ONLY the JSON object specified below — no prose before or after.

INPUT BUNDLE (case_id=tomo_endless_blue_onibi_ks_2026; frozen, pre-outcome):
----------------------------------------
PRODUCT NAME:
Tomo: Endless Blue

PRODUCT DESCRIPTION:
Tomo: Endless Blue is an in-development voxel-based open-world RPG for PC, currently being funded on Kickstarter by the indie studio Onibi. It is marketed as a mash-up of creature-collecting and sandbox building — loosely 'Pokemon meets Minecraft'.

The game is set in the Endless Blue, a vast ocean realm dotted with islands built from the fragments of lost civilizations. The world is populated by creatures called Tomo that act as the player's companions in exploration and real-time combat. A core selling point is local AI generation: each player receives a procedurally generated version of the world — unique villages, NPCs, quests, cultures, and storylines — generated on the player's own device, playable offline.

Core design: capture and train Tomo (using terrain, bait, traps, or combat); a physics-based building system for homes, ships, vehicles, and machines that move, break, and float; real-time action combat that blends fighting with on-the-fly building; and solo-or-co-op play.

It is a pre-release product: the game is still in development and is planned to launch on PC later in the year. Backers are pledging to fund development in exchange for a future digital copy and rewards. It competes for the attention of an audience that has seen many ambitious crowdfunded and early-access games promise large open worlds and under-deliver, and it leans on on-device AI generation, a feature some players are skeptical of.

TARGET CUSTOMERS:
  - creature-collector fans (Pokemon-style players)
  - sandbox / voxel builders (Minecraft / Terraria players)
  - open-world survival-craft and exploration players
  - indie-game crowdfunding backers
  - players interested in procedural / AI-generated worlds
  - co-op PC gamers

PRICE OR PRICE STRUCTURE:
Crowdfunding pledge model on Kickstarter. A pledge of at least roughly $24 (subject to change during the campaign) is stated to include a digital copy of the finished PC game plus backer rewards; the campaign's funding goal was $100,000. The final retail/storefront price and any post-launch monetization are not finalized.

COMPETITORS OR ALTERNATIVES:
  - Minecraft
  - Pokemon series
  - Palworld
  - Terraria
  - Valheim
  - early-access voxel / survival-craft games on Steam

LAUNCH GEOGRAPHY:
Global / online — an English-language PC game crowdfunded via Kickstarter (planned for storefronts such as Steam), with a primarily US and European backer base.

LAUNCH STATE:
unlaunched

CAMPAIGN CONTEXT:
Crowdfunding campaign for a pre-release PC game, scheduled to close 2026-06-20. The game is still in development; backers pledge to fund development in exchange for a future digital copy and rewards. Predict the market's reaction to the product on its own merits — do not assume the campaign succeeds or fails.

CAMPAIGN CLOSE DATE:
2026-06-20
----------------------------------------

Return ONE JSON object and nothing else, with EXACTLY these keys:
{
  "buyer_action_positive": <number 0-100>,   // % who would take the buying/backing action now
  "receptive": <number 0-100>,               // % open/interested but not yet acting
  "uncertain_proof_needed": <number 0-100>,  // % who need more proof before deciding
  "skeptical_resistant": <number 0-100>,     // % skeptical or resistant
  "confidence": <number 0-1>,                // your calibrated self-confidence
  "top_adoption_reasons": [<string>, ...],
  "top_rejection_reasons": [<string>, ...],
  "one_thing_needed": <string>,              // the single thing that would most move the market
  "recommended_segment": <string>,           // the segment most likely to act
  "expected_action_signal": <string>,        // the concrete action you'd expect (e.g. kickstarter_pledge)
  "forecast_notes": <string>
}
The four bucket percentages MUST sum to ~100 (±1.5). If — and only if — you genuinely
cannot produce calibrated four-bucket proportions from the bundle alone, return instead:
{ "schema_failure": true, "schema_failure_reason": <string>, "confidence": <number 0-1> }
(do NOT include the four buckets in a schema_failure response).
```
