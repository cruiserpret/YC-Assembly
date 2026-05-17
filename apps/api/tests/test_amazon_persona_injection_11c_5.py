"""Phase 11C.5 — Amazon persona-injection experiment tests.

Up through Phase 11C.4, Amazon evidence was strictly AUDIT-ONLY —
visible in `founder_report.json` under `technical.amazon_reviews_2023`
but never reaching persona prompts. Phase 11C.5 adds an EXPERIMENTAL
path that injects a compact Amazon buyer-language block into each
persona's discussion prompt, gated behind a third explicit flag.

This file pins:

  1. The third gate defaults False, on top of the other two.
  2. The prompt-block helper returns None when ANY of the three
     gates is off (no DB hit when fully disabled).
  3. The block, when present, is capped at 12 snippets, balanced
     across `_PROMPT_BLOCK_BUCKET_ORDER`, with no single bucket
     filling more than 2 slots.
  4. Per-snippet length ≤ 180 chars (a second cap layered on top
     of Phase-11A's 240-char distillation cap).
  5. No forbidden review-row fields appear in the block text.
  6. The block carries the `same_category_only` invariant — every
     snippet's signal_type comes from the matched category.
  7. The discussion-pipeline `_build_block` returns the prior
     Phase-11C.4 prompt shape when `amazon_persona_block=None`.
  8. The audit dict carries `amazon_persona_injection_enabled` so
     `founder_report.json` can show whether the gate was on.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from assembly.pipeline.amazon_evidence_injector import (
    build_amazon_persona_prompt_block,
)
from assembly.pipeline.amazon_evidence_injector import (
    _PROMPT_BLOCK_BUCKET_ORDER,
    _PROMPT_BLOCK_MAX_SNIPPETS,
    _PROMPT_BLOCK_PER_BUCKET,
    _PROMPT_SNIPPET_CHAR_CAP,
    _balanced_prompt_snippets,
    _format_prompt_snippet,
)


_QUIETCART = {
    "product_name": "QuietCart",
    "product_description": (
        "A browser extension for online shoppers who want to stop "
        "impulse buying."
    ),
    "category_hint": "browser extension",
    "competitors_or_alternatives": ["Freedom", "Opal"],
}


class _SettingsAllOn:
    amazon_reviews_enabled = True
    amazon_reviews_runtime_enabled = True
    amazon_reviews_persona_injection_enabled = True
    amazon_reviews_same_category_only = True
    amazon_reviews_max_signals_per_run = 80
    amazon_reviews_max_signals_per_category = 40
    amazon_reviews_max_signals_per_competitor = 20
    amazon_reviews_max_signals_per_brand = 8
    amazon_reviews_max_signals_per_theme = 10


def _settings_with(
    *, enabled: bool, runtime: bool, persona_injection: bool,
) -> Any:
    class _S:
        amazon_reviews_enabled = enabled
        amazon_reviews_runtime_enabled = runtime
        amazon_reviews_persona_injection_enabled = persona_injection
        amazon_reviews_same_category_only = True
        amazon_reviews_max_signals_per_run = 80
        amazon_reviews_max_signals_per_category = 40
        amazon_reviews_max_signals_per_competitor = 20
        amazon_reviews_max_signals_per_brand = 8
        amazon_reviews_max_signals_per_theme = 10
    return _S()


def _exploding_sessionmaker(*a: Any, **kw: Any) -> Any:  # pragma: no cover
    raise AssertionError("opened DB session despite flags off")


def _pkg_to_candidate_pool_response(pkg: Any) -> tuple[list, Any, str | None]:
    """Phase 11C.7 compatibility helper. Earlier persona-injection
    fakes returned an `AmazonEvidencePackage` via
    `retrieve_for_product_brief`. The injector now calls the new
    `retrieve_candidate_pool_for_persona` instead, which returns a
    `(signals, CandidatePoolStats, category_matched)` tuple. This
    helper adapts a package to that tuple so the same fake-data
    fixtures keep working after 11C.7.
    """
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


# ---------------------------------------------------------------------------
# 1. Third gate defaults to False
# ---------------------------------------------------------------------------


def test_third_gate_defaults_false_on_settings() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.amazon_reviews_enabled is False
    assert s.amazon_reviews_runtime_enabled is False
    assert s.amazon_reviews_persona_injection_enabled is False


# ---------------------------------------------------------------------------
# 2. ANY gate off => None, no DB access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enabled,runtime,persona",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),    # the audit-only Phase-11C.2/3/4 path
        (True, False, True),
        (False, True, True),
        (False, False, True),
    ],
)
def test_persona_block_returns_none_when_any_gate_off(
    enabled: bool, runtime: bool, persona: bool,
) -> None:
    """Eight combinations of the three flags; the helper only returns
    a non-None block when ALL three are True. Sessionmaker is an
    exploding fake to prove no DB access when off."""
    settings = _settings_with(
        enabled=enabled, runtime=runtime, persona_injection=persona,
    )
    out = asyncio.run(build_amazon_persona_prompt_block(
        _QUIETCART,
        sessionmaker=_exploding_sessionmaker,  # type: ignore[arg-type]
        settings=settings,
    ))
    assert out is None


# ---------------------------------------------------------------------------
# 3 + 4. With all three gates on, the block respects cap + balance
# ---------------------------------------------------------------------------


def test_balanced_snippet_picker_respects_total_and_per_bucket_caps() -> None:
    """Direct unit test of the balancing helper."""
    from assembly.sources.amazon_reviews_provider.retrieval import (
        RetrievedSignal,
    )

    def _sig(stype: str, idx: int) -> RetrievedSignal:
        return RetrievedSignal(
            signal_type=stype,
            sentiment_bucket="negative" if stype != "praise" else "positive",
            theme=f"theme_{stype}_{idx}",
            category="Software",
            brand=f"B{idx}",
            product_title=f"P{idx}",
            rating=2 if stype != "praise" else 5,
            short_snippet=f"snippet {stype} {idx}",
            competitor_mention=None,
            use_case=None,
            verified_purchase=True,
            helpful_votes=1,
        )

    # 10 of each type — way over the cap.
    rows = [
        _sig(t, i)
        for t in _PROMPT_BLOCK_BUCKET_ORDER
        for i in range(10)
    ]
    picked = _balanced_prompt_snippets(rows)
    # Total cap.
    assert len(picked) == _PROMPT_BLOCK_MAX_SNIPPETS
    # Per-bucket cap.
    by_type: dict[str, int] = {}
    for s in picked:
        by_type[s.signal_type] = by_type.get(s.signal_type, 0) + 1
    for stype, count in by_type.items():
        assert count <= _PROMPT_BLOCK_PER_BUCKET, (
            f"{stype} exceeded per-bucket cap: {count}"
        )
    # Negative-leaning buckets should dominate the pool — the
    # bucket order puts them before praise + use_case + proof_need.
    negative_pool = sum(by_type.get(t, 0) for t in (
        "objection", "durability", "price", "trust",
        "setup", "support", "safety", "switch_reason",
        "return_reason",
    ))
    assert negative_pool >= 6, (
        f"persona block did not lean toward objection-side signals: "
        f"only {negative_pool}/12 were negative buckets"
    )


def test_format_prompt_snippet_caps_text_to_180_chars() -> None:
    from assembly.sources.amazon_reviews_provider.retrieval import (
        RetrievedSignal,
    )
    long_snippet = "x " * 300  # 600 chars
    s = RetrievedSignal(
        signal_type="objection",
        sentiment_bucket="negative",
        theme="t",
        category="Software",
        brand="b", product_title="p", rating=1,
        short_snippet=long_snippet,
        competitor_mention=None, use_case=None,
        verified_purchase=True, helpful_votes=0,
    )
    line = _format_prompt_snippet(s)
    # The bullet prefix `- [objection/negative] ` adds ~24 chars.
    # We care that the *snippet portion* respects the cap.
    assert _PROMPT_SNIPPET_CHAR_CAP == 180
    # End sentinel appears when truncated.
    assert line.endswith("…")
    # Whole line stays well under 250 chars.
    assert len(line) < 250


# ---------------------------------------------------------------------------
# 5. No forbidden fields appear in the block text
# ---------------------------------------------------------------------------


_FORBIDDEN_TOKENS_IN_PROMPT = (
    "source_review_hash", "user_id", "user_id_hash",
    "raw_text", "review_body", "image_url", "image_urls",
    "internal_id", "row_id",
)


def test_block_text_never_contains_forbidden_field_tokens() -> None:
    """End-to-end: with all three flags on and a synthetic retriever
    returning a populated package, the produced text block does not
    contain any forbidden token literal."""
    from assembly.sources.amazon_reviews_provider import (
        AmazonEvidencePackage, RetrievedSignal,
    )

    pkg = AmazonEvidencePackage(
        attempted=True,
        feature_flag_status={},
        category_matched="Software",
        signals=[
            RetrievedSignal(
                signal_type="objection",
                sentiment_bucket="negative",
                theme="generic_disappointment",
                category="Software",
                brand="BrandX", product_title="TitleX",
                rating=1,
                short_snippet=(
                    "never worked properly on my mac — couldn't get the "
                    "browser extension to install for online shoppers."
                ),
                competitor_mention=None, use_case=None,
                verified_purchase=True, helpful_votes=5,
            ),
            RetrievedSignal(
                signal_type="trust",
                sentiment_bucket="negative",
                theme="scam_suspicion",
                category="Software",
                brand="BrandY", product_title="TitleY",
                rating=2,
                short_snippet=(
                    "feels scammy — browser extension asks for payment "
                    "after install on online shopping sites."
                ),
                competitor_mention=None, use_case=None,
                verified_purchase=True, helpful_votes=8,
            ),
        ],
        distribution={"objection": 1, "trust": 1},
        brand_coverage=1.0, title_coverage=1.0,
        skipped_reasons={}, notes=[],
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return pkg

        async def retrieve_candidate_pool_for_persona(self, shape):
            return _pkg_to_candidate_pool_response(pkg)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        block = asyncio.run(build_amazon_persona_prompt_block(
            _QUIETCART,
            sessionmaker=MagicMock(),
            settings=_SettingsAllOn(),  # type: ignore[arg-type]
        ))
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]

    assert block is not None
    for token in _FORBIDDEN_TOKENS_IN_PROMPT:
        assert token not in block, (
            f"persona block leaked forbidden token {token!r}"
        )
    # Header explicitly labels the data as buyer-language NOT truth.
    assert "buyer language" in block.lower()
    assert "ground truth" in block.lower()


# ---------------------------------------------------------------------------
# 6. same_category_only: persona block surfaces signals only from
#    the matched category (synthetic mixed-category package
#    exercises the retriever's gate first).
# ---------------------------------------------------------------------------


def test_persona_block_surfaces_only_matched_category() -> None:
    """The Phase-11C.2 retriever gate keeps cross-category signals
    out of the package. The persona block faithfully renders only
    what the retriever returned."""
    from assembly.sources.amazon_reviews_provider import (
        AmazonEvidencePackage, RetrievedSignal,
    )

    # The retriever's same_category_only gate would have already
    # dropped the All_Beauty rows below — so the package we feed
    # the fake retriever returns ONLY Software rows. The persona
    # block must echo that.
    # Snippets must share words with the QuietCart brief so the
    # Phase-11C.6 relevance filter keeps them. Without overlap the
    # filter (active when persona_min_relevance > 0) would drop
    # them as off-topic before the same-category check runs.
    sig = lambda i: RetrievedSignal(  # noqa: E731
        signal_type="objection",
        sentiment_bucket="negative",
        theme="couldnt_setup",  # not generic_disappointment, so no theme penalty
        category="Software",
        brand=f"B{i}",
        product_title="Browser Extension Tool",
        rating=1,
        short_snippet=(
            f"browser extension issue {i} for online shoppers — "
            f"impulse buying control broke on Amazon checkout"
        ),
        competitor_mention=None, use_case=None,
        verified_purchase=True, helpful_votes=1,
    )
    pkg = AmazonEvidencePackage(
        attempted=True, feature_flag_status={},
        category_matched="Software",
        signals=[sig(i) for i in range(8)],
        distribution={"objection": 8},
        brand_coverage=1.0, title_coverage=1.0,
        skipped_reasons={"cross_category_filtered": 46},
        notes=[],
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return pkg

        async def retrieve_candidate_pool_for_persona(self, shape):
            return _pkg_to_candidate_pool_response(pkg)

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        block = asyncio.run(build_amazon_persona_prompt_block(
            _QUIETCART,
            sessionmaker=MagicMock(),
            settings=_SettingsAllOn(),  # type: ignore[arg-type]
        ))
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]
    assert block is not None
    # Header references the matched category.
    assert "category=Software" in block
    # No mention of other category names that the corpus contains.
    for forbidden_cat in ("All_Beauty", "Subscription_Boxes",
                          "Health_and_Personal_Care",
                          "Industrial_and_Scientific"):
        assert forbidden_cat not in block


# ---------------------------------------------------------------------------
# 7. Discussion-pipeline _build_block: prompt shape is identical
#    when amazon_persona_block is None
# ---------------------------------------------------------------------------


def test_discussion_pipeline_accepts_amazon_block_argument() -> None:
    """Phase 11C.5 added an optional kwarg to run_live_discussion.
    Default is None — production prompts stay byte-for-byte
    identical to Phase 11C.4."""
    from assembly.orchestration.live_discussion_pipeline import (
        run_live_discussion,
    )
    sig = inspect.signature(run_live_discussion)
    assert "amazon_persona_block" in sig.parameters
    p = sig.parameters["amazon_persona_block"]
    assert p.default is None


def test_discussion_pipeline_source_appends_amazon_block_conditionally() -> None:
    """Source-level static check: _build_block must guard the
    Amazon block on `amazon_persona_block` being truthy. A future
    refactor that unconditionally interpolates the block would
    leak it into prompts even when the flag is off."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_discussion_pipeline.py"
    ).read_text(encoding="utf-8")
    # The variable must appear inside a conditional, not as a bare
    # interpolation. Phase 11C.5 uses an `if amazon_persona_block`
    # check before appending.
    assert "amazon_persona_block" in src
    assert "if amazon_persona_block" in src


# ---------------------------------------------------------------------------
# 8. Audit dict echoes the third flag
# ---------------------------------------------------------------------------


def test_audit_dict_carries_persona_injection_flag() -> None:
    """When the audit helper runs (first two flags on, third off),
    the produced audit dict must STILL report the third flag's
    state. The audit helper itself ONLY gates on the first two
    flags — the third flag is observability-only on the audit
    path."""
    from assembly.sources.amazon_reviews_provider import (
        AmazonEvidencePackage,
    )
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_evidence_section_from_dict_brief,
    )

    class _FakeRetriever:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def retrieve_for_product_brief(self, shape):
            return AmazonEvidencePackage(
                attempted=True, feature_flag_status={},
                category_matched="Software",
                signals=[], distribution={},
                brand_coverage=0.0, title_coverage=0.0,
                skipped_reasons={}, notes=[],
            )

    import assembly.pipeline.amazon_evidence_injector as inj
    orig = inj.AmazonSignalRetriever
    inj.AmazonSignalRetriever = _FakeRetriever  # type: ignore[assignment]
    try:
        audit = asyncio.run(
            build_amazon_evidence_section_from_dict_brief(
                _QUIETCART,
                sessionmaker=MagicMock(),
                settings=_settings_with(
                    enabled=True, runtime=True,
                    persona_injection=False,
                ),  # type: ignore[arg-type]
            )
        )
    finally:
        inj.AmazonSignalRetriever = orig  # type: ignore[assignment]
    # All three flag-states present in the audit.
    assert audit["amazon_enabled"] is True
    assert audit["amazon_runtime_enabled"] is True
    assert audit["amazon_persona_injection_enabled"] is False


def test_audit_dict_persona_injection_flag_default_false() -> None:
    """When no flags are set, the audit dict reports
    persona_injection_enabled=False."""
    from assembly.pipeline.amazon_evidence_injector import (
        build_amazon_evidence_section_from_dict_brief,
    )

    audit = asyncio.run(build_amazon_evidence_section_from_dict_brief(
        _QUIETCART,
        sessionmaker=_exploding_sessionmaker,  # type: ignore[arg-type]
        settings=_settings_with(
            enabled=False, runtime=False, persona_injection=False,
        ),  # type: ignore[arg-type]
    ))
    assert audit["amazon_persona_injection_enabled"] is False


# ---------------------------------------------------------------------------
# 9. Drift — no HTTP imports in the new helper code
# ---------------------------------------------------------------------------


def test_persona_block_helper_has_no_http_imports() -> None:
    from assembly.pipeline import amazon_evidence_injector as inj
    src = inspect.getsource(inj)
    forbidden = ("requests", "httpx", "aiohttp", "selenium",
                 "playwright", "scrapy", "bs4", "beautifulsoup4")
    for token in forbidden:
        pattern = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(token)}\b",
            re.MULTILINE,
        )
        assert pattern.search(src) is None


# ---------------------------------------------------------------------------
# 10. Caller hook in live_founder_brief — source-level proof
# ---------------------------------------------------------------------------


def test_live_founder_brief_calls_persona_block_helper() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    ).read_text(encoding="utf-8")
    assert "build_amazon_persona_prompt_block" in src
    # And the result is passed to run_live_discussion.
    assert "amazon_persona_block=" in src
