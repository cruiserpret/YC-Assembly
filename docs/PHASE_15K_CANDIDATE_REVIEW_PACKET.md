# Phase 15K — First Real Validation Candidate Acquisition (Human Review Packet)

**Status: 8 candidate files created, all `needs_review`. NONE ingested into the
validation ledger. Official dataset unchanged (6 training / 0 holdout / 0 pending).
Phase 15E remains BLOCKED.**

Every figure below was sourced by a 4-agent read-only web workflow **and then
independently re-verified by the operator via a second WebFetch** on the cited
source on 2026-05-31. `claimed_outcome_proportions` is intentionally **null** on
every candidate — the interpretive four-bucket mapping is the **human reviewer's**
job, not the machine's. No proportions were invented.

Candidates live in `apps/api/validation_cases/candidates/` (a `needs_review`
staging area that is **never** loaded as a validation case). Each carries
verified Tier-1/Tier-2 action signals (real counts + source URLs); the reviewer
checklist is left unanswered, so the factory **refuses** to promote any of them
until a human completes the review.

## Why these are useful for Assembly's calibration

Assembly forecasts the *proportion* of a market that is buyer / receptive /
uncertain / skeptical. Crowdfunding, app installs, and OSS adoption give a
**verifiable buyer/action numerator** against a **self-selected denominator** —
the non-buyer buckets are usually NOT observable. The most valuable cases here
are the ones where the action signal **diverged from the realized outcome**
(Coolest Cooler, Humane AI Pin, Clubhouse): they let us test whether a model
over-states the durable-buyer bucket. The batch is deliberately spread across
success, fulfillment-failure, market-rejection, hype-then-decline, and sustained
adoption.

## Candidate table

| candidate_id | product | category | source_type | evidence (Tier-1 verified) | outcome | recommended |
|---|---|---|---|---|---|---|
| `pebble_time_kickstarter_2015` | Pebble Time | consumer_hardware_wearable | kickstarter | 78,471 backers / $20,338,986 (T1) | success → company wound down 2016 | needs_review → training |
| `pebble_original_kickstarter_2012` | Pebble (original) | consumer_hardware_wearable | kickstarter | 68,929 backers / $10,266,845 (T1) | success (record at the time) | needs_review → training |
| `exploding_kittens_kickstarter_2015` | Exploding Kittens | tabletop_card_game | kickstarter | 219,382 backers / $8,782,571 (T1) | success, delivered Sep 2015 | needs_review → training |
| `coolest_cooler_kickstarter_2014` | Coolest Cooler | consumer_hardware_appliance | kickstarter | 62,642 backers / $13,285,226 (T1) + >20,000 unfulfilled (churn) | **fulfillment FAILURE** (shut down 2019) | needs_review → training |
| `vox_machina_kickstarter_2019` | Critical Role: Vox Machina | creator_media_animation | kickstarter | 88,887 backers / $11,385,449 (T1) | success → Amazon series | needs_review → training |
| `clubhouse_app_launch_2021` | Clubhouse | consumer_social_app | app_store | 8.1M downloads by Feb 15 2021 (T1, free) | **hype spike → ~21% decline** | needs_review → training |
| `automatic1111_sdwebui_oss_2022` | AUTOMATIC1111 SD-webui | open_source_ai_tool | github | 30,369 forks (T1) + 163,371 stars (T2) | sustained OSS adoption | needs_review → training |
| `humane_ai_pin_launch_2024` | Humane AI Pin | ai_hardware_wearable | mixed | ~10,000 units shipped (T1) | **market REJECTION** (returns > sales; HP $116M) | needs_review → training |

**Tier-1/Tier-2 count:** all 8 carry a verified Tier-1 action signal (7 are
Tier-1 paid/preorder/install; AUTOMATIC1111 has a Tier-1 fork + Tier-2 star).
**Likely Assembly bucket mapping direction (for the reviewer, NOT pre-filled):**
the verified action count anchors the **buyer/action-positive** bucket; the
reviewer must estimate receptive / uncertain / skeptical, noting that for the
failure cases (Coolest Cooler, Humane, Clubhouse) a meaningful skeptical/resistant
share is implied by the divergent downstream outcome.

## What the human MUST verify before promoting ANY candidate

For each candidate:
1. **Open the cited `source_urls`** and confirm the headline number(s) in
   `action_signal_candidates[].count` and `raw_outcome_evidence` still match.
2. **Decide the four-bucket `claimed_outcome_proportions`** (sum ≈ 100) — this is
   an interpretive judgement; record the rationale in `reviewer_notes`. If the
   non-buyer buckets cannot be defensibly estimated, **do not invent them** —
   keep the candidate as evidence-only or reject it.
3. **Complete the `reviewer_checklist`** (all yes/no questions, a `suitable_for`
   designation, an `evidence_tier`).
4. **Assign `evidence_tier`** (Tier-1 for the paid/preorder/fork actions; treat
   free installs/stars conservatively as Tier-1/2 per the notes).
5. **Choose the designation: TRAINING** (recommended). These are *retrospective*
   cases with *known* outcomes, so they **cannot be a clean holdout** — the
   factory's anti-leakage gate will block holdout. Use them as training data, or
   reject if the bucket mapping is not defensible.
6. **Re-confirm low legal/privacy risk** (all sources here are public press /
   Wikipedia / official APIs).

## Exact commands to promote later (only after human review)

```bash
cd apps/api
# 1. (edit the candidate file: fill reviewer_checklist, evidence_tier, and
#     claimed_outcome_proportions with your reviewed four-bucket mapping)
# 2. approve (runs every hard gate; dry-run first)
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training --dry-run
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training
# 3. ingest into training_cases.json (dry-run shows the exact case first)
python scripts/phase_15j_candidate_factory.py ingest --id <candidate_id> --dry-run
python scripts/phase_15j_candidate_factory.py ingest --id <candidate_id>
# reject instead:
python scripts/phase_15j_candidate_factory.py reject --id <candidate_id> --reason "<why>"
```

## Dropped during re-verification (NOT created — moved to sourcing plan)

The operator's independent re-fetch did **not** reconcile these, so no candidate
file was created (no fabricated numbers):

- **Quibi** — subscriber count conflicts across sources (Wikipedia "~500,000" vs
  Variety "710,000, down from 1.1M"; plus trial-vs-paid ambiguity). Needs a
  single authoritative figure before use.
- **BrewDog "Equity for Punks"** — the £75M / 200,000-investor figure (from The
  Conversation) was **not corroborated** by the Wikipedia BrewDog article (which
  only shows a 2011 £2M raise). Needs a primary/official source.

## Sourcing plan — next candidates to verify (evidence still needed)

These appeared in the workflow but lacked a fetchable primary source or a clean
count; each needs the listed evidence before a candidate file is created:

- **Glowforge** ($27.9M preorders, 2015) — needs a fetchable primary/official
  source (GeekWire/Seattle Times 403'd); needs a backer/unit count.
- **Flow Hive** (Indiegogo, 2015) — final total (~$12–13.2M / ~37,000 orders)
  needs primary confirmation (only an interim $3.5M snapshot was fetchable).
- **Ouya** (2012) — dollar total verified ($8.6M) but the **backer count**
  (~63,416) needs a primary source.
- **Frosthaven / Kingdom Death: Monster** — verified only via the third-party
  Kicktraq scraper; confirm against the primary Kickstarter / BackerKit.
- **Notion** (~4M paying customers / $400M ARR) — only third-party aggregators;
  needs an official Notion figure.
- **Robinhood** (~1M pre-launch waitlist) — only growth-marketing blogs; needs a
  primary/press confirmation.
- **Amazon Fire Phone** — strong market-rejection signal ($170M write-down,
  $83M unsold) but **no clean buyer count**; usable only as a directional case.
- **Rabbit R1** (~100k buyers vs ~5k DAU) — daily-active verified; the 100k buyer
  count needs verbatim confirmation.
- **Substack / Superhuman / Threads / ChatGPT** — verified headline numbers but
  Tier-2 (free signup / waitlist / star) or platform-aggregate; admit only as
  flagged semi-action contrast cases, not Tier-1 buyer anchors.

## Guardrails honored

No paid simulation, no product/runtime LLM call, no fabricated case, no
unreviewed ingestion, no append to `seed_cases.json`, no calibration, no forecast
change, no Phase 13 / token-system change, no accuracy claim. Official ledger
remains 6 training / 0 holdout / 0 pending; candidates are isolated and unloaded.
