"""Tests for the intake parser, including C2 provenance enforcement."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.mock import MockProvider
from assembly.pipeline.intake_parser import (
    extract_sources,
    parse_brief,
)
from assembly.schemas.brief import SimulationBriefIn
from assembly.schemas.product_intelligence import (
    DerivedString,
    ProductIntelligenceObject,
    ProvenanceKind,
    SourceField,
    verify_provenance,
)


# ---------------------------------------------------------------------------
# extract_sources
# ---------------------------------------------------------------------------


def test_extract_sources_covers_all_input_fields(basic_brief: SimulationBriefIn) -> None:
    s = extract_sources(basic_brief)
    assert s[SourceField.PRODUCT_TYPE] == "ai_commerce_platform"
    assert s[SourceField.PRODUCT_NAME] == "Amboras"
    assert "Amboras is an AI commerce platform" in s[SourceField.DESCRIPTION]
    assert "Shopify Magic" in s[SourceField.COMPETITORS]
    assert "Shopify merchants doing $10k-$80k/month" in s[SourceField.TARGET_SOCIETY]
    assert "$49/mo starter" in s[SourceField.PRICE_STRUCTURE]


# ---------------------------------------------------------------------------
# Provenance verifier — direct unit tests
# ---------------------------------------------------------------------------


def _make_pio_with_field(field_value: DerivedString, **overrides) -> ProductIntelligenceObject:
    """Helper: build a minimal valid PIO and override the named field."""
    base = {
        "product_type": DerivedString(
            value="ai_commerce_platform",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.PRODUCT_TYPE,
            source_excerpt="ai_commerce_platform",
        ),
        "product_name": DerivedString(
            value="Amboras",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.PRODUCT_NAME,
            source_excerpt="Amboras",
        ),
        "description_normalized": DerivedString(
            value="something",
            provenance=ProvenanceKind.PARAPHRASE,
            source_field=SourceField.DESCRIPTION,
            source_excerpt="AI commerce platform",
        ),
        "price_summary": DerivedString(
            value="monthly",
            provenance=ProvenanceKind.PARAPHRASE,
            source_field=SourceField.PRICE_STRUCTURE,
            source_excerpt="subscription_monthly",
        ),
        "target_society_summary": DerivedString(
            value="Shopify merchants",
            provenance=ProvenanceKind.PARAPHRASE,
            source_field=SourceField.TARGET_SOCIETY,
            source_excerpt="Shopify merchants",
        ),
    }
    base.update(overrides)
    return ProductIntelligenceObject(**base)


def test_verify_provenance_passes_clean_pio(basic_brief: SimulationBriefIn) -> None:
    pio = _make_pio_with_field(
        DerivedString(
            value="x",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.PRODUCT_NAME,
            source_excerpt="Amboras",
        )
    )
    sources = extract_sources(basic_brief)
    errors = verify_provenance(pio, sources=sources)
    assert errors == []


def test_verify_provenance_rejects_excerpt_not_in_source(basic_brief: SimulationBriefIn) -> None:
    pio = _make_pio_with_field(
        DerivedString(
            value="invented thing",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.DESCRIPTION,
            source_excerpt="this phrase definitely does not appear in the brief",
        ),
        product_type=_make_pio_with_field(  # noqa: F841 unused since we override below
            DerivedString(value="x", provenance=ProvenanceKind.VERBATIM,
                          source_field=SourceField.PRODUCT_TYPE,
                          source_excerpt="ai_commerce_platform"),
        ).product_type,
    )
    # Override the description to be the bogus one
    pio = pio.model_copy(update={
        "description_normalized": DerivedString(
            value="invented thing",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.DESCRIPTION,
            source_excerpt="this phrase definitely does not appear in the brief",
        ),
    })
    errors = verify_provenance(pio, sources=extract_sources(basic_brief))
    assert any(e.rule == "provenance.excerpt_not_in_source" for e in errors)


def test_verify_provenance_accepts_assumption_with_rationale(basic_brief: SimulationBriefIn) -> None:
    pio = _make_pio_with_field(
        DerivedString(
            value="x",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.PRODUCT_NAME,
            source_excerpt="Amboras",
        ),
        novelty_type=DerivedString(
            value="ux_improvement",
            provenance=ProvenanceKind.ASSUMPTION,
            assumption_rationale="not stated in brief; inferred from automation framing",
        ),
    )
    errors = verify_provenance(pio, sources=extract_sources(basic_brief))
    assert errors == []


def test_pydantic_rejects_assumption_without_rationale() -> None:
    """C2: structural validation should reject assumption with no rationale."""
    with pytest.raises(Exception):  # pydantic ValidationError
        DerivedString(
            value="x",
            provenance=ProvenanceKind.ASSUMPTION,
            assumption_rationale=None,
        )


def test_pydantic_rejects_verbatim_without_source_field() -> None:
    with pytest.raises(Exception):
        DerivedString(
            value="x",
            provenance=ProvenanceKind.VERBATIM,
            source_field=None,
            source_excerpt="x",
        )


def test_pydantic_rejects_verbatim_with_assumption_rationale_set() -> None:
    """assumption_rationale must be null for non-assumption provenance."""
    with pytest.raises(Exception):
        DerivedString(
            value="x",
            provenance=ProvenanceKind.VERBATIM,
            source_field=SourceField.PRODUCT_NAME,
            source_excerpt="Amboras",
            assumption_rationale="should not be set here",
        )


# ---------------------------------------------------------------------------
# parse_brief — end-to-end with MockProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_brief_clean_path(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio_json: str,
) -> None:
    p = MockProvider()
    p.add_default(valid_pio_json)

    result = await parse_brief(
        basic_brief,
        provider=p,
        sessionmaker=None,
        simulation_id=uuid4(),
        model="mock",
    )

    assert result.repair_attempts_used == 0
    assert result.product_intelligence.product_name.value == "Amboras"
    assert result.product_intelligence.trust_risks
    assert len(p.calls) == 1


@pytest.mark.asyncio
async def test_parse_brief_repairs_bad_provenance(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio_json: str,
) -> None:
    """First response has an invented excerpt; second response is clean."""
    bad = json.loads(valid_pio_json)
    # break: claim verbatim from description with a phrase not in the brief
    bad["description_normalized"] = {
        "value": "something invented",
        "provenance": "verbatim",
        "source_field": "user_description",
        "source_excerpt": "THIS DOES NOT APPEAR IN THE USER BRIEF",
    }
    p = MockProvider()
    p.add_response(predicate=lambda *_: True, response=json.dumps(bad))
    p.add_default(valid_pio_json)

    result = await parse_brief(
        basic_brief,
        provider=p,
        sessionmaker=None,
        simulation_id=uuid4(),
        model="mock",
        max_repair_attempts=2,
    )
    assert result.repair_attempts_used == 1
    assert result.product_intelligence.product_name.value == "Amboras"


@pytest.mark.asyncio
async def test_parse_brief_exhausts_when_provenance_never_clean(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio_json: str,
) -> None:
    bad = json.loads(valid_pio_json)
    bad["description_normalized"] = {
        "value": "x",
        "provenance": "verbatim",
        "source_field": "user_description",
        "source_excerpt": "literally nowhere in the brief",
    }
    bad_str = json.dumps(bad)

    p = MockProvider()
    p.add_default(bad_str)  # always fails provenance

    with pytest.raises(LLMRepairExhausted):
        await parse_brief(
            basic_brief,
            provider=p,
            sessionmaker=None,
            simulation_id=uuid4(),
            model="mock",
            max_repair_attempts=1,
        )
