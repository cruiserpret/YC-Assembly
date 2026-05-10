"""Tests for the Phase 5.5 retrieval provider layer + its integration into
the evidence builder."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from assembly.llm.errors import CutoffViolationError
from assembly.pipeline.evidence_builder import build_evidence
from assembly.retrieval.extraction_provider import (
    ExtractedPage,
    HttpxExtractionProvider,
    MockExtractionProvider,
    make_extracted_page,
)
from assembly.retrieval.factory import (
    get_extraction_provider,
    get_search_provider,
)
from assembly.retrieval.search_provider import (
    MockSearchProvider,
    SearchResult,
)
from assembly.schemas.brief import SimulationBriefIn


# ---------------------------------------------------------------------------
# MockSearchProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_search_returns_default_when_no_rules() -> None:
    p = MockSearchProvider()
    p.add_default([
        SearchResult(url="https://x.test/", title="X", snippet="…", rank=1),
    ])
    out = await p.search("anything")
    assert len(out) == 1
    assert out[0].url == "https://x.test/"


@pytest.mark.asyncio
async def test_mock_search_routes_by_query_substring() -> None:
    p = MockSearchProvider()
    p.add_results_for_query("pricing", [
        SearchResult(url="https://x.test/pricing", title="X Pricing", snippet="…", rank=1),
    ])
    p.add_default([])
    out = await p.search("Shopify Magic pricing")
    assert out[0].url == "https://x.test/pricing"
    out2 = await p.search("totally unrelated")
    assert out2 == []


@pytest.mark.asyncio
async def test_mock_search_records_calls() -> None:
    p = MockSearchProvider()
    p.add_default([])
    await p.search("q1", max_results=5)
    await p.search("q2", max_results=10)
    assert p.calls == [("q1", 5), ("q2", 10)]


# ---------------------------------------------------------------------------
# MockExtractionProvider — including cutoff-date guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_extraction_returns_canned_page() -> None:
    p = MockExtractionProvider()
    p.add_default(make_extracted_page(url="https://x.test/", text="Hello"))
    page = await p.extract("https://x.test/")
    assert page.text == "Hello"


@pytest.mark.asyncio
async def test_mock_extraction_blocks_live_under_cutoff() -> None:
    p = MockExtractionProvider()
    p.add_default(make_extracted_page(url="https://x.test/", text="…"))
    with pytest.raises(CutoffViolationError):
        await p.extract(
            "https://x.test/", cutoff_date=date(2026, 2, 1), snapshot=None
        )


@pytest.mark.asyncio
async def test_mock_extraction_with_snapshot_under_cutoff() -> None:
    """Snapshot bypasses the cutoff guard."""
    p = MockExtractionProvider()
    p.add_default(make_extracted_page(url="https://x.test/", text="snapshot text"))
    page = await p.extract(
        "https://x.test/", cutoff_date=date(2026, 2, 1), snapshot="/tmp/x.html"
    )
    assert page.text == "snapshot text"


# ---------------------------------------------------------------------------
# Factory respects env config
# ---------------------------------------------------------------------------


def test_factory_default_is_mock_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """No SEARCH_PROVIDER env override → mock."""
    from assembly.config import get_settings
    get_settings.cache_clear()
    p = get_search_provider()
    assert p.name == "mock"


def test_factory_default_is_httpx_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    from assembly.config import get_settings
    get_settings.cache_clear()
    p = get_extraction_provider()
    assert p.name == "httpx"


def test_factory_falls_back_to_mock_when_tavily_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for `search_provider=tavily` and the provider failing for any
    reason (missing key, missing SDK, network error during init) → factory
    logs and falls back to mock instead of crashing.

    We simulate the failure deterministically by patching
    TavilySearchProvider to raise on instantiation. This tests the
    fallback path without depending on whether the test environment has
    TAVILY_API_KEY set."""
    from assembly.config import Settings
    from assembly.llm.errors import LLMProviderError
    from assembly.retrieval import factory as factory_mod

    fake = Settings(search_provider="tavily")
    monkeypatch.setattr(factory_mod, "get_settings", lambda: fake)

    class _BoomTavily:
        def __init__(self, *args, **kwargs):
            raise LLMProviderError("simulated missing key")

    import assembly.retrieval.tavily as tavily_mod
    monkeypatch.setattr(tavily_mod, "TavilySearchProvider", _BoomTavily)

    p = get_search_provider()
    assert p.name == "mock"


# ---------------------------------------------------------------------------
# Retrieval integration into build_evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_integrates_into_evidence_builder(
    basic_brief: SimulationBriefIn,
) -> None:
    """When both providers are passed, build_evidence runs retrieval queries
    and turns hits into evidence_items."""
    sim_id = uuid4()
    search = MockSearchProvider()
    search.add_results_for_query("pricing", [
        SearchResult(
            url="https://compa.test/pricing",
            title="CompA Pricing",
            snippet="Plus plan: Custom",
            rank=1,
        ),
    ])
    search.add_default([])

    extractor = MockExtractionProvider()
    extractor.add_page(
        "compa.test/pricing",
        make_extracted_page(
            url="https://compa.test/pricing",
            text="Plus plan: Custom. Build a Shopify store with one click.",
            title="CompA Pricing",
        ),
    )

    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        search_provider=search,
        extraction_provider=extractor,
    )

    # We should see at least one direct retrieval evidence item with the
    # pricing source_type.
    pricing_items = [
        i for i in result.items
        if i.source_type == "pricing_page"
        and i.kind == "direct"
        and i.metadata.get("search_query")
    ]
    assert pricing_items, "expected at least one retrieved pricing_page evidence_item"
    assert pricing_items[0].source_url == "https://compa.test/pricing"
    assert "Plus plan" in pricing_items[0].content


@pytest.mark.asyncio
async def test_retrieval_emits_missing_when_no_search_results(
    basic_brief: SimulationBriefIn,
) -> None:
    """Search with empty results must produce kind=missing items."""
    sim_id = uuid4()
    search = MockSearchProvider()  # no rules → empty for every query
    extractor = MockExtractionProvider()

    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        search_provider=search,
        extraction_provider=extractor,
    )

    # At least one retrieval-driven missing evidence item with reason no_search_results
    no_results_missing = [
        i for i in result.items
        if i.kind == "missing"
        and i.metadata.get("reason") == "no_search_results"
    ]
    assert len(no_results_missing) >= 1


@pytest.mark.asyncio
async def test_retrieval_blocks_live_under_cutoff_date(
    basic_brief: SimulationBriefIn,
) -> None:
    """C3 preserved across retrieval: cutoff_date set + no snapshot ⇒
    extractor refuses, item recorded as missing with reason cutoff_violation."""
    sim_id = uuid4()
    search = MockSearchProvider()
    search.add_default([
        SearchResult(
            url="https://compa.test/pricing",
            title="CompA",
            snippet="…",
            rank=1,
        ),
    ])
    extractor = MockExtractionProvider()
    extractor.add_default(make_extracted_page(url="https://compa.test/pricing", text="x"))

    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        search_provider=search,
        extraction_provider=extractor,
        cutoff_date=date(2026, 2, 1),
    )

    cutoff_blocks = [
        i for i in result.items
        if i.kind == "missing"
        and i.metadata.get("reason") == "cutoff_violation"
    ]
    assert len(cutoff_blocks) >= 1


@pytest.mark.asyncio
async def test_retrieval_uses_snapshot_under_cutoff(
    basic_brief: SimulationBriefIn,
    tmp_path: Path,
) -> None:
    """Snapshot path lets retrieval proceed even with cutoff_date set —
    parity with url_fetcher behavior."""
    sim_id = uuid4()
    snap = tmp_path / "compa.html"
    snap.write_text("<html>CompA snapshot content</html>", encoding="utf-8")

    search = MockSearchProvider()
    search.add_default([
        SearchResult(url="https://compa.test/pricing", title="x", snippet="x", rank=1),
    ])
    extractor = MockExtractionProvider()
    extractor.add_default(
        make_extracted_page(
            url="https://compa.test/pricing", text="CompA snapshot content"
        )
    )

    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        search_provider=search,
        extraction_provider=extractor,
        cutoff_date=date(2026, 2, 1),
        snapshots={"https://compa.test/pricing": snap},
    )

    # Should have direct items from retrieval despite cutoff being set.
    pricing_direct = [
        i for i in result.items
        if i.kind == "direct"
        and i.source_url == "https://compa.test/pricing"
    ]
    assert len(pricing_direct) >= 1


@pytest.mark.asyncio
async def test_retrieval_results_become_anchors_for_society_builder(
    basic_brief: SimulationBriefIn,
) -> None:
    """Smoke test: an evidence_item produced by retrieval has a stable id
    that the society builder can later anchor a trait to."""
    sim_id = uuid4()
    search = MockSearchProvider()
    search.add_default([
        SearchResult(url="https://compa.test/", title="x", snippet="x", rank=1),
    ])
    extractor = MockExtractionProvider()
    extractor.add_default(make_extracted_page(url="https://compa.test/", text="text"))

    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        search_provider=search,
        extraction_provider=extractor,
    )

    retrieval_items = [
        i for i in result.items
        if i.metadata.get("search_query")
    ]
    assert retrieval_items
    # Each has a stable UUID
    ids = {i.id for i in retrieval_items}
    assert len(ids) == len(retrieval_items)


# ---------------------------------------------------------------------------
# httpx fallback (no key required) — uses the existing url_fetcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_httpx_extraction_via_snapshot(tmp_path: Path) -> None:
    """The httpx extractor implements ExtractionProvider via the existing
    url_fetcher — so snapshots Just Work."""
    snap = tmp_path / "page.html"
    snap.write_text(
        "<html><body><h1>Hello</h1><p>some text</p></body></html>",
        encoding="utf-8",
    )
    p = HttpxExtractionProvider()
    page = await p.extract("https://x.test/", snapshot=snap)
    assert "Hello" in page.text
    assert page.source_kind == "snapshot"
