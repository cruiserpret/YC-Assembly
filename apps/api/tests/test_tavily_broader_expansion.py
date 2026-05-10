"""Phase 8.2F.6 — broader human-signal expansion tests.

Asserts:
  - BROADER_HUMAN_SIGNAL_QUERIES has the right size (12–15)
  - every entry maps to a known StakeholderCategory value
  - the catalog targets the three audit-flagged missing categories
    (dtc_founder_brand_control, freelancer_using_merchant,
    lock_in_worried_operator) with at least one query each
  - `for_broader_human_signal_expansion` factory wires:
        * the broader catalog
        * `operator_run=True`
        * `run_purpose='phase_8_2f_6_broader_human_signal_expansion'`
        * caps 15 / 10 / 100
        * `query_to_category` propagated
  - `target_missing_category` propagates to metadata at
    normalize-payload time
  - default smoke-test caps unchanged (5 / 5 / 25)
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from assembly.pipeline.ingestion.run_summary import RawSourcePayload
from assembly.pipeline.ingestion.tavily_adapter import (
    BROADER_HUMAN_SIGNAL_QUERIES,
    HUMAN_SIGNAL_QUERIES,
    TavilyResultMetadata,
    TavilySearchExtractAdapter,
    _result_to_payload,
)
from assembly.pipeline.persona_relevance.rubric import StakeholderCategory


# ---------------------------------------------------------------------------
# Catalog shape
# ---------------------------------------------------------------------------


def test_broader_catalog_size_within_12_to_15() -> None:
    assert 12 <= len(BROADER_HUMAN_SIGNAL_QUERIES) <= 15


def test_broader_catalog_distinct_from_existing_human_signal_set() -> None:
    """No literal duplication of the Phase 8.2F.5 catalog (we want
    different queries aimed at different gaps)."""
    overlap = set(BROADER_HUMAN_SIGNAL_QUERIES.keys()) & set(HUMAN_SIGNAL_QUERIES)
    assert overlap == set(), (
        f"Broader catalog must not duplicate Phase 8.2F.5 queries: {overlap}"
    )


def test_broader_catalog_categories_match_stakeholder_enum() -> None:
    valid_values = {c.value for c in StakeholderCategory}
    invalid: list[tuple[str, str]] = []
    for q, cat in BROADER_HUMAN_SIGNAL_QUERIES.items():
        if cat not in valid_values:
            invalid.append((q, cat))
    assert invalid == [], (
        f"Some target_missing_category values aren't in StakeholderCategory: "
        f"{invalid}"
    )


def test_broader_catalog_covers_audit_missing_categories() -> None:
    """The Phase 8.2F.7 audit found these three categories with zero
    coverage. The broader catalog must target each with at least one
    query."""
    targets = set(BROADER_HUMAN_SIGNAL_QUERIES.values())
    audit_missing = {
        StakeholderCategory.DTC_FOUNDER_BRAND_CONTROL.value,
        StakeholderCategory.FREELANCER_USING_MERCHANT.value,
        StakeholderCategory.LOCK_IN_WORRIED_OPERATOR.value,
    }
    missing = audit_missing - targets
    assert missing == set(), (
        f"Broader catalog does NOT target audit-flagged missing categories: "
        f"{missing}"
    )


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def test_for_broader_human_signal_expansion_uses_broader_catalog() -> None:
    a = TavilySearchExtractAdapter.for_broader_human_signal_expansion()
    assert len(a._queries) == len(BROADER_HUMAN_SIGNAL_QUERIES)
    assert set(a._queries) == set(BROADER_HUMAN_SIGNAL_QUERIES.keys())
    assert a._query_to_category == dict(BROADER_HUMAN_SIGNAL_QUERIES)


def test_for_broader_human_signal_expansion_sets_operator_flags() -> None:
    a = TavilySearchExtractAdapter.for_broader_human_signal_expansion()
    assert a._operator_run is True
    assert a._test_fixture is False
    assert a._run_purpose == "phase_8_2f_6_broader_human_signal_expansion"


def test_for_broader_human_signal_expansion_uses_broader_caps() -> None:
    a = TavilySearchExtractAdapter.for_broader_human_signal_expansion()
    assert a.max_results_per_query == 10
    assert a.max_accepted == 100


def test_phase_8_2f_5_factory_caps_unchanged_after_82f_6() -> None:
    """Phase 8.2F.5's tier (10/10/75) must not regress."""
    a = TavilySearchExtractAdapter.for_human_signal_expansion()
    assert a.max_results_per_query == 10
    assert a.max_accepted == 75
    assert len(a._queries) == 10


def test_default_smoke_test_caps_unchanged_after_82f_6() -> None:
    """Phase 8.2E smoke caps (5/5/25) must not regress."""
    a = TavilySearchExtractAdapter()
    assert a.max_results_per_query == 5
    assert a.max_accepted == 25


# ---------------------------------------------------------------------------
# target_missing_category metadata propagation
# ---------------------------------------------------------------------------


def test_result_to_payload_propagates_target_category() -> None:
    p = _result_to_payload(
        result={
            "url": "https://reddit.example.test/r/shopify/x",
            "title": "x",
            "content": "I'm a Shopify merchant; lock-in is a concern",
            "score": 0.6,
        },
        query="Shopify merchant lock-in concerns",
        rank=0,
        captured_at=datetime.now(UTC),
        target_missing_category="lock_in_worried_operator",
    )
    assert p is not None
    assert p.metadata["target_missing_category"] == "lock_in_worried_operator"


def test_result_to_payload_omits_target_category_when_none() -> None:
    p = _result_to_payload(
        result={
            "url": "https://example.test/x",
            "title": "x",
            "content": "I'm a merchant; some content here",
            "score": 0.5,
        },
        query="generic",
        rank=0,
        captured_at=datetime.now(UTC),
        target_missing_category=None,
    )
    assert p is not None
    assert "target_missing_category" not in p.metadata


def test_normalize_payload_carries_target_category_into_validated_metadata() -> None:
    a = TavilySearchExtractAdapter.for_broader_human_signal_expansion()
    raw = RawSourcePayload(
        source_url="https://reddit.example.test/r/shopify/threads/lockin",
        captured_at=datetime.now(UTC),
        content=(
            "I'm a Shopify merchant doing $20k/month and I'm worried "
            "about lock-in if I switch to an AI store builder. The "
            "switching cost back to my current setup would be huge."
        ),
        raw_handle=None,
        metadata={
            "query": "Shopify merchant lock-in concerns",
            "result_rank": 0,
            "title": "lock-in concerns thread",
            "domain": "reddit.example.test",
            "tavily_score": 0.7,
            "published_date": None,
            "target_missing_category": "lock_in_worried_operator",
        },
    )
    out = a.normalize_payload(raw)
    md = TavilyResultMetadata.model_validate(out.metadata)
    assert md.target_missing_category == "lock_in_worried_operator"
    assert md.run_purpose == "phase_8_2f_6_broader_human_signal_expansion"
    assert md.operator_run is True
    assert md.test_fixture is False


# ---------------------------------------------------------------------------
# Direct-string queries with no category map still work (back-compat)
# ---------------------------------------------------------------------------


def test_string_only_queries_still_work_without_category_map() -> None:
    a = TavilySearchExtractAdapter(queries=["plain query"])
    assert a._queries == ["plain query"]
    assert a._query_to_category == {}
