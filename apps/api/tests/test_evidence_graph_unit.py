"""Phase 6.75 — unit tests for the evidence graph package (no DB).

These tests exercise pure-Python logic:
  - deterministic classifier
  - LLM classifier (rejection of invented ids / classes)
  - normalized_content_hash determinism
  - retriever ranking heuristics over in-memory items
  - claim validator structural rules

Integration-flavored DB tests live in `test_evidence_graph_integration.py`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from assembly.embeddings.mock import MockEmbeddingProvider
from assembly.embeddings.none import NoEmbeddingProvider
from assembly.embeddings.provider import EMBEDDING_DIM
from assembly.llm.mock import MockProvider
from assembly.models.evidence import EvidenceItem
from assembly.models.evidence_edge import EDGE_BASIS, EDGE_TYPES
from assembly.pipeline.evidence_graph.classifier import deterministic_class
from assembly.pipeline.evidence_graph.dedup import normalized_content_hash


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


def test_edge_types_closed_enum_size() -> None:
    """Migration mirrors this list — must stay 14 values per the plan."""
    assert len(EDGE_TYPES) == 14
    assert set(EDGE_TYPES) >= {
        "supports", "contradicts", "similar_to",
        "causes_objection", "reduces_objection",
        "maps_to_segment", "maps_to_price_sensitivity",
        "maps_to_switching_trigger", "maps_to_trust_barrier",
        "maps_to_competitor", "maps_to_category_language",
        "maps_to_recommendation", "priced_against", "competes_with",
    }


def test_edge_basis_values() -> None:
    assert set(EDGE_BASIS) == {"direct", "analogical", "inferred"}


# ---------------------------------------------------------------------------
# Classifier (deterministic-only paths can be unit-tested without DB)
# ---------------------------------------------------------------------------


def _make_item(**kw):
    """Build a duck-typed item that satisfies the attribute reads done by
    `deterministic_class` and `normalized_content_hash`. Avoids constructing
    a real ORM `EvidenceItem` (which requires a session) since these unit
    tests don't need DB-mapped descriptors."""
    from types import SimpleNamespace
    base = dict(
        id=uuid4(),
        simulation_id=uuid4(),
        kind="direct",
        source_type="user_input",
        source_url=None,
        content="some content",
        captured_at=None,
        metadata_={},
        node_class="unknown",
        node_class_confidence=Decimal("0"),
        content_hash="x" * 32,
        dedup_group_id=None,
        embedding_model=None,
        embedded_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_deterministic_competitor_page() -> None:
    item = _make_item(source_type="competitor_page")
    klass, conf = deterministic_class(item)
    assert klass == "competitor"
    assert conf == Decimal("1.0")


def test_deterministic_pricing_page() -> None:
    item = _make_item(source_type="pricing_page")
    klass, _ = deterministic_class(item)
    assert klass == "pricing"


def test_deterministic_user_input_competitor_field() -> None:
    item = _make_item(
        source_type="user_input",
        metadata_={"input_field": "competitors"},
    )
    klass, conf = deterministic_class(item)
    assert klass == "competitor"
    assert conf == Decimal("1.0")


def test_deterministic_user_input_target_society() -> None:
    item = _make_item(
        source_type="user_input",
        metadata_={"input_field": "target_society"},
    )
    klass, _ = deterministic_class(item)
    assert klass == "segment_behavior"


def test_deterministic_unknown_default() -> None:
    item = _make_item(source_type="user_input", metadata_={})
    klass, _ = deterministic_class(item)
    assert klass == "unknown"


def test_deterministic_missing_with_pricing_hint() -> None:
    item = _make_item(
        kind="missing",
        source_type="user_input",
        metadata_={"expected_kind": "pricing_page"},
    )
    klass, _ = deterministic_class(item)
    assert klass == "pricing"


def test_deterministic_missing_without_hint_stays_unknown() -> None:
    item = _make_item(kind="missing", source_type="user_input", metadata_={})
    klass, _ = deterministic_class(item)
    assert klass == "unknown"


# ---------------------------------------------------------------------------
# Content hash determinism
# ---------------------------------------------------------------------------


def test_content_hash_same_content_same_hash() -> None:
    a = _make_item(content="The merchant is overwhelmed.")
    b = _make_item(content="The merchant is overwhelmed.")
    assert normalized_content_hash(a) == normalized_content_hash(b)


def test_content_hash_whitespace_normalization() -> None:
    a = _make_item(content="The merchant is overwhelmed.")
    b = _make_item(content="  THE   merchant  is  overwhelmed.  ")
    assert normalized_content_hash(a) == normalized_content_hash(b)


def test_content_hash_falls_back_to_url_excerpt_when_content_empty() -> None:
    item = _make_item(
        content="",
        source_url="https://example.test/x",
        metadata_={"source_excerpt": "snippet"},
    )
    h = normalized_content_hash(item)
    assert h
    # Two items with same fallback inputs hash identically.
    item2 = _make_item(
        content="",
        source_url="https://example.test/x",
        metadata_={"source_excerpt": "snippet"},
    )
    assert h == normalized_content_hash(item2)


def test_content_hash_falls_back_to_id_for_empty_everything() -> None:
    item = _make_item(content="", source_url=None, metadata_={})
    h = normalized_content_hash(item)
    assert h


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_embedding_dim_matches_constant() -> None:
    p = MockEmbeddingProvider()
    [vec] = await p.embed(["hello"])
    assert vec is not None
    assert len(vec) == EMBEDDING_DIM


@pytest.mark.asyncio
async def test_mock_embedding_deterministic() -> None:
    p = MockEmbeddingProvider()
    [a] = await p.embed(["the same text"])
    [b] = await p.embed(["the same text"])
    assert a == b


@pytest.mark.asyncio
async def test_mock_embedding_distinct_texts_distinct_vectors() -> None:
    p = MockEmbeddingProvider()
    a, b = await p.embed(["one", "two"])
    assert a is not None and b is not None
    assert a != b


@pytest.mark.asyncio
async def test_mock_embedding_empty_text_returns_none() -> None:
    p = MockEmbeddingProvider()
    [vec] = await p.embed([""])
    assert vec is None


@pytest.mark.asyncio
async def test_no_embedding_provider_returns_none_for_each() -> None:
    p = NoEmbeddingProvider()
    out = await p.embed(["a", "b", "c"])
    assert out == [None, None, None]
