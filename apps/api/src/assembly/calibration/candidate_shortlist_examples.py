"""Phase 12A.3 — Preliminary, UNVERIFIED candidate shortlist.

This is a deliberately empty-of-facts placeholder file. Each entry
below describes a *category slot* the operator may want to fill
with a real product/startup for blinded validation. **No real
product names, no hidden outcomes, no raw comments, no URLs.** The
operator supplies those later in a dedicated phase, with explicit
authorization.

Honesty rule (Phase 12A.3 spec):
  > "Do not fabricate real candidate facts. If a candidate cannot
  > be verified from already available local/operator-provided
  > information, mark it as 'unverified_candidate' and ask for
  > operator-supplied links/data later."

Every slot in this file therefore:

  - has ``product_name`` set to a slot-id placeholder (no real
    company name), so a quick grep finds nothing fabricated
  - has ``operator_recommendation = None`` (computed → "unverified")
  - has ``outcome_quality = "unknown"`` and
    ``estimated_observation_count = "unknown"`` until operator fills
  - has ``cutoff_clarity = "unclear"`` until operator fills
  - has ``pre_launch_sources_available`` and
    ``outcome_sources_available`` populated only with the *types* of
    sources we'd want to see for that category (no URLs or
    specific corpora)

The recommender will mark every slot as ``"unverified"`` until the
operator fills in concrete metadata. That is the intended behavior
for Phase 12A.3.

The five category slots mirror the categories called out in the
Phase 12A.3 spec:

  1. Product Hunt AI/SaaS launch
  2. Hacker News devtool launch
  3. App Store or Chrome extension product
  4. B2B SaaS / prosumer tool with public reviews
  5. Consumer product with review data
"""
from __future__ import annotations

from assembly.calibration.case_candidate_selection import CaseCandidate


def preliminary_unverified_shortlist() -> list[CaseCandidate]:
    """Return the five category-slot placeholders.

    These are NOT real candidates yet. They are slots that the
    operator must fill, replacing ``product_name`` with a real
    product chosen against the contamination + model-prior rules
    in :mod:`assembly.calibration.case_candidate_selection`, and
    filling in the cutoff date, observation count, and outcome
    source access.
    """
    return [
        CaseCandidate(
            candidate_id="slot_product_hunt_ai_saas_a",
            product_name="[OPERATOR_TO_SUPPLY: Product Hunt AI/SaaS launch]",
            category="AI SaaS tool",
            launch_or_cutoff_date=None,
            pre_launch_sources_available=[
                "product_hunt_launch_page_text",
                "founder_announcement_thread",
            ],
            outcome_sources_available=[
                "product_hunt_comments",
                "twitter_x_reactions",
                "operator_supplied_user_feedback",
            ],
            estimated_observation_count="unknown",
            contamination_risk="low",
            model_prior_risk="medium",
            outcome_quality="unknown",
            cutoff_clarity="unclear",
            category_fit="strong",
            source_access_risk="operator_supply",
            notes=(
                "Operator must select a specific Product Hunt launch "
                "from 2023-2024 with: (a) a clear launch date, (b) at "
                "least ~50 substantive comments (not pure 'congrats' "
                "noise), (c) not already used to develop Assembly's "
                "tech-market signal layers (the framework auto-flags "
                "known-contaminated product names). Prefer niche AI "
                "tools rather than mega-famous ones."
            ),
        ),
        CaseCandidate(
            candidate_id="slot_hacker_news_devtool_a",
            product_name="[OPERATOR_TO_SUPPLY: HN-launched devtool]",
            category="developer tool",
            launch_or_cutoff_date=None,
            pre_launch_sources_available=[
                "show_hn_thread_text",
                "project_readme_or_landing_page_at_launch",
            ],
            outcome_sources_available=[
                "show_hn_comment_thread_after_top",
                "github_stars_trajectory",
                "twitter_x_developer_reactions",
            ],
            estimated_observation_count="unknown",
            contamination_risk="low",
            model_prior_risk="medium",
            outcome_quality="unknown",
            cutoff_clarity="unclear",
            category_fit="strong",
            source_access_risk="operator_supply",
            notes=(
                "Operator must pick a Show-HN thread that has >50 "
                "comments AND a meaningful split of buyer/receptive/"
                "skeptical reactions. Avoid devtools where the entire "
                "thread is congratulatory."
            ),
        ),
        CaseCandidate(
            candidate_id="slot_chrome_extension_or_app_store_a",
            product_name="[OPERATOR_TO_SUPPLY: Chrome extension or App Store product]",
            category="consumer mobile or browser extension",
            launch_or_cutoff_date=None,
            pre_launch_sources_available=[
                "store_listing_description_at_launch",
                "founder_blog_announcement_post",
            ],
            outcome_sources_available=[
                "store_review_text_after_launch",
                "reddit_reaction_threads",
            ],
            estimated_observation_count="unknown",
            contamination_risk="low",
            model_prior_risk="low",
            outcome_quality="unknown",
            cutoff_clarity="unclear",
            category_fit="medium",
            source_access_risk="operator_supply",
            notes=(
                "Store reviews are usually obtainable via operator-"
                "supplied export; do NOT scrape store pages. Avoid "
                "any extension whose review count is <30."
            ),
        ),
        CaseCandidate(
            candidate_id="slot_b2b_prosumer_tool_a",
            product_name="[OPERATOR_TO_SUPPLY: B2B SaaS / prosumer tool with public reviews]",
            category="B2B SaaS",
            launch_or_cutoff_date=None,
            pre_launch_sources_available=[
                "launch_post_or_pricing_page_at_launch",
            ],
            outcome_sources_available=[
                "g2_or_capterra_review_text",
                "subreddit_or_slack_community_reactions",
            ],
            estimated_observation_count="unknown",
            contamination_risk="low",
            model_prior_risk="medium",
            outcome_quality="unknown",
            cutoff_clarity="unclear",
            category_fit="medium",
            source_access_risk="operator_supply",
            notes=(
                "Prefer tools with >100 review-style reactions. Avoid "
                "tools whose reviews are dominated by paid "
                "promotional posts."
            ),
        ),
        CaseCandidate(
            candidate_id="slot_consumer_product_with_reviews_a",
            product_name="[OPERATOR_TO_SUPPLY: Consumer product with review data]",
            category="consumer product",
            launch_or_cutoff_date=None,
            pre_launch_sources_available=[
                "product_launch_announcement",
                "press_release_at_launch",
            ],
            outcome_sources_available=[
                "retailer_review_text",
                "reddit_or_youtube_first_impressions",
            ],
            estimated_observation_count="unknown",
            contamination_risk="low",
            model_prior_risk="medium",
            outcome_quality="unknown",
            cutoff_clarity="unclear",
            category_fit="weak",
            source_access_risk="operator_supply",
            notes=(
                "Consumer reviews are heaviest source of buyer/"
                "receptive/uncertain/skeptical signal. Avoid mega-"
                "famous brands (model prior risk = high). Prefer a "
                "mid-tier launch within the last 12-18 months."
            ),
        ),
    ]
