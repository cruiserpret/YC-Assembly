"""Phase 11C.7 — brand/subcategory-anchored Amazon retrieval tests.

Phase 11C.6's relevance scorer worked, but real McAuley category-only
retrieval surfaces too much noise (games inside Software, sponges
inside Health_and_Personal_Care) — at threshold 0.20 nearly every
candidate gets filtered out. Phase 11C.7 widens the candidate pool
BEFORE relevance scoring, pulling from four anchored pools instead of
the previous category-only pull.

This file pins:

  1. SignalSource Protocol exposes the two new query surfaces.
  2. InMemorySignalSource implements title-keyword + brand-substring.
  3. Title-keyword extraction respects the category preset + brief.
  4. Browser-extension brief pulls software/browser/extension snippets.
  5. Wellness-wearable brief pulls wearable/stress/sensor snippets.
  6. Competitor names anchor the brand pool via substring.
  7. Off-topic games downrank for QuietCart-style briefs.
  8. Off-topic sponges/bottles downrank for CalmCue-style briefs.
  9. Candidate-pool expansion beats Phase-11C.6 category-only retrieval
     in kept-signal count for niche briefs.
 10. same_category_only invariant still holds across all four pools.
 11. Final prompt cap (12 snippets, 2 per bucket) still enforced.
 12. All three persona-injection gates still required.
 13. Audit dict exposes per-pool counts + matched brands +
     title_keywords_used + fallback_used.
 14. Forbidden fields never leak — RetrievedSignal contract preserved.
 15. Drift: only the whitelisted injector touches the retriever.
 16. PostgresSignalSource has the new two methods (type-check only).

NO LIVE LLM, NO LIVE NETWORK, NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from assembly.sources.amazon_reviews_provider import (
    AmazonSignalRetriever,
    CandidatePoolStats,
    InMemorySignalSource,
    ProductBriefShape,
    RetrievalConfig,
    RetrievedSignal,
    SignalRow,
    SignalSource,
)
from assembly.sources.amazon_reviews_provider.retrieval import (
    _TITLE_KEYWORD_HINTS,
    _extract_title_keywords,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_QUIETCART_BRIEF = {
    "product_name": "QuietCart",
    "product_description": (
        "A browser extension for online shoppers who want to stop "
        "impulse buying on Amazon and other shopping sites. Adds a "
        "delay and privacy-focused review of carts before checkout."
    ),
    "category_hint": "browser extension",
    "competitors_or_alternatives": ["Freedom", "Opal", "Rocket Money"],
}

_CALMCUE_BRIEF = {
    "product_name": "CalmCue stress wearable",
    "product_description": (
        "A screenless wellness wearable that tracks stress and sleep "
        "via passive skin sensors. No mobile app required."
    ),
    "category_hint": "wellness wearable",
    "competitors_or_alternatives": ["Apollo Neuro", "Muse", "Whoop"],
}


_QUIETCART_SHAPE = ProductBriefShape(
    product_name=str(_QUIETCART_BRIEF["product_name"]),
    description=str(_QUIETCART_BRIEF["product_description"]),
    category_hint=str(_QUIETCART_BRIEF["category_hint"]),
    competitors=tuple(_QUIETCART_BRIEF["competitors_or_alternatives"]),
)
_CALMCUE_SHAPE = ProductBriefShape(
    product_name=str(_CALMCUE_BRIEF["product_name"]),
    description=str(_CALMCUE_BRIEF["product_description"]),
    category_hint=str(_CALMCUE_BRIEF["category_hint"]),
    competitors=tuple(_CALMCUE_BRIEF["competitors_or_alternatives"]),
)


class _SettingsAllOn:
    amazon_reviews_enabled = True
    amazon_reviews_runtime_enabled = True
    amazon_reviews_persona_injection_enabled = True
    amazon_reviews_same_category_only = True
    amazon_reviews_persona_min_relevance = 0.20
    amazon_reviews_max_signals_per_run = 80
    amazon_reviews_max_signals_per_category = 40
    amazon_reviews_max_signals_per_competitor = 20
    amazon_reviews_max_signals_per_brand = 8
    amazon_reviews_max_signals_per_theme = 10


def _exploding_sessionmaker(*a: Any, **kw: Any) -> Any:  # pragma: no cover
    raise AssertionError("DB session opened despite flags off")


def _enabled_cfg() -> RetrievalConfig:
    return RetrievalConfig(
        enabled=True,
        runtime_enabled=True,
        same_category_only=True,
        persona_injection_enabled=True,
        max_signals_per_run=80,
        max_signals_per_category=40,
        max_signals_per_competitor=20,
        max_signals_per_brand=8,
        max_signals_per_theme=10,
    )


def _row(
    *,
    signal_type: str = "objection",
    sentiment: str = "negative",
    theme: str | None = "couldnt_setup",
    category: str = "Software",
    brand: str | None = "BrandX",
    title: str | None = "Generic Title",
    asin: str = "B000000001",
    parent_asin: str = "B000000001",
    rating: int | None = 2,
    verified: bool | None = True,
    helpful: int | None = 3,
    snippet: str = "this was difficult to set up",
    competitor: str | None = None,
    use_case: str | None = None,
    review_hash: str | None = None,
) -> SignalRow:
    # Generate a unique hash when not provided so dedup doesn't
    # collapse rows whose snippets happen to match.
    if review_hash is None:
        review_hash = f"h_{abs(hash((title, snippet, brand))) % 10_000_000}"
    return SignalRow(
        signal_type=signal_type,
        sentiment_bucket=sentiment,
        theme=theme,
        category=category,
        brand=brand,
        product_title=title,
        asin=asin,
        parent_asin=parent_asin,
        rating=rating,
        verified_purchase=verified,
        helpful_votes=helpful,
        short_snippet=snippet,
        competitor_mention=competitor,
        use_case=use_case,
        source_review_hash=review_hash,
    )


def _software_corpus() -> list[SignalRow]:
    """Synthetic Software-category corpus that mirrors the real
    McAuley shape — a mix of genuinely browser-extension-shaped
    rows, ambient app/SaaS rows, and game/antivirus noise.

    Snippets are intentionally distinct in their first 96 chars so
    the retriever's snippet-collision dedup doesn't collapse rows
    in a single signal_type bucket.
    """
    rows: list[SignalRow] = []

    # Objection bucket — 4 distinct browser-extension setup snippets.
    obj_snippets = [
        (
            "tried installing the browser extension on Chrome and the "
            "permissions dialog never finished loading for me as an "
            "online shopper"
        ),
        (
            "Firefox version of this browser extension keeps disabling "
            "itself every time I close my impulse-buying shopping tabs"
        ),
        (
            "extension worked on desktop but the mobile browser app does "
            "not actually block any shopping sites for impulse purchases"
        ),
        (
            "uninstalled because the browser extension blocked legitimate "
            "checkout pages on Amazon that I needed for routine groceries"
        ),
    ]
    for i, sn in enumerate(obj_snippets):
        rows.append(_row(
            signal_type="objection",
            theme=f"setup_issue_{i}",
            category="Software",
            brand=f"ExtVendor{i}",
            title=f"QuietBrowser Extension v{i} for online shoppers",
            snippet=sn,
            rating=2, helpful=5,
        ))

    # Trust bucket — 4 distinct privacy concern snippets.
    trust_snippets = [
        (
            "asks for full access to every shopping page I visit which "
            "feels too invasive for a privacy-focused browser extension"
        ),
        (
            "extension's privacy policy is unclear about whether it sells "
            "my checkout history to advertisers as an online shopper"
        ),
        (
            "had to give credit card info just to enable the basic "
            "impulse-blocking shopping feature which felt scammy to me"
        ),
        (
            "the browser extension flagged my own bank login as a phishing "
            "shopping site which makes me question its trust signals"
        ),
    ]
    for i, sn in enumerate(trust_snippets):
        rows.append(_row(
            signal_type="trust",
            theme=f"privacy_concern_{i}",
            category="Software",
            brand=f"PrivacyApp{i}",
            title=f"PrivacyApp browser extension {i}",
            snippet=sn,
            rating=1, helpful=10,
        ))

    # Setup bucket — 4 distinct onboarding snippets.
    setup_snippets = [
        (
            "took me an hour to figure out which browser extension button "
            "actually enables the impulse-buying blocker for shopping"
        ),
        (
            "the setup wizard for this browser extension never asked me "
            "which shopping sites I wanted to block for impulse control"
        ),
        (
            "extension installed cleanly but no obvious way to whitelist "
            "the online shopping sites I actually want to visit normally"
        ),
        (
            "documentation for setting up the browser extension impulse "
            "blocker is hidden behind a paywalled support portal page"
        ),
    ]
    for i, sn in enumerate(setup_snippets):
        rows.append(_row(
            signal_type="setup",
            theme=f"onboarding_friction_{i}",
            category="Software",
            brand=f"SetupCo{i}",
            title=f"OnboardEasy browser extension {i} for shoppers",
            snippet=sn,
            rating=2, helpful=4,
        ))

    # Price bucket — 4 distinct subscription snippets.
    price_snippets = [
        (
            "five dollars a month feels steep for a browser extension that "
            "only blocks impulse buying on a handful of shopping sites"
        ),
        (
            "the free tier of this browser extension only lets you block "
            "two shopping sites which is not enough for online shoppers"
        ),
        (
            "had no idea the browser extension subscription would jump to "
            "yearly billing after the first month of impulse blocking"
        ),
        (
            "cancelling the subscription left the browser extension still "
            "installed and silently watching every shopping checkout page"
        ),
    ]
    for i, sn in enumerate(price_snippets):
        rows.append(_row(
            signal_type="price",
            theme=f"pricing_friction_{i}",
            category="Software",
            brand=f"PriceCo{i}",
            title=f"BudgetGuard browser extension {i} for shoppers",
            snippet=sn,
            rating=2, helpful=3,
        ))

    # Competitor-anchored rows (Freedom) — 3 distinct switch_reason.
    freedom_snippets = [
        (
            "switched from this browser extension to Freedom because "
            "Freedom actually blocks shopping sites during work hours"
        ),
        (
            "Freedom blocked all my shopping sites including amazon "
            "browser extension never reliably caught impulse purchases"
        ),
        (
            "after a month I went back to Freedom because their browser "
            "extension covers more shopping sites with stricter blocking"
        ),
    ]
    for i, sn in enumerate(freedom_snippets):
        rows.append(_row(
            signal_type="switch_reason",
            theme=f"switched_to_freedom_{i}",
            category="Software",
            brand="Freedom",
            title=f"Freedom Distraction Blocker App {i}",
            snippet=sn,
            rating=4, helpful=8,
        ))

    # 10 OFF-TOPIC noise rows: games, antivirus, OS software.
    game_snippets = [
        "Bikini Bottom Bash crashes every time I try to load level one of the game",
        "this game is too expensive for what amounts to a tile-matching mobile clone",
        "kids loved the gameplay but the in-app purchases for new game levels add up",
        "the SpongeBob game has too many ads between levels making it unplayable",
        "story mode of this game ends abruptly with a cliffhanger and no resolution",
    ]
    for i, sn in enumerate(game_snippets):
        rows.append(_row(
            signal_type="objection",
            theme="generic_disappointment",
            category="Software",
            brand=f"GameCo{i}",
            title=f"Bikini Bottom Bash Adventure Game {i}",
            snippet=sn,
            rating=1, helpful=1,
        ))
    av_snippets = [
        "antivirus works fine on my windows machine and catches every threat",
        "premium antivirus subscription auto-renewed without any obvious warning email",
        "this antivirus slows down my laptop boot time by almost two minutes daily",
        "antivirus dashboard interface is confusing and full of upsell modal popups",
        "support for the antivirus product was unhelpful when my license deactivated",
    ]
    for i, sn in enumerate(av_snippets):
        rows.append(_row(
            signal_type="praise",
            theme="general_praise",
            category="Software",
            brand=f"AntivirusCo{i}",
            title=f"AntiThreat Premium Antivirus Software {i}",
            snippet=sn,
            rating=5, helpful=0,
        ))
    return rows


def _wellness_corpus() -> list[SignalRow]:
    """Synthetic Health_and_Personal_Care corpus with wearable on-topic
    rows + sponge/bottle/beauty noise.

    Snippets are intentionally distinct in their first 96 chars so
    the retriever's snippet-collision dedup doesn't collapse rows.
    """
    rows: list[SignalRow] = []

    # Durability bucket — 4 distinct wearable failure snippets.
    dur_snippets = [
        (
            "wristband stress sensor stopped tracking my heart rate "
            "after about six weeks of normal sleep wearable use"
        ),
        (
            "haptic feedback motor on this wearable wristband stress "
            "device failed during the first month of sleep tracking"
        ),
        (
            "the wearable wristband band material cracked at the sensor "
            "mount within a few weeks of light stress and sleep wear"
        ),
        (
            "battery on my wearable stress wristband barely lasts a day "
            "with continuous sleep heart-rate sensor tracking enabled"
        ),
    ]
    for i, sn in enumerate(dur_snippets):
        rows.append(_row(
            signal_type="durability",
            theme=f"device_break_{i}",
            category="Health_and_Personal_Care",
            brand=f"WearableBrand{i}",
            title=f"Heart-rate stress sensor wristband {i}",
            snippet=sn,
            rating=2, helpful=4,
        ))

    # Trust bucket — 4 distinct data-privacy concerns.
    trust_snippets = [
        (
            "worried about how my continuous heart rate and sleep stress "
            "data from this wearable sensor wristband is being shared"
        ),
        (
            "no clear disclosure about which cloud receives the wearable "
            "wristband stress and sleep sensor heart-rate telemetry data"
        ),
        (
            "wellness wearable app required social-network login to view "
            "stress and sleep wristband heart-rate sensor data history"
        ),
        (
            "after returning the wearable wristband stress device the app "
            "still shows my heart-rate sleep sensor history online today"
        ),
    ]
    for i, sn in enumerate(trust_snippets):
        rows.append(_row(
            signal_type="trust",
            theme=f"data_concern_{i}",
            category="Health_and_Personal_Care",
            brand=f"SensorCo{i}",
            title=f"Wellness wearable heart sensor {i}",
            snippet=sn,
            rating=2, helpful=7,
        ))

    # Setup bucket — 4 distinct onboarding snippets.
    setup_snippets = [
        (
            "pairing the wearable wristband stress sensor to my phone "
            "took an hour because the heart-rate sleep setup kept timing"
        ),
        (
            "no instructions came with the wearable stress wristband and "
            "the heart-rate sleep sensor onboarding video was outdated"
        ),
        (
            "wearable wristband stress sensor would not calibrate until "
            "I let the heart-rate sleep app run unattended overnight"
        ),
        (
            "setup process for the wearable stress wristband heart-rate "
            "sensor was clearly written for a much older sleep app"
        ),
    ]
    for i, sn in enumerate(setup_snippets):
        rows.append(_row(
            signal_type="setup",
            theme=f"onboarding_friction_{i}",
            category="Health_and_Personal_Care",
            brand=f"SetupCo{i}",
            title=f"OnboardEasy wearable wristband {i}",
            snippet=sn,
            rating=2, helpful=3,
        ))

    # Price bucket — 4 distinct subscription/cost snippets.
    price_snippets = [
        (
            "wearable wristband stress sensor itself was reasonably priced "
            "but the heart-rate sleep tracking subscription doubled the cost"
        ),
        (
            "did not realize the wearable stress wristband required a "
            "monthly heart-rate sensor sleep tracking subscription to use"
        ),
        (
            "wearable wristband stress device was expensive and the "
            "heart-rate sleep sensor still misreads basic exercise data"
        ),
        (
            "refund process for the wearable stress wristband heart-rate "
            "sleep sensor was slow and required multiple support emails"
        ),
    ]
    for i, sn in enumerate(price_snippets):
        rows.append(_row(
            signal_type="price",
            theme=f"pricing_friction_{i}",
            category="Health_and_Personal_Care",
            brand=f"PriceCo{i}",
            title=f"BudgetWear wearable wristband {i}",
            snippet=sn,
            rating=2, helpful=3,
        ))

    # Competitor-anchored rows (Apollo Neuro) — 3 distinct switch_reason.
    apollo_snippets = [
        (
            "switched to Apollo Neuro because its haptic wearable wristband "
            "actually responds to my stress and sleep heart-rate sensor cues"
        ),
        (
            "Apollo Neuro wearable wristband haptic stress device "
            "outperformed this product on real-time sleep heart-rate tracking"
        ),
        (
            "after a month I bought an Apollo Neuro wearable wristband "
            "because the haptic stress sleep heart-rate response was faster"
        ),
    ]
    for i, sn in enumerate(apollo_snippets):
        rows.append(_row(
            signal_type="switch_reason",
            theme=f"switched_to_apollo_{i}",
            category="Health_and_Personal_Care",
            brand="Apollo Neuro",
            title=f"Apollo Neuro wearable haptic stress band {i}",
            snippet=sn,
            rating=4, helpful=6,
        ))

    # OFF-TOPIC noise rows: sponges, bottles, beauty.
    sponge_snippets = [
        "this loofah bath sponge falls apart after the first three showers I used it",
        "premium loofah sponge feels rough and scratches my sensitive bath skin",
        "the bath sponge developed mildew within two weeks despite air drying daily",
        "loofah handle snapped off the second time I used the premium bath sponge",
        "premium bath sponge is way overpriced for what is essentially a regular loofah",
    ]
    for i, sn in enumerate(sponge_snippets):
        rows.append(_row(
            signal_type="objection",
            theme="generic_disappointment",
            category="Health_and_Personal_Care",
            brand=f"SpongeCo{i}",
            title=f"Premium Loofah Bath Sponge {i}",
            snippet=sn,
            rating=2, helpful=0,
        ))
    bottle_snippets = [
        "plastic water bottle has a good seal and does not leak in my work backpack",
        "this 32oz plastic water bottle is the perfect size for daily gym use",
        "love the bottle color and the carry strap makes the water bottle portable",
        "plastic water bottle keeps drinks cold for about four hours which is fine",
        "lid for this water bottle is easy to clean unlike most plastic bottles I own",
    ]
    for i, sn in enumerate(bottle_snippets):
        rows.append(_row(
            signal_type="praise",
            theme="general_praise",
            category="Health_and_Personal_Care",
            brand=f"BottleCo{i}",
            title=f"Plastic Water Bottle 32oz {i}",
            snippet=sn,
            rating=5, helpful=0,
        ))
    return rows


def _make_retriever(rows: list[SignalRow]) -> AmazonSignalRetriever:
    return AmazonSignalRetriever(
        InMemorySignalSource(rows), config=_enabled_cfg(),
    )


# ---------------------------------------------------------------------------
# 1. SignalSource Protocol exposes the two new query surfaces
# ---------------------------------------------------------------------------


def test_signal_source_protocol_has_title_keyword_method() -> None:
    """Protocol-level check: the new method appears in the interface."""
    assert hasattr(SignalSource, "fetch_by_title_keyword")


def test_signal_source_protocol_has_brand_substring_method() -> None:
    assert hasattr(SignalSource, "fetch_by_brand_substring")


# ---------------------------------------------------------------------------
# 2. InMemorySignalSource implements the new surfaces correctly
# ---------------------------------------------------------------------------


def test_in_memory_fetch_by_title_keyword_finds_substring_matches() -> None:
    rows = [
        _row(title="Browser extension privacy guard", review_hash="h1"),
        _row(title="Stainless steel water bottle", review_hash="h2"),
        _row(title="Cart-saver Chrome plugin", review_hash="h3"),
    ]
    src = InMemorySignalSource(rows)
    out = asyncio.run(src.fetch_by_title_keyword("browser", limit=10))
    titles = {r.product_title for r in out}
    assert "Browser extension privacy guard" in titles
    assert "Stainless steel water bottle" not in titles


def test_in_memory_fetch_by_title_keyword_respects_category() -> None:
    rows = [
        _row(category="Software", title="Browser extension X",
             review_hash="h1"),
        _row(category="Electronics", title="Browser laptop",
             review_hash="h2"),
    ]
    src = InMemorySignalSource(rows)
    out = asyncio.run(
        src.fetch_by_title_keyword(
            "browser", category="Software", limit=10,
        ),
    )
    assert {r.category for r in out} == {"Software"}


def test_in_memory_fetch_by_brand_substring_matches_brand_or_title() -> None:
    rows = [
        _row(brand="Freedom App", title="Distraction Blocker",
             review_hash="hA"),
        _row(brand="OtherCo", title="Freedom Inspired Tool",
             review_hash="hB"),
        _row(brand="OtherCo", title="Unrelated Thing",
             review_hash="hC"),
    ]
    src = InMemorySignalSource(rows)
    out = asyncio.run(
        src.fetch_by_brand_substring("freedom", limit=10),
    )
    keys = {r.source_review_hash for r in out}
    assert keys == {"hA", "hB"}


def test_in_memory_fetch_by_brand_substring_competitor_field() -> None:
    rows = [
        _row(brand="OtherCo", competitor="Apollo Neuro",
             title="GenericTitle", review_hash="hX"),
    ]
    src = InMemorySignalSource(rows)
    out = asyncio.run(
        src.fetch_by_brand_substring("apollo", limit=10),
    )
    assert len(out) == 1


def test_in_memory_fetch_by_title_keyword_empty_string_returns_empty() -> None:
    src = InMemorySignalSource([_row(review_hash="h1")])
    assert asyncio.run(src.fetch_by_title_keyword("", limit=5)) == []
    assert asyncio.run(src.fetch_by_brand_substring("", limit=5)) == []


# ---------------------------------------------------------------------------
# 3. Title-keyword extraction respects category preset + brief
# ---------------------------------------------------------------------------


def test_title_keywords_includes_preset_overlap_first() -> None:
    """A QuietCart-style brief should pull preset-overlap tokens
    (browser, extension, shopping, impulse) before brief-only
    tokens."""
    kws = _extract_title_keywords(_QUIETCART_SHAPE, "Software")
    preset = set(_TITLE_KEYWORD_HINTS["Software"])
    # First N tokens should all be in the preset (or the brief has
    # no preset overlap at all).
    if kws and any(k in preset for k in kws):
        # At least one preset overlap exists; the FIRST occurrence
        # of a preset token must come before any non-preset token.
        first_preset = next(i for i, k in enumerate(kws) if k in preset)
        first_non_preset = next(
            (i for i, k in enumerate(kws) if k not in preset),
            len(kws),
        )
        assert first_preset <= first_non_preset


def test_title_keywords_quietcart_contains_browser_or_extension() -> None:
    kws = _extract_title_keywords(_QUIETCART_SHAPE, "Software")
    assert "browser" in kws or "extension" in kws or "shopping" in kws


def test_title_keywords_calmcue_contains_wearable_or_stress() -> None:
    kws = _extract_title_keywords(
        _CALMCUE_SHAPE, "Health_and_Personal_Care",
    )
    assert (
        "wearable" in kws or "stress" in kws or "sensor" in kws
        or "sleep" in kws
    )


def test_title_keywords_empty_brief_returns_empty() -> None:
    shape = ProductBriefShape(product_name="", description="")
    assert _extract_title_keywords(shape, "Software") == []


def test_title_keywords_capped_at_max() -> None:
    """Even with a very long brief, we don't produce more than the
    documented max. Catches a regression where the cap is bypassed."""
    long_desc = " ".join(
        f"word{i}token" for i in range(50)
    )
    shape = ProductBriefShape(product_name="x", description=long_desc)
    kws = _extract_title_keywords(shape, None)
    assert len(kws) <= 12


def test_title_keywords_excludes_short_and_stopwords() -> None:
    shape = ProductBriefShape(
        product_name="A The And Browser",
        description="An or extension is for online shoppers to stop",
    )
    kws = _extract_title_keywords(shape, "Software")
    for kw in kws:
        assert len(kw) >= 4
        assert kw not in {"the", "and", "for"}


# ---------------------------------------------------------------------------
# 4. QuietCart brief pulls software/browser/extension snippets
# ---------------------------------------------------------------------------


def test_quietcart_brief_pulls_browser_extension_pool() -> None:
    """Expanded retrieval surfaces title-keyword-anchored rows the
    plain category pull would NOT have ranked first."""
    rows = _software_corpus()
    r = _make_retriever(rows)
    candidates, stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_QUIETCART_SHAPE),
    )
    assert category == "Software"
    # Title-keyword pool produced rows (the on-topic browser extension
    # rows have "browser"/"extension" in their titles).
    assert stats.title_keyword_candidates > 0
    # At least one survives dedupe with a useful theme.
    on_topic = [
        s for s in candidates
        if s.product_title and (
            "browser" in s.product_title.lower()
            or "extension" in s.product_title.lower()
        )
    ]
    assert len(on_topic) >= 4


def test_quietcart_audit_reports_title_keywords_used() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )

    rows = _software_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_retriever = inj.AmazonSignalRetriever
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.AmazonSignalRetriever = orig_retriever
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]

    assert "title_keywords_used" in audit
    assert audit["title_keywords_used"]
    # The preset keywords for Software that appear in the QuietCart
    # brief: browser, extension, shopping, impulse, ...
    assert any(
        k in audit["title_keywords_used"]
        for k in ("browser", "extension", "shopping", "impulse")
    )


# ---------------------------------------------------------------------------
# 5. CalmCue brief pulls wearable/stress/sensor snippets
# ---------------------------------------------------------------------------


def test_calmcue_brief_pulls_wearable_stress_pool() -> None:
    rows = _wellness_corpus()
    r = _make_retriever(rows)
    candidates, stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_CALMCUE_SHAPE),
    )
    assert category == "Health_and_Personal_Care"
    assert stats.title_keyword_candidates > 0
    on_topic = [
        s for s in candidates
        if s.product_title and (
            "wearable" in s.product_title.lower()
            or "stress" in s.product_title.lower()
            or "sensor" in s.product_title.lower()
        )
    ]
    assert len(on_topic) >= 4


# ---------------------------------------------------------------------------
# 6. Competitor names anchor the brand pool via substring
# ---------------------------------------------------------------------------


def test_competitor_substring_match_populates_brand_pool() -> None:
    """Freedom-branded rows in the corpus should be retrieved through
    the brand pool when 'Freedom' is in the brief's competitors."""
    rows = _software_corpus()
    r = _make_retriever(rows)
    candidates, stats, _ = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_QUIETCART_SHAPE),
    )
    assert stats.competitor_brand_candidates > 0
    assert "Freedom" in stats.matched_brands_or_competitors


def test_apollo_neuro_substring_match_populates_brand_pool() -> None:
    rows = _wellness_corpus()
    r = _make_retriever(rows)
    candidates, stats, _ = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_CALMCUE_SHAPE),
    )
    assert stats.competitor_brand_candidates > 0
    assert any(
        "Apollo" in c for c in stats.matched_brands_or_competitors
    )


# ---------------------------------------------------------------------------
# 7. Off-topic games downrank for QuietCart (relevance filter
#    behaviour preserved through the new candidate-pool path)
# ---------------------------------------------------------------------------


def test_quietcart_game_titles_downranked_through_full_pipeline() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )
    rows = _software_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        block, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]

    assert block is not None
    # No "Bikini Bottom" / game noise in the final block.
    assert "Bikini Bottom" not in block
    assert "game" not in block.lower()


# ---------------------------------------------------------------------------
# 8. Off-topic sponges downrank for CalmCue
# ---------------------------------------------------------------------------


def test_calmcue_sponge_titles_downranked_through_full_pipeline() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )
    rows = _wellness_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        block, _audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _CALMCUE_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]

    assert block is not None
    # No sponge / bottle noise in the final block.
    assert "sponge" not in block.lower()
    assert "loofah" not in block.lower()
    assert "bottle" not in block.lower()


# ---------------------------------------------------------------------------
# 9. Candidate-pool expansion beats Phase-11C.6 category-only retrieval
#    in kept-signal count for niche briefs
# ---------------------------------------------------------------------------


def test_expanded_pool_kept_signal_count_meets_threshold() -> None:
    """With the synthetic-but-realistic corpus, the expanded
    candidate-pool path must surface at least 6 relevance-passing
    candidates for QuietCart. This is the operator-spec'd success
    criterion."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )
    rows = _software_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]
    # Operator-spec: ≥ 6 reasonably relevant snippets in the final
    # block for a brief that has good corpus coverage.
    assert audit["final_snippets"] >= 6, (
        f"expected ≥ 6 final snippets, got {audit['final_snippets']}; "
        f"audit={audit}"
    )


# ---------------------------------------------------------------------------
# 10. same_category_only invariant still holds across all four pools
# ---------------------------------------------------------------------------


def test_same_category_only_drops_cross_category_signal_type_rows() -> None:
    """Pool 4 (signal_type) deliberately ignores category at the
    DB layer for diversity. The hard same_category gate must drop
    any cross-category rows it surfaces."""
    rows = _software_corpus() + [
        _row(
            signal_type="objection",
            category="Health_and_Personal_Care",
            brand="Cross",
            title="Cross-category row",
            snippet="this should NOT surface for QuietCart",
            review_hash="hx_cross",
        ),
    ]
    r = _make_retriever(rows)
    candidates, _stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_QUIETCART_SHAPE),
    )
    assert category == "Software"
    for s in candidates:
        assert s.category == "Software"


def test_unclassified_brief_returns_empty_pool() -> None:
    """If `same_category_only` is on (default) and the brief doesn't
    classify, the retriever refuses to retrieve. Failing closed."""
    r = _make_retriever(_software_corpus())
    candidates, stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(
            ProductBriefShape(
                product_name="zzz unclassifiable widget",
                description="aaa bbb ccc",
            ),
        ),
    )
    assert category is None
    assert candidates == []
    assert stats.candidates_after_dedupe == 0


def test_unclassified_brief_with_same_category_false_does_not_block() -> None:
    """The dev-only dial: when same_category_only is False AND the
    brief doesn't classify, the retriever still retrieves and flags
    `fallback_used=True`."""
    cfg = RetrievalConfig(
        enabled=True,
        runtime_enabled=True,
        same_category_only=False,
        persona_injection_enabled=True,
        max_signals_per_run=80,
        max_signals_per_category=40,
        max_signals_per_competitor=20,
        max_signals_per_brand=8,
        max_signals_per_theme=10,
    )
    r = AmazonSignalRetriever(
        InMemorySignalSource(_software_corpus()), config=cfg,
    )
    candidates, stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(
            ProductBriefShape(
                product_name="zzz unclassifiable thing",
                description="aaa bbb ccc",
            ),
        ),
    )
    assert category is None
    # We still pulled the signal-type pool — diversity remains.
    assert stats.signal_type_candidates > 0
    assert stats.fallback_used is True
    assert candidates


# ---------------------------------------------------------------------------
# 11. Final prompt cap (12 snippets, 2 per bucket) still enforced
# ---------------------------------------------------------------------------


def test_final_prompt_block_respects_12_snippet_cap() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        _PROMPT_BLOCK_MAX_SNIPPETS,
        _PROMPT_BLOCK_PER_BUCKET,
        build_amazon_persona_prompt_block_with_audit,
    )
    # 200 on-topic rows — more than enough to overflow the cap.
    rows = []
    for i in range(200):
        rows.append(_row(
            signal_type="objection",
            theme=f"theme_{i % 5}",
            category="Software",
            brand=f"BX{i}",
            title=(
                f"Browser extension privacy shopping focus blocker {i}"
            ),
            snippet=(
                f"browser extension impulse buying issue {i} on "
                f"shopping sites"
            ),
            rating=2, helpful=5,
            review_hash=f"h_oversupply_{i}",
        ))

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]

    assert audit["final_snippets"] <= _PROMPT_BLOCK_MAX_SNIPPETS
    for stype, count in audit["final_block_distribution"].items():
        assert count <= _PROMPT_BLOCK_PER_BUCKET


# ---------------------------------------------------------------------------
# 12. All three persona-injection gates still required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enabled,runtime,persona",
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_audit_helper_returns_none_when_any_gate_off(
    enabled: bool, runtime: bool, persona: bool,
) -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )

    class _S:
        amazon_reviews_enabled = enabled
        amazon_reviews_runtime_enabled = runtime
        amazon_reviews_persona_injection_enabled = persona
        amazon_reviews_same_category_only = True
        amazon_reviews_persona_min_relevance = 0.20
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10

    block, audit = asyncio.run(
        build_amazon_persona_prompt_block_with_audit(
            _QUIETCART_BRIEF,
            sessionmaker=_exploding_sessionmaker,
            settings=_S(),  # type: ignore[arg-type]
        ),
    )
    assert block is None
    # Audit shape still populated with zero counts.
    assert audit["signals_considered"] == 0
    assert audit["candidate_pool_size"] == 0


# ---------------------------------------------------------------------------
# 13. Audit dict exposes per-pool counts + matched brands +
#     title_keywords_used + fallback_used
# ---------------------------------------------------------------------------


_REQUIRED_AUDIT_KEYS_11C_7 = (
    "min_relevance_threshold",
    "category_matched",
    "candidate_pool_size",
    "category_candidates",
    "title_keyword_candidates",
    "competitor_brand_candidates",
    "signal_type_candidates",
    "candidates_after_dedupe",
    "title_keywords_used",
    "matched_brands_or_competitors",
    "fallback_used",
    "signals_considered",
    "signals_kept_after_filter",
    "signals_rejected_relevance",
    "candidates_after_relevance",
    "final_snippets",
    "avg_relevance_score",
    "top_kept_scores",
    "top_rejected_scores",
    "rejection_reasons",
    "sample_rejected_snippets",
    "final_block_distribution",
)


def test_audit_dict_has_every_11c7_field() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )
    rows = _software_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]
    missing = [k for k in _REQUIRED_AUDIT_KEYS_11C_7 if k not in audit]
    assert not missing, f"audit missing 11C.7 keys: {missing}"


def test_audit_top_scores_lists_capped_at_5_and_sorted_desc() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )
    rows = _software_corpus()

    class _FakeSource:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._src = InMemorySignalSource(rows)

        async def fetch_by_category(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_category(*a, **kw)

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_competitor(*a, **kw)

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_theme(*a, **kw)

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_title_keyword(*a, **kw)

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            return await self._src.fetch_by_brand_substring(*a, **kw)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig_source = inj.PostgresSignalSource
    inj.PostgresSignalSource = _FakeSource  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                _QUIETCART_BRIEF,
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            ),
        )
    finally:
        inj.PostgresSignalSource = orig_source  # type: ignore[assignment]
    assert len(audit["top_kept_scores"]) <= 5
    assert len(audit["top_rejected_scores"]) <= 5
    # Descending sort.
    for lst in (audit["top_kept_scores"], audit["top_rejected_scores"]):
        for a, b in zip(lst, lst[1:]):
            assert a >= b


# ---------------------------------------------------------------------------
# 14. Forbidden fields never leak — RetrievedSignal contract preserved
# ---------------------------------------------------------------------------


def test_candidate_pool_returns_only_retrieved_signals() -> None:
    """Every element returned is a RetrievedSignal — the stripped
    persona-grade shape — never a raw SignalRow."""
    r = _make_retriever(_software_corpus())
    candidates, _stats, _category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_QUIETCART_SHAPE),
    )
    for c in candidates:
        assert isinstance(c, RetrievedSignal)
        assert not hasattr(c, "source_review_hash")


def test_candidate_pool_stats_carries_no_signal_data() -> None:
    """The stats dataclass must only carry counts + thin lists —
    NO snippet text, no signal rows."""
    fields = set(CandidatePoolStats.__dataclass_fields__)
    # Sanity: no field name suggests row data.
    forbidden_substrings = (
        "snippet", "raw_text", "user_id", "source_review_hash",
        "image", "review_body",
    )
    for f in fields:
        for token in forbidden_substrings:
            assert token not in f, (
                f"CandidatePoolStats.{f} looks like raw row data"
            )


# ---------------------------------------------------------------------------
# 15. Drift: only the whitelisted injector touches the retriever
# ---------------------------------------------------------------------------


def test_only_whitelisted_file_imports_candidate_pool_method() -> None:
    """The new `retrieve_candidate_pool_for_persona` method shares
    the same whitelist as the rest of the retriever — only
    `pipeline/amazon_evidence_injector.py` may call it from live
    pipeline code."""
    api_root = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly"
    )
    live_dirs = [
        api_root / "api",
        api_root / "pipeline",
        api_root / "orchestration",
    ]
    whitelist = {api_root / "pipeline" / "amazon_evidence_injector.py"}
    for d in live_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if path in whitelist:
                continue
            text = path.read_text(encoding="utf-8")
            assert "retrieve_candidate_pool_for_persona" not in text, (
                f"{path} calls retrieve_candidate_pool_for_persona — "
                f"must go through the whitelisted injector"
            )


# ---------------------------------------------------------------------------
# 16. PostgresSignalSource has the two new methods (type-check only)
# ---------------------------------------------------------------------------


def test_postgres_source_has_title_keyword_and_brand_methods() -> None:
    """Confirm the production source implements the new Protocol
    surfaces. We don't actually connect to Postgres here."""
    from assembly.sources.amazon_reviews_provider.postgres_source import (
        PostgresSignalSource,
    )
    assert callable(getattr(PostgresSignalSource, "fetch_by_title_keyword"))
    assert callable(
        getattr(PostgresSignalSource, "fetch_by_brand_substring"),
    )
    # Async coroutine functions.
    assert inspect.iscoroutinefunction(
        PostgresSignalSource.fetch_by_title_keyword,
    )
    assert inspect.iscoroutinefunction(
        PostgresSignalSource.fetch_by_brand_substring,
    )


# ---------------------------------------------------------------------------
# 17. Disabled flags short-circuit before any source method is called
# ---------------------------------------------------------------------------


def test_disabled_flags_return_empty_candidate_pool() -> None:
    """With both retrieval gates off, the new method short-circuits
    and never touches the source. We assert that by passing a fake
    source whose methods raise if called."""

    class _ExplodingSource:
        async def fetch_by_category(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_category called when off")

        async def fetch_by_competitor(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_competitor called when off")

        async def fetch_by_theme(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_theme called when off")

        async def fetch_by_title_keyword(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_title_keyword called when off")

        async def fetch_by_brand_substring(self, *a: Any, **kw: Any):
            raise AssertionError("fetch_by_brand_substring called when off")

    r = AmazonSignalRetriever(
        _ExplodingSource(),  # type: ignore[arg-type]
        config=RetrievalConfig(enabled=False, runtime_enabled=False),
    )
    candidates, stats, category = asyncio.run(
        r.retrieve_candidate_pool_for_persona(_QUIETCART_SHAPE),
    )
    assert candidates == []
    assert stats.candidates_after_dedupe == 0
    assert category is None
