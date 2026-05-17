"""Phase 11D.1 — synthetic tech-market fixtures.

Hand-curated, deliberately small (≤ ~50 records total). Each entry
is a plausible-looking buyer-language snippet plus a market-context
hint and minimal metadata. The fixtures cover the six operator-spec
product categories:

  1. ai_saas               — AI / LLM SaaS tools
  2. browser_extension     — productivity / privacy / shopping
  3. devtool_api           — developer APIs / SDKs / CLIs
  4. b2b_workflow_saas     — enterprise workflow tools
  5. consumer_mobile_app   — consumer mobile apps
  6. marketplace           — two-sided marketplaces

These are NOT scraped from any real source. They are synthetic
buyer-language patterns the founder team wrote to exercise the
distiller's regex rules. No company name in this file is a target
of any analysis; the company strings (e.g. "Generic AI Co") are
intentionally bland to avoid implying real-product attribution.

Production code MUST NOT call `iter_phase_11d_1_fixtures()` —
the FixtureTechMarketSignalProvider raises if its enable flag is
off. The function is exposed at module level so tests can call it
directly without standing up a provider.
"""
from __future__ import annotations

from collections.abc import Iterable

from assembly.sources.tech_market_provider.signal_types import (
    MarketContext,
)


# Each fixture is (raw_text, market_context_hint, metadata).
# `metadata` carries the scaffolding fields prefixed with
# `_assembly_internal_` so the provider can route them into the
# distilled signal without polluting the published metadata.

_AI_SAAS_FIXTURES: list[tuple[str, MarketContext | None, dict]] = [
    (
        "I'm a developer trying to integrate the Claude API into our "
        "team's docs pipeline — the webhook keeps dropping events and "
        "I can't tell if my SDK version is the problem or the LLM "
        "provider is.",
        "AI_tool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "ai_dev_forum",
            "_assembly_internal_product_category": "ai_saas",
            "_assembly_internal_company_or_product": "Generic AI Co",
            "_assembly_internal_competitor_name": None,
            "_assembly_internal_evidence_url": None,
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "We had to drop this tool because procurement asked for a SOC2 "
        "report and the vendor couldn't produce one in time. The PII "
        "story for prompt logging wasn't great either.",
        "B2B",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "g2_style_review",
            "_assembly_internal_product_category": "ai_saas",
            "_assembly_internal_company_or_product": "Generic AI Co",
            "_assembly_internal_competitor_name": None,
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Honestly I'd happily pay $40/mo for a faster LLM with longer "
        "context — the slow inference time is the real pain right now.",
        "AI_tool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "twitter_style",
            "_assembly_internal_product_category": "ai_saas",
            "_assembly_internal_company_or_product": "Generic AI Co",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "This thing is just a thin wrapper around GPT — Microsoft "
        "could easily replicate it, so I'm not sure it's a real "
        "company versus a feature.",
        "AI_tool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "hn_thread",
            "_assembly_internal_product_category": "ai_saas",
            "_assembly_internal_company_or_product": "Generic AI Co",
            "_assembly_internal_competitor_name": "OpenAI",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "As a YC partner I see a lot of these — most LLM tools end up "
        "as nice-to-have rather than mission critical, and that's the "
        "first to cut when the budget tightens.",
        "AI_tool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "investor_post",
            "_assembly_internal_product_category": "ai_saas",
            "_assembly_internal_company_or_product": "Generic AI Co",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_BROWSER_EXTENSION_FIXTURES: list[
    tuple[str, MarketContext | None, dict]
] = [
    (
        "I use this browser extension every day and the setup was "
        "fine on Chrome, but the Firefox install never finished — "
        "the docs were obviously written for the Chrome version.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "chrome_webstore_review",
            "_assembly_internal_product_category": "browser_extension",
            "_assembly_internal_company_or_product":
                "Generic Shopping Extension",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Stopped using it because the privacy disclosure said the "
        "extension reads every page I visit. Felt sketchy, switched "
        "to a cheaper alternative that only watches checkout pages.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "chrome_webstore_review",
            "_assembly_internal_product_category": "browser_extension",
            "_assembly_internal_company_or_product":
                "Generic Shopping Extension",
            "_assembly_internal_competitor_name":
                "Other Shopping Tool",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Support was terrible — opened a ticket about the API "
        "integration with my budgeting app and nobody replied for "
        "two weeks.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "chrome_webstore_review",
            "_assembly_internal_product_category": "browser_extension",
            "_assembly_internal_company_or_product":
                "Generic Shopping Extension",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "$5/mo feels overpriced for a browser extension that only "
        "blocks impulse buying on three sites. I'd pay if it covered "
        "the whole shopping web.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "twitter_style",
            "_assembly_internal_product_category": "browser_extension",
            "_assembly_internal_company_or_product":
                "Generic Shopping Extension",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_DEVTOOL_API_FIXTURES: list[
    tuple[str, MarketContext | None, dict]
] = [
    (
        "Tried this CLI for our deployment pipeline — the webhook "
        "feature is undocumented and the integration with our existing "
        "GitHub actions broke after the v2 release.",
        "devtool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "github_issue",
            "_assembly_internal_product_category": "devtool_api",
            "_assembly_internal_company_or_product":
                "Generic Devtool",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "The docs were wrong about how the SDK handles retries. "
        "Spent half a day on stack overflow figuring out the actual "
        "behavior — this feels like prototype quality, not "
        "production-ready.",
        "devtool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "hn_thread",
            "_assembly_internal_product_category": "devtool_api",
            "_assembly_internal_company_or_product":
                "Generic Devtool",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Worth every penny — we replaced our home-grown solution "
        "with this CLI and it slotted into our day-to-day workflow "
        "really cleanly.",
        "devtool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "g2_style_review",
            "_assembly_internal_product_category": "devtool_api",
            "_assembly_internal_company_or_product":
                "Generic Devtool",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Their per-seat enterprise pricing is unreasonable for a "
        "devtool. We compared with the open-source alternative and "
        "moved away after a month.",
        "devtool",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "twitter_style",
            "_assembly_internal_product_category": "devtool_api",
            "_assembly_internal_company_or_product":
                "Generic Devtool",
            "_assembly_internal_competitor_name":
                "Open Source Alternative",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_B2B_WORKFLOW_SAAS_FIXTURES: list[
    tuple[str, MarketContext | None, dict]
] = [
    (
        "As a director of operations I signed the contract last year — "
        "the workflow fit our process well, but the SSO setup was "
        "harder than the vendor promised.",
        "B2B",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "g2_style_review",
            "_assembly_internal_product_category": "b2b_workflow_saas",
            "_assembly_internal_company_or_product":
                "Generic Workflow SaaS",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "We're renewing because the procurement and legal review last "
        "year was painful and starting over with another vendor would "
        "be worse than the price hike.",
        "B2B",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "linkedin_post",
            "_assembly_internal_product_category": "b2b_workflow_saas",
            "_assembly_internal_company_or_product":
                "Generic Workflow SaaS",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Tenant admin here — the onboarding was rough. The vendor's "
        "setup wizard for the workspace admin console kept failing "
        "with a permissions error.",
        "B2B",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "g2_style_review",
            "_assembly_internal_product_category": "b2b_workflow_saas",
            "_assembly_internal_company_or_product":
                "Generic Workflow SaaS",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Our finance team blocked the renewal — the price hike on the "
        "per-seat enterprise plan was too steep and they asked us to "
        "evaluate a cheaper alternative.",
        "B2B",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "internal_meeting_notes",
            "_assembly_internal_product_category": "b2b_workflow_saas",
            "_assembly_internal_company_or_product":
                "Generic Workflow SaaS",
            "_assembly_internal_competitor_name":
                "Cheaper Workflow Alternative",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_CONSUMER_MOBILE_APP_FIXTURES: list[
    tuple[str, MarketContext | None, dict]
] = [
    (
        "I use this app every day for tracking my habits — the daily "
        "workflow is smooth and I love the home-screen widget.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "app_store_review",
            "_assembly_internal_product_category":
                "consumer_mobile_app",
            "_assembly_internal_company_or_product":
                "Generic Habit App",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "The onboarding is way too long — I deleted the app after the "
        "fourth tutorial screen.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "app_store_review",
            "_assembly_internal_product_category":
                "consumer_mobile_app",
            "_assembly_internal_company_or_product":
                "Generic Habit App",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Privacy disclosure says they share my data with ad partners. "
        "Switched to a competitor that promises no tracking.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "app_store_review",
            "_assembly_internal_product_category":
                "consumer_mobile_app",
            "_assembly_internal_company_or_product":
                "Generic Habit App",
            "_assembly_internal_competitor_name":
                "No-Tracking Habit App",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "The yearly plan auto-renewed without any obvious warning. "
        "Customer service ghosted me for ten days when I asked for a "
        "refund.",
        "B2C",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "app_store_review",
            "_assembly_internal_product_category":
                "consumer_mobile_app",
            "_assembly_internal_company_or_product":
                "Generic Habit App",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_MARKETPLACE_FIXTURES: list[
    tuple[str, MarketContext | None, dict]
] = [
    (
        "I run a small storefront on this marketplace — the take rate "
        "jumped from 8% to 12% last quarter and a cheaper alternative "
        "is starting to look attractive.",
        "marketplace",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "seller_forum",
            "_assembly_internal_product_category": "marketplace",
            "_assembly_internal_company_or_product":
                "Generic Marketplace",
            "_assembly_internal_competitor_name":
                "Cheaper Marketplace",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "As a buyer on this marketplace I keep getting two-sided "
        "friction — the seller's listing said one thing and the "
        "support ticket I opened sat unanswered for a week.",
        "marketplace",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "trustpilot_style",
            "_assembly_internal_product_category": "marketplace",
            "_assembly_internal_company_or_product":
                "Generic Marketplace",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "The marketplace's seller integration API broke our existing "
        "inventory sync. The developer docs were wrong about which "
        "endpoint to use.",
        "marketplace",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category": "github_issue",
            "_assembly_internal_product_category": "marketplace",
            "_assembly_internal_company_or_product":
                "Generic Marketplace",
            "fixture_set": "phase_11d_1",
        },
    ),
    (
        "Vendor approval for this marketplace was a six-month "
        "procurement nightmare. Worth it because the demand side is "
        "already there — but only just.",
        "marketplace",
        {
            "_assembly_internal_source_provider":
                "tech_market_fixture_synthetic",
            "_assembly_internal_source_category":
                "linkedin_post",
            "_assembly_internal_product_category": "marketplace",
            "_assembly_internal_company_or_product":
                "Generic Marketplace",
            "fixture_set": "phase_11d_1",
        },
    ),
]


_ALL_FIXTURES: tuple[
    list[tuple[str, MarketContext | None, dict]], ...
] = (
    _AI_SAAS_FIXTURES,
    _BROWSER_EXTENSION_FIXTURES,
    _DEVTOOL_API_FIXTURES,
    _B2B_WORKFLOW_SAAS_FIXTURES,
    _CONSUMER_MOBILE_APP_FIXTURES,
    _MARKETPLACE_FIXTURES,
)


def iter_phase_11d_1_fixtures(
) -> Iterable[tuple[str, MarketContext | None, dict]]:
    """Yield every Phase 11D.1 synthetic fixture in source order.

    Pure iteration; no IO. Production code MUST NOT call this
    directly — instead route through `FixtureTechMarketSignalProvider`,
    which enforces the feature-flag gate.
    """
    for bucket in _ALL_FIXTURES:
        yield from bucket


def total_fixture_count() -> int:
    """Cheap helper for tests to assert the fixture corpus stays
    within the documented size budget (Phase 11D.1 ≤ 50 records).
    """
    return sum(len(b) for b in _ALL_FIXTURES)


__all__ = [
    "iter_phase_11d_1_fixtures",
    "total_fixture_count",
]
