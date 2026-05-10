"""Phase 8.2F.5 — human-signal query strategy + metadata tests.

Asserts:
  - HUMAN_SIGNAL_QUERIES targets review/forum/comment/complaint intent
    (not generic SEO/product/listicle queries)
  - the `for_human_signal_expansion` factory wires:
        * the human-signal query catalog
        * `operator_run=True`
        * `run_purpose='phase_8_2f_5_human_signal_expansion'`
        * the expansion caps (10 queries × 10 results × 75 accepted)
  - `normalize_payload` flips `likely_human_signal_candidate=true` for
    Reddit / community.shopify.com / forum-shaped URLs
  - `normalize_payload` flips `likely_human_signal_candidate=false` for
    pricing / agency / blog URLs
  - operator-run rows carry `test_fixture=False` so the safe cleanup
    fixture cannot wipe them
"""
from __future__ import annotations

import re

import pytest

from assembly.pipeline.ingestion.tavily_adapter import (
    HUMAN_SIGNAL_QUERIES,
    TavilyResultMetadata,
    TavilySearchExtractAdapter,
    _looks_like_human_signal_candidate,
)


# ---------------------------------------------------------------------------
# 1) Query strategy intent
# ---------------------------------------------------------------------------


def test_human_signal_query_set_size_is_10() -> None:
    assert len(HUMAN_SIGNAL_QUERIES) == 10


def test_human_signal_queries_target_human_voice() -> None:
    """At least 7 of 10 queries should explicitly target review /
    forum / comment / complaint / discussion intent."""
    pattern = re.compile(
        r"\b(?:complaints?|concerns?|review|forum|community|discussion|"
        r"comments?|trust|merchant|founder|merchants|skepticism|"
        r"pricing|brand[-\s]?control)\b",
        re.IGNORECASE,
    )
    hits = sum(1 for q in HUMAN_SIGNAL_QUERIES if pattern.search(q))
    assert hits >= 7, (
        f"Only {hits}/{len(HUMAN_SIGNAL_QUERIES)} queries carry human-signal "
        "intent. Phase 8.2F.5 needs ≥ 7 queries that explicitly aim at "
        "review/forum/comment/complaint/discussion surfaces."
    )


def test_human_signal_queries_avoid_marketing_intent() -> None:
    """No query should read like a marketing / SEO / listicle query
    (e.g. 'top 10 best Shopify SEO plugins')."""
    forbidden = re.compile(
        r"\btop\s+\d+|\bbest\s+\d+|\b\d+\s+(?:best|top|tools|tips|"
        r"strategies|examples|reasons)\b",
        re.IGNORECASE,
    )
    offenders = [q for q in HUMAN_SIGNAL_QUERIES if forbidden.search(q)]
    assert offenders == [], (
        f"Human-signal queries must not look like marketing/SEO listicles: "
        f"{offenders}"
    )


def test_some_queries_use_site_operator_for_known_human_domains() -> None:
    """At least 5 queries should pin to a known human-signal domain via
    the `site:` operator. Tavily may not honour `site:` perfectly but
    the intent must be encoded in the query strings."""
    site_pinned = [
        q for q in HUMAN_SIGNAL_QUERIES
        if re.search(
            r"site:(?:community\.shopify\.com|reddit\.com|"
            r"news\.ycombinator\.com)",
            q, re.IGNORECASE,
        )
    ]
    assert len(site_pinned) >= 5


# ---------------------------------------------------------------------------
# 2) Expansion factory wires the right run-tracking + caps
# ---------------------------------------------------------------------------


def test_for_human_signal_expansion_uses_human_signal_queries() -> None:
    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    # Adapter exposes `_queries` privately; we only assert via
    # the public surface that it accepted the catalog. Length is 10.
    assert len(a._queries) == 10
    assert a._queries == list(HUMAN_SIGNAL_QUERIES)


def test_for_human_signal_expansion_sets_operator_flags() -> None:
    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    assert a._operator_run is True
    assert a._test_fixture is False
    assert a._run_purpose == "phase_8_2f_5_human_signal_expansion"


def test_for_human_signal_expansion_uses_expansion_caps() -> None:
    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    assert a.max_results_per_query == 10
    assert a.max_accepted == 75


def test_default_constructor_uses_smoke_test_caps() -> None:
    a = TavilySearchExtractAdapter()
    assert a.max_results_per_query == 5
    assert a.max_accepted == 25


def test_caps_cannot_exceed_expansion_ceiling() -> None:
    """Phase 8.2I.1 raised the expansion ceilings to
    30 queries × 10 results × 120 accepted (8.2F.6 was 15/10/100).
    Anything beyond is structurally rejected at construction."""
    with pytest.raises(ValueError):
        TavilySearchExtractAdapter(max_queries=31)
    with pytest.raises(ValueError):
        TavilySearchExtractAdapter(max_results_per_query=11)
    with pytest.raises(ValueError):
        TavilySearchExtractAdapter(max_accepted=121)


# ---------------------------------------------------------------------------
# 3) Likely-human-signal heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://community.shopify.com/c/shopify-discussions/the-real-cost-of-plugin-bloat/td-p/12345",
    "https://www.reddit.com/r/shopify/comments/aaa/i_switched_from_bigcommerce/",
    "https://news.ycombinator.com/item?id=123",
    "https://forum.example.test/threads/12345",
])
def test_known_human_signal_url_flagged_true(url: str) -> None:
    assert _looks_like_human_signal_candidate(
        url=url,
        title="Public discussion",
        snippet=(
            "I'm a Shopify merchant doing $30k/month and I switched from "
            "BigCommerce last year. My plugin stack is overwhelming."
        ),
    ) is True


@pytest.mark.parametrize("url", [
    "https://example.test/pricing",
    "https://agency.example.test/services/shopify",
    "https://blog.example.test/best-shopify-seo-plugins-2025",
    "https://expertdesignstudio.example.test/about",
])
def test_marketing_url_flagged_false(url: str) -> None:
    assert _looks_like_human_signal_candidate(
        url=url,
        title="Trusted agency for Shopify merchants",
        snippet=(
            "We help merchants launch faster. Trusted by 1,200+ brands. "
            "Get started today with our agency."
        ),
    ) is False


def test_first_person_in_snippet_alone_does_not_flip_marketing_url() -> None:
    """Even when a snippet has first-person fragments, an obviously
    marketing URL still classifies as not human-signal — the URL shape
    has structural priority."""
    flagged = _looks_like_human_signal_candidate(
        url="https://agency.example.test/pricing",
        title="Our pricing plans",
        snippet=(
            "I've helped 100+ merchants launch. Trusted by leading brands. "
            "Get started today."
        ),
    )
    assert flagged is False


# ---------------------------------------------------------------------------
# 4) normalize_payload merges run-tracking flags + heuristic
# ---------------------------------------------------------------------------


def test_normalize_payload_injects_human_signal_flag_for_forum_url() -> None:
    from datetime import UTC, datetime
    from assembly.pipeline.ingestion.run_summary import RawSourcePayload

    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    raw = RawSourcePayload(
        source_url="https://community.shopify.com/c/discussion/12345",
        captured_at=datetime.now(UTC),
        content=(
            "I'm a Shopify merchant doing $30k/month and I'm fed up with "
            "paying for plugins. I tried switching to a consolidated tool."
        ),
        raw_handle=None,
        metadata={
            "query": HUMAN_SIGNAL_QUERIES[0],
            "result_rank": 0,
            "title": "Public discussion: plugin consolidation",
            "domain": "community.shopify.com",
            "tavily_score": 0.9,
            "published_date": None,
        },
    )
    out = a.normalize_payload(raw)
    md = TavilyResultMetadata.model_validate(out.metadata)
    assert md.likely_human_signal_candidate is True
    assert md.operator_run is True
    assert md.test_fixture is False
    assert md.run_purpose == "phase_8_2f_5_human_signal_expansion"


def test_normalize_payload_injects_low_signal_flag_for_marketing_url() -> None:
    from datetime import UTC, datetime
    from assembly.pipeline.ingestion.run_summary import RawSourcePayload

    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    raw = RawSourcePayload(
        source_url="https://agency.example.test/services/shopify",
        captured_at=datetime.now(UTC),
        content=(
            "We help merchants launch their Shopify stores faster. "
            "Trusted by 1,200+ brands. Book a demo today."
        ),
        raw_handle=None,
        metadata={
            "query": "Shopify merchants plugin bloat complaints",
            "result_rank": 0,
            "title": "Trusted Shopify agency",
            "domain": "agency.example.test",
            "tavily_score": 0.3,
            "published_date": None,
        },
    )
    out = a.normalize_payload(raw)
    md = TavilyResultMetadata.model_validate(out.metadata)
    assert md.likely_human_signal_candidate is False
    assert md.operator_run is True
    assert md.test_fixture is False


def test_normalize_payload_preserves_explicit_test_fixture_flag() -> None:
    """If a fixture pre-sets `test_fixture=true` in raw metadata, the
    adapter must not silently flip it via its own per-instance default."""
    from datetime import UTC, datetime
    from assembly.pipeline.ingestion.run_summary import RawSourcePayload

    a = TavilySearchExtractAdapter()  # operator_run=False, test_fixture=False
    raw = RawSourcePayload(
        source_url="https://community.shopify.com/c/discussion/abc",
        captured_at=datetime.now(UTC),
        content=(
            "I am a Shopify merchant doing about $20k a month and I "
            "switched from BigCommerce. The plugin stack is overwhelming."
        ),
        raw_handle=None,
        metadata={
            "query": "Shopify merchants plugin bloat complaints",
            "result_rank": 0,
            "title": None,
            "domain": "community.shopify.com",
            "tavily_score": 0.5,
            "published_date": None,
            "test_fixture": True,
            "run_purpose": "test_fixture",
            "operator_run": False,
        },
    )
    out = a.normalize_payload(raw)
    md = TavilyResultMetadata.model_validate(out.metadata)
    assert md.test_fixture is True
    assert md.operator_run is False
    assert md.run_purpose == "test_fixture"
