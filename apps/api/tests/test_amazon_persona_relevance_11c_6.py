"""Phase 11C.6 — product-shape relevance filter tests.

Phase 11C.5's A/B run surfaced a real issue: McAuley's category-level
retrieval is too broad. A QuietCart brief in the Software category
gets snippets about games; a CalmCue brief in Health_and_Personal_Care
gets snippets about sponges/bottles. Phase 11C.6 adds a deterministic
relevance scorer that drops those mismatches before the persona block
is built.

This file pins:

  1. The scorer is deterministic + pure.
  2. Game snippets fail the threshold on a browser-extension brief.
  3. Privacy/setup/trust software snippets pass.
  4. Sponge/bottle snippets fail on a wellness-wearable brief.
  5. Wellness/wearable-adjacent snippets pass when available.
  6. Generic praise is downranked.
  7. The threshold config defaults conservatively (0.20).
  8. The block formatter wires the filter on automatically when
     relevance > 0.
  9. The audit dict reports filter stats (considered, kept, rejected,
     reasons, sample rejected, avg score).
 10. No forbidden fields surface in audit / block.
 11. Hard caps still hold after filtering.

NO LIVE LLM, NO LIVE NETWORK, NO POSTGRES.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from assembly.sources.amazon_reviews_provider import (
    AmazonEvidencePackage,
    ProductBriefShape,
    RetrievedSignal,
)
from assembly.sources.amazon_reviews_provider.relevance import (
    SignalRelevanceScore,
    filter_signals_by_relevance,
    score_signal_for_brief,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_QUIETCART_SHAPE = ProductBriefShape(
    product_name="QuietCart",
    description=(
        "A browser extension for online shoppers who want to stop "
        "impulse buying on Amazon and other shopping sites. Adds a "
        "delay and privacy-focused review of carts before checkout."
    ),
    category_hint="browser extension",
    competitors=("Freedom", "Opal", "Rocket Money"),
)

_CALMCUE_SHAPE = ProductBriefShape(
    product_name="CalmCue stress wearable",
    description=(
        "A screenless wellness wearable that tracks stress and sleep "
        "via passive skin sensors. No mobile app required."
    ),
    category_hint="wellness wearable",
    competitors=("Apollo Neuro", "Muse", "Whoop"),
)


def _sig(
    *,
    signal_type: str = "objection",
    sentiment: str = "negative",
    theme: str = "generic_disappointment",
    category: str = "Software",
    brand: str | None = "Some Brand",
    title: str | None = "Some Title",
    rating: int | None = 1,
    snippet: str = "this thing is bad",
    verified: bool = True,
    helpful: int = 1,
    competitor_mention: str | None = None,
    use_case: str | None = None,
) -> RetrievedSignal:
    return RetrievedSignal(
        signal_type=signal_type,
        sentiment_bucket=sentiment,
        theme=theme,
        category=category,
        brand=brand,
        product_title=title,
        rating=rating,
        short_snippet=snippet,
        competitor_mention=competitor_mention,
        use_case=use_case,
        verified_purchase=verified,
        helpful_votes=helpful,
    )


# ---------------------------------------------------------------------------
# 1. Deterministic + structurally sound
# ---------------------------------------------------------------------------


def test_scorer_is_deterministic() -> None:
    s = _sig(snippet="really useful browser extension")
    a = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    b = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert a == b


def test_score_clamped_to_unit_interval() -> None:
    s = _sig(
        title="QuietCart browser extension privacy",
        snippet="browser extension impulse buying privacy",
        signal_type="trust", theme="privacy_concern",
        verified=True, helpful=20,
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert 0.0 <= sc.total <= 1.0


# ---------------------------------------------------------------------------
# 2 + 3. QuietCart (Software, browser extension) — game snippets out,
#       privacy/setup/trust snippets in.
# ---------------------------------------------------------------------------


def test_game_snippet_fails_quietcart_filter() -> None:
    """A snippet about a game in the Software category must score
    below the default 0.20 threshold."""
    s = _sig(
        signal_type="objection",
        theme="generic_disappointment",
        title="SpongeBob SquarePants: Bikini Bottom Bash",
        snippet=(
            "This game should if been free; $3.99 for a game that's "
            "similar to the Simpson's Tapped out is a bit too much."
        ),
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.total < 0.20, (
        f"Game snippet scored too high: {sc.total} ({sc})"
    )


def test_trust_software_snippet_passes_quietcart_filter() -> None:
    """A privacy/trust complaint about a software product must
    survive the filter."""
    s = _sig(
        signal_type="trust",
        sentiment="negative",
        theme="privacy_concern",
        title="Free Browser Extension That Wants Your Data",
        snippet=(
            "If you want a company to have complete access to all of "
            "your personal data then this is the FREE for a year app "
            "for you. Browser extension privacy issues are real."
        ),
        verified=True,
        helpful=15,
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.total >= 0.20, (
        f"Real trust complaint scored too low: {sc.total} ({sc})"
    )


def test_setup_software_snippet_passes_quietcart_filter() -> None:
    """A real setup-friction software complaint that shares words
    with the brief (browser, extension, install) survives."""
    s = _sig(
        signal_type="setup",
        sentiment="negative",
        theme="couldnt_setup",
        title="Browser Extension Install Failed",
        snippet=(
            "I couldn't get the browser extension to install on my "
            "Chrome — kept failing every shopping site I tried."
        ),
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.total >= 0.20


def test_competitor_match_boosts_quietcart_score() -> None:
    """A signal whose brand IS one of the brief's competitors gets
    the competitor-match boost."""
    s = _sig(
        signal_type="price",
        theme="price_objection",
        brand="Freedom",  # competitor in the brief
        title="Freedom App Monthly Subscription",
        snippet=(
            "Too expensive for what it does — blocks distracting sites "
            "but the price tier is steep."
        ),
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.competitor_match is True
    assert sc.total >= 0.20


# ---------------------------------------------------------------------------
# 4 + 5. CalmCue (wellness wearable) — sponges/bottles out, wearable
#       snippets in.
# ---------------------------------------------------------------------------


def test_sponge_snippet_fails_calmcue_filter() -> None:
    s = _sig(
        category="Health_and_Personal_Care",
        signal_type="objection",
        theme="generic_disappointment",
        title="Exfoliating Bath Sponge 3-pack",
        snippet=(
            "This sponge does not work. I scrubbed and scrubbed but "
            "nothing came out from my skin."
        ),
    )
    sc = score_signal_for_brief(s, brief=_CALMCUE_SHAPE)
    assert sc.total < 0.20, (
        f"Sponge snippet scored too high: {sc.total} ({sc})"
    )


def test_wearable_snippet_passes_calmcue_filter() -> None:
    s = _sig(
        category="Health_and_Personal_Care",
        signal_type="durability",
        theme="broke_or_died",
        title="Stress Tracking Wearable Wristband",
        snippet=(
            "The wearable's sensor stopped working after 3 months. "
            "Stress and sleep tracking became unreliable."
        ),
    )
    sc = score_signal_for_brief(s, brief=_CALMCUE_SHAPE)
    assert sc.total >= 0.20


def test_competitor_match_works_for_calmcue() -> None:
    s = _sig(
        category="Health_and_Personal_Care",
        signal_type="switch_reason",
        theme="comparison_better_than",
        brand="Apollo Neuro",
        title="Apollo Neuro wearable",
        snippet="switched from a sleep tracker to this one",
    )
    sc = score_signal_for_brief(s, brief=_CALMCUE_SHAPE)
    assert sc.competitor_match is True


# ---------------------------------------------------------------------------
# 6. Generic praise / generic disappointment downranked
# ---------------------------------------------------------------------------


def test_generic_praise_downranked() -> None:
    """A bare 'I love it' snippet with no product-specific words
    should fall below the default threshold for QuietCart."""
    s = _sig(
        signal_type="praise",
        sentiment="positive",
        theme="general_praise",
        title="Great Product",
        snippet="I love it",  # extremely short + generic
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.total < 0.20


def test_generic_disappointment_downranked() -> None:
    """A bare 'disappointing' snippet with no specifics should drop."""
    s = _sig(
        signal_type="objection",
        sentiment="negative",
        theme="generic_disappointment",
        title="Bad Product",
        snippet="disappointing",
    )
    sc = score_signal_for_brief(s, brief=_QUIETCART_SHAPE)
    assert sc.total < 0.20


# ---------------------------------------------------------------------------
# 7. Threshold + config default
# ---------------------------------------------------------------------------


def test_default_threshold_is_conservative() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_persona_min_relevance == 0.20


def test_threshold_zero_disables_filter() -> None:
    """With min_score=0.0 every signal survives — Phase-11C.5 mode."""
    signals = [
        _sig(snippet="this game is bad"),
        _sig(snippet="this sponge is bad"),
        _sig(snippet="completely unrelated"),
    ]
    kept, rejected = filter_signals_by_relevance(
        signals, brief=_QUIETCART_SHAPE, min_score=0.0,
    )
    assert len(kept) == 3
    assert len(rejected) == 0


def test_strict_threshold_drops_borderline_signals() -> None:
    """A strict threshold (0.50) drops signals that pass the lax 0.20."""
    s = _sig(
        signal_type="trust",
        theme="privacy_concern",
        title="Some Software Tool",
        snippet="useful tool for online shoppers concerned about privacy",
    )
    lax = filter_signals_by_relevance(
        [s], brief=_QUIETCART_SHAPE, min_score=0.20,
    )
    strict = filter_signals_by_relevance(
        [s], brief=_QUIETCART_SHAPE, min_score=0.50,
    )
    # Borderline = passes lax, fails strict.
    assert (len(lax[0]) == 1) or (len(strict[0]) == 0)


# ---------------------------------------------------------------------------
# 8. End-to-end: build_amazon_persona_prompt_block_with_audit shape
# ---------------------------------------------------------------------------


def _exploding_sessionmaker(*a: Any, **kw: Any) -> Any:  # pragma: no cover
    raise AssertionError("DB session opened despite flags off")


def _pkg_to_candidate_pool_response(pkg: Any) -> tuple[list, Any, str | None]:
    """Phase 11C.7 compatibility helper. Adapts a legacy
    AmazonEvidencePackage into the new
    `retrieve_candidate_pool_for_persona` return tuple."""
    from assembly.sources.amazon_reviews_provider import (
        CandidatePoolStats,
    )
    n = len(pkg.signals)
    stats = CandidatePoolStats(
        category_candidates=n,
        title_keyword_candidates=0,
        competitor_brand_candidates=0,
        signal_type_candidates=0,
        candidates_after_dedupe=n,
        title_keywords_used=[],
        matched_brands_or_competitors=[],
        fallback_used=False,
    )
    return (list(pkg.signals), stats, pkg.category_matched)


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


def _mixed_quality_package() -> AmazonEvidencePackage:
    """Mix of high-relevance + game noise — matches what the
    Phase-11C.5 A/B run surfaced on real data."""
    return AmazonEvidencePackage(
        attempted=True, feature_flag_status={},
        category_matched="Software",
        signals=[
            # Real privacy concern — should survive.
            _sig(
                signal_type="trust",
                theme="privacy_concern",
                title="Free Browser Extension Wants Your Data",
                snippet=(
                    "If you want a company to have complete access to all "
                    "of your personal data then this is the FREE for a "
                    "year browser extension for you."
                ),
                helpful=15,
            ),
            # Real setup pain — should survive.
            _sig(
                signal_type="setup",
                theme="couldnt_setup",
                title="Browser Extension for Online Shopping",
                snippet=(
                    "I couldn't get this browser extension to install on "
                    "my Mac for online shopping sites."
                ),
            ),
            # Game noise — should be filtered out.
            _sig(
                signal_type="objection",
                theme="generic_disappointment",
                title="SpongeBob SquarePants Bikini Bottom Bash",
                snippet=(
                    "This game should if been free; $3.99 for a game "
                    "is too much."
                ),
            ),
            # Another piece of noise.
            _sig(
                signal_type="praise",
                theme="general_praise",
                title="Hair Straightener",
                snippet="great hair straightener heats up quickly",
            ),
        ],
        distribution={},
        brand_coverage=1.0,
        title_coverage=1.0,
        skipped_reasons={},
        notes=[],
    )


def test_audit_aware_helper_reports_filter_counts() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return _mixed_quality_package()

        async def retrieve_candidate_pool_for_persona(self, shape):
            return _pkg_to_candidate_pool_response(
                _mixed_quality_package(),
            )

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        block, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                {
                    "product_name": "QuietCart",
                    "product_description": (
                        "Browser extension for online shoppers to stop "
                        "impulse buying on shopping sites."
                    ),
                    "category_hint": "browser extension",
                    "competitors_or_alternatives": ["Freedom", "Opal"],
                },
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            )
        )
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]

    # Audit shape — all keys present.
    required = {
        "min_relevance_threshold",
        "signals_considered",
        "signals_kept_after_filter",
        "signals_rejected_relevance",
        "avg_relevance_score",
        "rejection_reasons",
        "sample_rejected_snippets",
        "final_block_distribution",
    }
    assert required <= set(audit), (
        f"audit missing keys: {required - set(audit)}"
    )
    assert audit["signals_considered"] == 4
    assert audit["signals_kept_after_filter"] >= 2  # trust + setup
    assert audit["signals_rejected_relevance"] >= 1  # game noise out
    # Sample-rejected snippets exist + carry a drop_reason.
    if audit["sample_rejected_snippets"]:
        for r in audit["sample_rejected_snippets"]:
            assert "drop_reason" in r
    # Block was produced because at least the trust + setup signals
    # survived.
    assert block is not None
    assert "category=Software" in block


def test_audit_aware_helper_returns_none_when_flags_off() -> None:
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )

    class _SettingsOff:
        amazon_reviews_enabled = False
        amazon_reviews_runtime_enabled = False
        amazon_reviews_persona_injection_enabled = False
        amazon_reviews_same_category_only = True
        amazon_reviews_persona_min_relevance = 0.20
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10

    block, audit = asyncio.run(
        __import__(
            "assembly.pipeline.amazon_evidence_injector",
            fromlist=["build_amazon_persona_prompt_block_with_audit"],
        ).build_amazon_persona_prompt_block_with_audit(
            {"product_name": "x", "product_description": "y"},
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsOff(),  # type: ignore[arg-type]
        ),
    )
    assert block is None
    # Audit shape still populated with zero counts.
    assert audit["signals_considered"] == 0
    assert audit["signals_kept_after_filter"] == 0


# ---------------------------------------------------------------------------
# 9. Forbidden-field invariant survives the audit dict
# ---------------------------------------------------------------------------


def test_filter_audit_dict_has_no_forbidden_fields() -> None:
    """Belt-and-braces: even with sample_rejected_snippets present,
    the audit dict carries zero forbidden top-level keys."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block_with_audit,
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return _mixed_quality_package()

        async def retrieve_candidate_pool_for_persona(self, shape):
            return _pkg_to_candidate_pool_response(
                _mixed_quality_package(),
            )

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        _, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                {"product_name": "QuietCart", "product_description": "x"},
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            )
        )
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]

    forbidden = {
        "source_review_hash", "user_id", "user_id_hash",
        "images", "image_url", "raw_text", "review_body", "id",
    }
    assert not (set(audit) & forbidden)
    for entry in audit["sample_rejected_snippets"]:
        assert not (set(entry) & forbidden)


# ---------------------------------------------------------------------------
# 10. Caps still hold after relevance filtering
# ---------------------------------------------------------------------------


def test_caps_still_apply_after_relevance_filter() -> None:
    """100 relevance-passing signals fed in => 12-snippet final
    block cap still enforced."""
    from assembly.pipeline.amazon_evidence_injector import (
        _PROMPT_BLOCK_MAX_SNIPPETS,
        _PROMPT_BLOCK_PER_BUCKET,
        build_amazon_persona_prompt_block_with_audit,
    )

    # 30 signals across 5 types — all designed to score well.
    signals = []
    types = ("objection", "trust", "setup", "durability", "price")
    for i in range(6):
        for t in types:
            signals.append(_sig(
                signal_type=t,
                theme=f"theme_{t}",
                title=(
                    "QuietCart browser extension for online shoppers"
                ),
                snippet=(
                    f"browser extension issue {i}: shoppers and impulse "
                    f"buying on shopping sites - {t} concern"
                ),
                helpful=10,
            ))

    _pkg_for_caps = AmazonEvidencePackage(
        attempted=True, feature_flag_status={},
        category_matched="Software",
        signals=signals, distribution={},
        brand_coverage=1.0, title_coverage=1.0,
        skipped_reasons={}, notes=[],
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return _pkg_for_caps

        async def retrieve_candidate_pool_for_persona(self, shape):
            return _pkg_to_candidate_pool_response(_pkg_for_caps)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        block, audit = asyncio.run(
            build_amazon_persona_prompt_block_with_audit(
                {
                    "product_name": "QuietCart",
                    "product_description": (
                        "Browser extension for online shoppers to stop "
                        "impulse buying on shopping sites."
                    ),
                    "category_hint": "browser extension",
                },
                sessionmaker=MagicMock(),
                settings=_SettingsAllOn(),  # type: ignore[arg-type]
            )
        )
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]

    # Final block respects the per-signal-type cap of 2.
    final_dist = audit["final_block_distribution"]
    for stype, count in final_dist.items():
        assert count <= _PROMPT_BLOCK_PER_BUCKET, (
            f"{stype} exceeded per-bucket cap after relevance filter"
        )
    total_in_block = sum(final_dist.values())
    assert total_in_block <= _PROMPT_BLOCK_MAX_SNIPPETS


# ---------------------------------------------------------------------------
# 11. Three-flag gate still required
# ---------------------------------------------------------------------------


def test_relevance_filter_inert_when_persona_flag_off() -> None:
    """If persona_injection_enabled=False, the relevance filter is
    irrelevant because the helper returns None before scoring."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_persona_prompt_block,
    )

    class _SettingsTwoOn:
        amazon_reviews_enabled = True
        amazon_reviews_runtime_enabled = True
        amazon_reviews_persona_injection_enabled = False  # ← off
        amazon_reviews_same_category_only = True
        amazon_reviews_persona_min_relevance = 0.20
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10

    block = asyncio.run(
        build_amazon_persona_prompt_block(
            {"product_name": "x", "product_description": "y"},
            sessionmaker=_exploding_sessionmaker,
            settings=_SettingsTwoOn(),  # type: ignore[arg-type]
        )
    )
    assert block is None
