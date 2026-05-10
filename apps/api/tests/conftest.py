"""Shared pytest fixtures."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from assembly.llm.mock import MockProvider
from assembly.llm.provider import LLMCallContext
from assembly.pipeline.evidence_builder import (
    EvidenceBuildResult,
    PendingEvidenceItem,
)
from assembly.schemas.brief import (
    CompetitorRef,
    PriceStructure,
    SimulationBriefIn,
    TargetSociety,
)
from assembly.schemas.product_intelligence import ProductIntelligenceObject


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def bypass_cost_guarded_chat(monkeypatch: pytest.MonkeyPatch):
    """Phase 6.6: parser, evidence builder, and society builder all now route
    LLM calls through `cost_guarded_chat`. That helper acquires a Postgres row
    lock on `simulations.id`, which most unit tests don't have. This fixture
    patches `cost_guarded_chat` (and every module that imported it by name)
    with a thin pass-through: build the LLMCallContext, call provider.chat,
    return the response. No row lock, no cap, no log write.

    Tests that want the real cost guard (e.g. integration tests with real
    Postgres + a real Simulation row) should NOT use this fixture.
    """
    from assembly.llm import guarded_chat as guarded_chat_mod
    from assembly.llm.provider import LLMCallContext
    from assembly.pipeline import evidence_builder as evidence_builder_mod
    from assembly.pipeline import intake_parser as intake_parser_mod
    from assembly.pipeline import society_builder as society_builder_mod
    from assembly.pipeline.simulation import call_llm as call_llm_mod

    async def fake_cost_guarded_chat(
        *,
        sessionmaker,
        simulation_id,
        stage,
        messages,
        provider,
        model=None,
        hard_cap_usd=None,
        max_tokens=2048,
        temperature=0.4,
        capture_prompt_snapshot=True,
        estimated_prompt_tokens=4000,
        estimated_completion_tokens=1000,
    ):
        ctx = LLMCallContext(
            stage=stage,
            model=model or "test-model",
            simulation_id=simulation_id,
            max_tokens=max_tokens,
            temperature=temperature,
            capture_prompt_snapshot=capture_prompt_snapshot,
        )
        return await provider.chat(list(messages), ctx)

    # Patch the canonical symbol AND every module that re-bound it via
    # `from assembly.llm.guarded_chat import cost_guarded_chat`.
    for module in (
        guarded_chat_mod,
        intake_parser_mod,
        evidence_builder_mod,
        society_builder_mod,
        call_llm_mod,
    ):
        if hasattr(module, "cost_guarded_chat"):
            monkeypatch.setattr(module, "cost_guarded_chat", fake_cost_guarded_chat)


@pytest.fixture
def basic_brief() -> SimulationBriefIn:
    return SimulationBriefIn(
        product_type="ai_commerce_platform",
        product_name="Amboras",
        description=(
            "Amboras is an AI commerce platform that builds and operates "
            "Shopify stores autonomously for merchants who do not want to "
            "manage plugins or hire agencies. Founders worry the AI will "
            "damage brand identity. Merchants would switch if they saw "
            "proof that they retain final control over branding and pricing."
        ),
        price_structure=PriceStructure(
            model="subscription_monthly",
            amount="$49/mo starter",
            notes="performance tier later",
        ),
        target_society=TargetSociety(
            description=(
                "Shopify merchants doing $10k-$80k/month, frustrated with "
                "plugin bloat and overwhelmed by managing apps."
            ),
            geography="US/Canada",
            known_segments=["mid-volume merchants", "premium brand operators"],
        ),
        competitors=[
            CompetitorRef(name="Shopify Magic", url="https://example.com/magic"),
            CompetitorRef(name="Conversion AI Tool"),
        ],
        product_url=None,
        additional_context="Founders worry about brand control and trust.",
    )


@pytest.fixture
def llm_ctx() -> LLMCallContext:
    return LLMCallContext(
        stage="intake_parser",
        model="mock",
        simulation_id=uuid4(),
        max_tokens=2048,
        temperature=0.2,
    )


@pytest.fixture
def valid_pio_json(basic_brief: SimulationBriefIn) -> str:
    """A hand-crafted valid PIO JSON whose every excerpt provably appears in
    `basic_brief`. Used by parser tests to validate the success path."""
    obj = {
        "product_type": {
            "value": "ai_commerce_platform",
            "provenance": "verbatim",
            "source_field": "user_product_type",
            "source_excerpt": "ai_commerce_platform",
        },
        "product_name": {
            "value": "Amboras",
            "provenance": "verbatim",
            "source_field": "user_product_name",
            "source_excerpt": "Amboras",
        },
        "description_normalized": {
            "value": (
                "AI commerce platform that builds and operates Shopify stores "
                "autonomously for merchants."
            ),
            "provenance": "paraphrase",
            "source_field": "user_description",
            "source_excerpt": (
                "AI commerce platform that builds and operates Shopify "
                "stores autonomously"
            ),
        },
        "price_summary": {
            "value": "$49/mo starter, performance tier later",
            "provenance": "paraphrase",
            "source_field": "user_price_structure",
            "source_excerpt": "$49/mo starter",
        },
        "target_society_summary": {
            "value": "Mid-volume Shopify merchants overwhelmed by plugins.",
            "provenance": "paraphrase",
            "source_field": "user_target_society",
            "source_excerpt": (
                "Shopify merchants doing $10k-$80k/month"
            ),
        },
        "buyer_roles": [
            {
                "value": "Shopify merchants doing $10k-$80k/month",
                "provenance": "verbatim",
                "source_field": "user_target_society",
                "source_excerpt": "Shopify merchants doing $10k-$80k/month",
            },
        ],
        "current_alternatives": [
            {
                "value": "Shopify Magic",
                "provenance": "verbatim",
                "source_field": "user_competitors",
                "source_excerpt": "Shopify Magic",
            },
        ],
        "claims": [
            {
                "text": {
                    "value": "Operates Shopify stores autonomously",
                    "provenance": "paraphrase",
                    "source_field": "user_description",
                    "source_excerpt": "operates Shopify stores autonomously",
                },
                "promise_type": "functional",
            },
        ],
        "trust_risks": [
            {
                "value": "Founders worry the AI will damage brand identity",
                "provenance": "verbatim",
                "source_field": "user_description",
                "source_excerpt": (
                    "Founders worry the AI will damage brand identity"
                ),
            },
        ],
        "objections": [],
        "switching_triggers": [
            {
                "value": "Proof that merchants retain final control",
                "provenance": "paraphrase",
                "source_field": "user_description",
                "source_excerpt": (
                    "they retain final control over branding and pricing"
                ),
            },
        ],
        "novelty_type": {
            "value": "ux_improvement",
            "provenance": "assumption",
            "assumption_rationale": (
                "User did not name a novelty type; the description suggests "
                "removing operational burden, which fits ux_improvement."
            ),
        },
        "emotional_promises": [],
        "functional_promises": [
            {
                "value": "Autonomous Shopify store operation",
                "provenance": "paraphrase",
                "source_field": "user_description",
                "source_excerpt": (
                    "operates Shopify stores autonomously"
                ),
            },
        ],
        "status_promises": [],
    }
    return json.dumps(obj)


@pytest.fixture
def valid_pio(valid_pio_json: str) -> ProductIntelligenceObject:
    return ProductIntelligenceObject.model_validate_json(valid_pio_json)


@pytest.fixture
def evidence_build_result(basic_brief: SimulationBriefIn) -> EvidenceBuildResult:
    """A deterministic EvidenceBuildResult that mirrors what `build_evidence`
    would produce from `basic_brief` with no fetched URLs and no extractor
    pass. The IDs are stable across the fixture (constructed here) so tests
    can build LLMSocietyDraft payloads that anchor to real evidence."""
    sim_id = UUID("00000000-0000-0000-0000-0000000000aa")
    items = [
        PendingEvidenceItem(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            simulation_id=sim_id,
            kind="direct",
            source_type="user_input",
            source_url=None,
            content=basic_brief.product_type,
            captured_at=None,
            metadata={"input_field": "user_product_type"},
        ),
        PendingEvidenceItem(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            simulation_id=sim_id,
            kind="direct",
            source_type="user_input",
            source_url=None,
            content=basic_brief.description,
            captured_at=None,
            metadata={"input_field": "user_description"},
        ),
        PendingEvidenceItem(
            id=UUID("33333333-3333-3333-3333-333333333333"),
            simulation_id=sim_id,
            kind="direct",
            source_type="user_input",
            source_url=None,
            content=basic_brief.target_society.description,
            captured_at=None,
            metadata={"input_field": "user_target_society"},
        ),
        PendingEvidenceItem(
            id=UUID("44444444-4444-4444-4444-444444444444"),
            simulation_id=sim_id,
            kind="direct",
            source_type="user_input",
            source_url=None,
            content=json.dumps(
                [c.model_dump() for c in basic_brief.competitors]
            ),
            captured_at=None,
            metadata={"input_field": "user_competitors"},
        ),
        PendingEvidenceItem(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            simulation_id=sim_id,
            kind="missing",
            source_type="public_review",
            source_url=None,
            content=(
                "Expected public_review evidence not provided by the user. "
                "Treated as missing."
            ),
            captured_at=None,
            metadata={"reason": "expected_but_absent"},
        ),
        PendingEvidenceItem(
            id=UUID("66666666-6666-6666-6666-666666666666"),
            simulation_id=sim_id,
            kind="missing",
            source_type="pricing_page",
            source_url=None,
            content="Expected pricing_page evidence not provided.",
            captured_at=None,
            metadata={"reason": "expected_but_absent"},
        ),
    ]
    return EvidenceBuildResult(
        items=items,
        fetched_pages=[],
        fetch_errors=[],
        extracted_phrases=[],
    )


@pytest.fixture
def evidence_ids(evidence_build_result: EvidenceBuildResult) -> dict[str, UUID]:
    """Convenience map for tests that anchor to specific evidence kinds."""
    by_field: dict[str, UUID] = {}
    for item in evidence_build_result.items:
        if item.kind == "direct" and item.metadata.get("input_field"):
            by_field[item.metadata["input_field"]] = item.id
        elif item.kind == "missing":
            by_field[f"missing_{item.source_type}"] = item.id
    return by_field
