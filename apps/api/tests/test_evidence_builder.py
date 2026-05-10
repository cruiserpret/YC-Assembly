"""Tests for evidence builder: C1 (source-bound extraction), C4 (deterministic
EXPECTED_EVIDENCE_BY_PRODUCT_TYPE), and end-to-end behavior."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from assembly.llm.mock import MockProvider
from assembly.pipeline.evidence_builder import (
    EXPECTED_EVIDENCE_BY_PRODUCT_TYPE,
    PendingEvidenceItem,
    build_evidence,
    expected_evidence_for,
    extract_category_language,
)
from assembly.pipeline.url_fetcher import FetchedPage
from assembly.schemas.brief import SimulationBriefIn
from datetime import UTC, datetime


# ---------------------------------------------------------------------------
# C4 — EXPECTED_EVIDENCE_BY_PRODUCT_TYPE is deterministic
# ---------------------------------------------------------------------------


def test_expected_evidence_known_product_type() -> None:
    expected = expected_evidence_for("ai_commerce_platform")
    assert "competitor_page" in expected
    assert "pricing_page" in expected
    assert "public_review" in expected
    assert "category_language" in expected


def test_expected_evidence_falls_back_to_default() -> None:
    expected = expected_evidence_for("totally_unknown_product_type")
    default = EXPECTED_EVIDENCE_BY_PRODUCT_TYPE["default"]
    assert expected == default


def test_expected_evidence_normalizes_product_type() -> None:
    """ai-commerce-platform / AI_COMMERCE_PLATFORM / 'AI commerce platform'
    should all map to the same key."""
    a = expected_evidence_for("AI Commerce Platform")
    b = expected_evidence_for("ai-commerce-platform")
    c = expected_evidence_for("ai_commerce_platform")
    assert a == b == c


def test_expected_evidence_returns_a_copy() -> None:
    """Mutating the returned list must not affect the static dict."""
    a = expected_evidence_for("ai_commerce_platform")
    a.append("FAKE_TYPE")
    assert "FAKE_TYPE" not in EXPECTED_EVIDENCE_BY_PRODUCT_TYPE["ai_commerce_platform"]


# ---------------------------------------------------------------------------
# C1 — source-bound extraction
# ---------------------------------------------------------------------------


def _make_page(url: str, text: str) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        captured_at=datetime.now(UTC),
        status_code=200,
        content_type="text/html",
        text=text,
        truncated=False,
        source_kind="snapshot",
        snapshot_path=None,
    )


@pytest.mark.asyncio
async def test_extractor_drops_phrases_not_in_pages(bypass_cost_guarded_chat) -> None:
    """C1: phrases the LLM emits but that don't appear in any page must be dropped."""
    pages = [
        _make_page(
            "https://compa.test/",
            "Plus plan: Custom. Build a Shopify store with one click. "
            "Trusted by 3000 merchants.",
        ),
    ]
    p = MockProvider()
    # The mock returns 4 phrases; 2 are in the page text, 2 are not.
    p.add_response_for_stage("evidence_extractor", json.dumps({
        "phrases": [
            {
                "phrase": "Plus plan: Custom",
                "source_url": "https://compa.test/",
                "source_excerpt": "Plus plan: Custom",
            },
            {
                "phrase": "Build a Shopify store with one click",
                "source_url": "https://compa.test/",
                "source_excerpt": "Build a Shopify store with one click",
            },
            {
                "phrase": "INVENTED CATEGORY PHRASE",
                "source_url": "https://compa.test/",
                "source_excerpt": "INVENTED CATEGORY PHRASE",
            },
            {
                "phrase": "Plus plan: Custom",
                "source_url": "https://different-url.test/not-fetched",
                "source_excerpt": "Plus plan: Custom",
            },
        ],
    }))

    extracted = await extract_category_language(
        pages=pages,
        provider=p,
        sessionmaker=None,
        model="mock",
        simulation_id=uuid4(),
    )

    phrases = {e.phrase for e in extracted}
    assert "Plus plan: Custom" in phrases
    assert "Build a Shopify store with one click" in phrases
    assert "INVENTED CATEGORY PHRASE" not in phrases
    # The fourth case (correct phrase but wrong URL) must also be dropped.
    urls = {e.source_url for e in extracted}
    assert "https://different-url.test/not-fetched" not in urls


@pytest.mark.asyncio
async def test_extractor_returns_empty_when_no_pages(bypass_cost_guarded_chat) -> None:
    p = MockProvider()
    extracted = await extract_category_language(
        pages=[], provider=p, sessionmaker=None, model="mock", simulation_id=uuid4()
    )
    assert extracted == []


@pytest.mark.asyncio
async def test_extractor_handles_provider_failure_gracefully(bypass_cost_guarded_chat) -> None:
    """If the LLM call fails completely, return empty (not crash)."""
    pages = [_make_page("https://compa.test/", "anything")]
    p = MockProvider()  # no rules, will raise
    extracted = await extract_category_language(
        pages=pages, provider=p, sessionmaker=None, model="mock", simulation_id=uuid4()
    )
    assert extracted == []


# ---------------------------------------------------------------------------
# build_evidence end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_evidence_produces_direct_for_user_inputs(
    basic_brief: SimulationBriefIn,
) -> None:
    sim_id = uuid4()
    result = await build_evidence(brief=basic_brief, simulation_id=sim_id)

    direct = [i for i in result.items if i.kind == "direct"]
    assert any(i.metadata.get("input_field") == "user_product_type" for i in direct)
    assert any(i.metadata.get("input_field") == "user_description" for i in direct)
    assert any(i.metadata.get("input_field") == "user_target_society" for i in direct)


@pytest.mark.asyncio
async def test_build_evidence_emits_missing_for_expected_unsupplied(
    basic_brief: SimulationBriefIn,
) -> None:
    """C4: every entry in EXPECTED_EVIDENCE_BY_PRODUCT_TYPE['ai_commerce_platform']
    not satisfied by user input or a fetched page becomes kind=missing."""
    sim_id = uuid4()
    result = await build_evidence(brief=basic_brief, simulation_id=sim_id)

    missing_types = {i.source_type for i in result.items if i.kind == "missing"}
    expected = set(expected_evidence_for(basic_brief.product_type))
    # We did not fetch any URLs (no real network), so all expected types
    # except those satisfied by user input should be in missing.
    # user_input doesn't satisfy competitor_page/pricing_page/public_review
    # by source_type, so those should all be missing.
    assert "competitor_page" in missing_types
    assert "pricing_page" in missing_types
    assert "public_review" in missing_types


@pytest.mark.asyncio
async def test_build_evidence_cutoff_blocks_live_fetch(
    basic_brief: SimulationBriefIn,
) -> None:
    """C3 integration: with cutoff_date set and no snapshot for a competitor URL,
    we get a kind=missing item documenting the gap, not a live fetch."""
    from datetime import date

    sim_id = uuid4()
    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        cutoff_date=date(2026, 2, 1),
        snapshots=None,
    )

    # Look for cutoff_violation reason in metadata of any missing item
    cutoff_blocks = [
        i for i in result.items
        if i.kind == "missing"
        and i.metadata.get("reason") == "cutoff_violation"
    ]
    # The basic_brief has a competitor URL → should have a cutoff_violation entry
    assert len(cutoff_blocks) >= 1


@pytest.mark.asyncio
async def test_build_evidence_uses_snapshot_under_cutoff(
    basic_brief: SimulationBriefIn,
    tmp_path: Path,
) -> None:
    """C3: with cutoff_date AND a snapshot for the URL, we read the snapshot."""
    from datetime import date

    snap = tmp_path / "magic.html"
    snap.write_text(
        "<html><body>Shopify Magic — AI for stores. Enterprise pricing.</body></html>",
        encoding="utf-8",
    )

    sim_id = uuid4()
    result = await build_evidence(
        brief=basic_brief,
        simulation_id=sim_id,
        cutoff_date=date(2026, 2, 1),
        snapshots={"https://example.com/magic": snap},
    )

    snapshot_items = [
        i for i in result.items
        if i.metadata.get("source_kind") == "snapshot"
    ]
    assert len(snapshot_items) == 1
    assert "Shopify Magic" in snapshot_items[0].content


def test_pending_item_carries_simulation_id(basic_brief: SimulationBriefIn) -> None:
    """All items must reference the simulation_id given to build_evidence."""
    # synchronous sanity: the dataclass stores what we pass
    sim_id = uuid4()
    item = PendingEvidenceItem(
        id=uuid4(),
        simulation_id=sim_id,
        kind="direct",
        source_type="user_input",
        source_url=None,
        content="x",
        captured_at=None,
    )
    assert item.simulation_id == sim_id
