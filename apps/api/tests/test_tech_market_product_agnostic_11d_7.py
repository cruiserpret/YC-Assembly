"""Phase 11D.7 — product-agnosticism regression tests.

Operator constraint: Assembly must stay product-agnostic. Every
distiller rule must generalize to a broader market signal — not
to a single product (Vivago, Semble), source (Product Hunt, HN),
or category (AI video, code search).

This file exercises every Phase 11D.7 pattern against text from
multiple product categories (B2B workflow SaaS, consumer mobile
app, marketplace, dev tool, AI SaaS, browser extension) to prove
the rules are universal market signals, not product-shaped.

Some patterns are intentionally category-aware (e.g. AI-agent
patterns "wastes tokens", "agent does not trust"). The operator
explicitly allows category-aware patterns. What's NOT allowed is
product-name-specific rules, so this file asserts:

  1. Each universal rule fires across at least two product
     categories.
  2. No rule depends on a specific product name (Vivago, Semble,
     Sora, etc.) to fire.
  3. Category-aware patterns (LLM/agent vocabulary) still
     contribute alongside the universal patterns.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import pytest

from assembly.sources.tech_market_provider import (
    RuleBasedTechMarketDistiller,
)


_distiller = RuleBasedTechMarketDistiller()


def _classify(
    text: str,
    *,
    product_category: str = "unknown",
    market_context_hint: str | None = None,
) -> str | None:
    out = _distiller.distill(
        text,
        source_provider="phase_11d_7_xcat_test",
        product_category=product_category,
        market_context_hint=market_context_hint,
    )
    if not out:
        return None
    return out[0].signal_type


# ---------------------------------------------------------------------------
# pain_urgency — universal across categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category, ctx", [
    # B2B workflow SaaS: "biggest challenge" + universal pain
    (
        "Onboarding new vendors is the biggest challenge in our "
        "team's daily workflow.",
        "b2b_workflow_saas", "B2B",
    ),
    # Consumer mobile app: "falls apart" + universal pain
    (
        "The notification system falls apart after a few hours of "
        "background sync.",
        "consumer_mobile_app", "B2C",
    ),
    # Marketplace: "biggest bottleneck"
    (
        "The biggest bottleneck for our sellers is the slow payout "
        "settlement cycle.",
        "marketplace", "marketplace",
    ),
    # Devtool / AI SaaS — category-aware "wastes tokens" pattern
    # (operator explicitly allowed category-aware patterns)
    (
        "The agent wastes tokens retrying every failed call.",
        "devtool_api", "devtool",
    ),
])
def test_pain_urgency_fires_across_product_categories(
    text: str, category: str, ctx: str,
) -> None:
    assert _classify(
        text, product_category=category, market_context_hint=ctx,
    ) == "pain_urgency"


def test_biggest_challenge_works_across_categories() -> None:
    """`biggest challenge` is a universal English pain idiom — must
    fire on B2B AND consumer products alike."""
    b2b = (
        "Vendor onboarding is the biggest challenge we face when "
        "rolling out the platform to a new region."
    )
    consumer = (
        "The biggest challenge with the daily streak feature is keeping "
        "users engaged past week three."
    )
    assert _classify(b2b, product_category="b2b_workflow_saas") == "pain_urgency"
    assert _classify(consumer, product_category="consumer_mobile_app") == "pain_urgency"


# ---------------------------------------------------------------------------
# developer_skepticism — universal methodology / tech-choice skepticism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS — benchmark methodology
    (
        "Is the benchmark measuring page load time or total "
        "render time?",
        "b2b_workflow_saas",
    ),
    # Marketplace — measurement methodology
    (
        "How do you measure conversion rate when the cohort sizes "
        "vary wildly?",
        "marketplace",
    ),
    # Consumer app — tech-choice skepticism
    (
        "Why write the mobile client in React Native when it would "
        "surely be faster in Swift?",
        "consumer_mobile_app",
    ),
    # AI SaaS — comparative skepticism without code search wording
    (
        "Their accuracy numbers seem too good — show me the "
        "benchmark measuring real-world tasks.",
        "ai_saas",
    ),
])
def test_developer_skepticism_fires_across_product_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "developer_skepticism"


def test_why_write_in_X_works_across_languages_and_categories() -> None:
    """`why write/use/choose X in Y` is universal tech-choice
    skepticism, not just Python-vs-Go on HN."""
    cases = [
        (
            "Why use MongoDB when Postgres would be a better fit?",
            "b2b_workflow_saas",
        ),
        (
            "Why build this on React Native when native iOS would "
            "be smoother?",
            "consumer_mobile_app",
        ),
        (
            "Why choose ElasticSearch when the dataset is this small?",
            "devtool_api",
        ),
    ]
    for text, cat in cases:
        assert _classify(text, product_category=cat) == "developer_skepticism", text


# ---------------------------------------------------------------------------
# workflow_fit — adoption-friction patterns across categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS — pipeline language
    (
        "We tried it in our pipeline but the analytics team did not "
        "trust the results.",
        "b2b_workflow_saas",
    ),
    # Devtool — harness language (broader than code search:
    # "harness" applies to test harnesses, CI harnesses, eval
    # harnesses)
    (
        "Could it be part of the harness so the CI run picks it up "
        "automatically?",
        "devtool_api",
    ),
    # AI SaaS — workflow-fit cue
    (
        "Forces compliance with our editorial review process before "
        "anything goes live.",
        "ai_saas",
    ),
    # Marketplace — "in our pipeline"
    (
        "We wired it into our pipeline and the seller approval flow "
        "got noticeably tighter.",
        "marketplace",
    ),
])
def test_workflow_fit_fires_across_product_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "workflow_fit"


def test_do_not_trust_results_works_across_categories() -> None:
    """`(do/does) not trust the results` is universal across any
    product where end-users have to interpret outputs."""
    cases = [
        (
            "Our analysts do not trust the results when the model "
            "ranks ambiguously.",
            "b2b_workflow_saas",
        ),
        (
            "Users do not trust the results when the timeline shows "
            "negative scores.",
            "consumer_mobile_app",
        ),
        (
            "The team does not trust results from the staging job.",
            "devtool_api",
        ),
    ]
    for text, cat in cases:
        sig = _classify(text, product_category=cat)
        # Either workflow_fit (adoption friction) or pain_urgency
        # (depending on what else is in the text). Both are
        # founder-meaningful; not specific to any product.
        assert sig in {"workflow_fit", "pain_urgency"}, (
            f"{text!r} got {sig!r}"
        )


# ---------------------------------------------------------------------------
# feature_inquiry — universal English question forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS
    (
        "Would this replace our current vendor portal for approval "
        "workflows?",
        "b2b_workflow_saas",
    ),
    # Consumer app
    (
        "Could you add a weekly streak summary email?",
        "consumer_mobile_app",
    ),
    # Marketplace
    (
        "Shouldn't it auto-suspend listings that violate the "
        "category policy?",
        "marketplace",
    ),
    # AI SaaS
    (
        "Does this work for documents that aren't in English?",
        "ai_saas",
    ),
    # Browser extension
    (
        "How many sites can be on the blocklist before performance "
        "drops?",
        "browser_extension",
    ),
])
def test_feature_inquiry_fires_across_product_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "feature_inquiry"


# ---------------------------------------------------------------------------
# integration_friction TIGHTENED — universal patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS — real API breakage
    (
        "The Salesforce API kept failing during the nightly export.",
        "b2b_workflow_saas",
    ),
    # Devtool — SDK breakage
    (
        "Their SDK won't connect to the new auth provider.",
        "devtool_api",
    ),
    # Marketplace — webhook breakage
    (
        "The payout webhook keeps dropping events on Tuesdays.",
        "marketplace",
    ),
])
def test_integration_friction_fires_across_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "integration_friction"


def test_integration_friction_does_not_fire_on_feature_inquiry_about_apis() -> None:
    """The tightening must hold across categories — bare "API" in a
    feature-inquiry context never fires integration_friction."""
    cases = [
        (
            "Does the platform expose a REST API for our analytics?",
            "b2b_workflow_saas",
        ),
        (
            "Are there public API docs for the listing search endpoint?",
            "marketplace",
        ),
        (
            "Can I see what your SDK exposes for custom widgets?",
            "consumer_mobile_app",
        ),
    ]
    for text, cat in cases:
        sig = _classify(text, product_category=cat)
        assert sig != "integration_friction", (
            f"{text!r} ({cat}) wrongly fired integration_friction"
        )


# ---------------------------------------------------------------------------
# onboarding_friction TIGHTENED — universal patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS
    (
        "Setup took two weeks and the tutorial was outdated.",
        "b2b_workflow_saas",
    ),
    # Consumer app
    (
        "The onboarding kept failing on the email-verification step.",
        "consumer_mobile_app",
    ),
    # Marketplace
    (
        "Hard to set up a seller profile without contacting support.",
        "marketplace",
    ),
])
def test_onboarding_friction_fires_across_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "onboarding_friction"


def test_onboarding_friction_does_not_fire_on_setup_as_a_verb() -> None:
    """Across categories, bare "setup" as a noun-recommendation must
    never fire onboarding_friction."""
    cases = [
        ("Setup hooks. Hooks are how your harness forces compliance.", "devtool_api"),
        ("Setup webhook subscribers for high-priority events.", "b2b_workflow_saas"),
        # Note: "Setup a daily reminder..." reads as imperative
        # advice, not friction.
        ("Setup a daily reminder and the streaks feature works fine.", "consumer_mobile_app"),
    ]
    for text, cat in cases:
        sig = _classify(text, product_category=cat)
        assert sig != "onboarding_friction", (
            f"{text!r} ({cat}) wrongly fired onboarding_friction"
        )


# ---------------------------------------------------------------------------
# competitor_comparison — universal patterns, not just hardcoded brands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    # B2B SaaS — generic competitor framing
    (
        "Compared to Salesforce CPQ this is much lighter to roll out.",
        "b2b_workflow_saas",
    ),
    # Consumer app — generic alternative framing
    (
        "I switched from this to a different habit-tracking app last "
        "month because the streak feature was more polished there.",
        "consumer_mobile_app",
    ),
    # Marketplace — generic "instead of" framing for a category,
    # not a brand
    (
        "Listing through a curated marketplace instead of a generic "
        "classifieds site got us better-fit buyers.",
        "marketplace",
    ),
])
def test_competitor_comparison_works_without_brand_hardcoding(
    text: str, category: str,
) -> None:
    """Phase 11D.5/11D.7 add a small AI-tool brand list (Sora,
    Runway, Claude Code, etc.) but the rule must ALSO fire on
    generic comparison framing for products outside that list."""
    sig = _classify(text, product_category=category)
    # Either competitor_comparison (preferred) or switching_objection
    # (for "switched from"). Both are correct buyer-language signals.
    assert sig in {"competitor_comparison", "switching_objection"}, (
        f"{text!r} ({category}) got {sig!r}"
    )


# ---------------------------------------------------------------------------
# switching_objection — universal switching patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, category", [
    (
        "We used to use Asana but moved away to a simpler tool last "
        "quarter.",
        "b2b_workflow_saas",
    ),
    (
        "I have used other habit-tracking apps before, but this one "
        "actually sticks.",
        "consumer_mobile_app",
    ),
    (
        "We came from a different storefront platform and the "
        "switching cost was high.",
        "marketplace",
    ),
])
def test_switching_objection_fires_across_categories(
    text: str, category: str,
) -> None:
    assert _classify(text, product_category=category) == "switching_objection"


# ---------------------------------------------------------------------------
# Product-name independence — no rule depends on Vivago/Semble/etc.
# ---------------------------------------------------------------------------


def test_no_hardcoded_product_names_in_distiller_source() -> None:
    """The distiller's regex source must not contain any reference to
    the specific products in our test corpora (Vivago, Semble) or
    other operator-specific brand names. The competitor_comparison
    rule DOES contain a small list of AI-tool brand names (Sora,
    Runway, etc.) which is intentionally category-aware
    (allowed by operator) — but those are public-domain product
    names representative of a category, not tied to Assembly's
    customer set."""
    from pathlib import Path
    pkg = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "sources" / "tech_market_provider"
        / "distiller.py"
    )
    src = pkg.read_text(encoding="utf-8")
    forbidden_customer_names = (
        "Vivago", "Semble", "VIVA",
        # operator's customer-specific assertions that must NEVER
        # land in production regex code
    )
    for name in forbidden_customer_names:
        assert name not in src, (
            f"distiller.py contains operator-customer name {name!r} — "
            f"every rule must generalize, not hardcode a single "
            f"customer product"
        )


def test_no_overfit_signal_yield_on_product_name_only() -> None:
    """If you swap a product name for a different brand in a
    classified Vivago/Semble text, the classification must NOT
    change. Pins that the rule fires on the BUYER LANGUAGE, not on
    the product name."""
    # Vivago row 4 fires pain_urgency on "failed attempts" + "burned
    # by". Swapping "Sora" for "Pika" or even "OurOwnCompetitor"
    # must keep the classification.
    original = (
        "I fed it my profile pic and a one-line prompt, and it "
        "generated a narrative video where I was the hero. "
        "Considering this was produced with a single still and a "
        "low-effort prompt, I was impressed given all my failed "
        "attempts to get Sora to do my bidding. If you have been "
        "burned by stitching together 4-second clips that do not "
        "cohere."
    )
    swapped = original.replace("Sora", "FicticiousCompetitor")
    assert _classify(original) == _classify(swapped) == "pain_urgency"

    # Semble row 14 fires pain_urgency on "biggest challenge".
    # Swapping "agent" for "user" / "team" / "operator" must not
    # change the class.
    original_2 = (
        "This looks great. I built a tool in the same space, and I "
        "found that the biggest challenge was often to get the "
        "agent to prefer to use the tool over bash tools."
    )
    swapped_2 = original_2.replace("agent", "user").replace(
        "bash tools", "the existing CLI",
    )
    assert _classify(original_2) == _classify(swapped_2) == "pain_urgency"
