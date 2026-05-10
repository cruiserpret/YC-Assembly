"""Tests for the Phase 5 Society Builder.

Coverage:
  - AgentField provenance (basis tagging) — pydantic structural rules
  - LLMAgentDraft / LLMSocietyDraft validation
  - validate_society: anchor existence, language linter, persona heuristic
  - build_society end-to-end with MockProvider
  - default influence-graph fallback
  - persist_society: marked integration (requires Postgres)
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from assembly.llm.errors import LLMRepairExhausted
from assembly.llm.mock import MockProvider
from assembly.pipeline.evidence_builder import EvidenceBuildResult
from assembly.pipeline.society_builder import (
    _default_influence_edges,
    build_society,
    validate_society,
)
from assembly.schemas.brief import SimulationBriefIn
from assembly.schemas.product_intelligence import ProductIntelligenceObject
from assembly.schemas.society import (
    AgentField,
    AgentTraits,
    BasisKind,
    BuyerStateLayer,
    CategoricalTrait,
    EconomicLayer,
    EmotionalJTBDLayer,
    GeneratedAgent,
    LLMAgentDraft,
    LLMEdgeDraft,
    LLMSocietyDraft,
    OCEANLayer,
    SocialInfluenceLayer,
    TrustProofRiskLayer,
)


# ---------------------------------------------------------------------------
# AgentField — structural provenance rules
# ---------------------------------------------------------------------------


def test_agentfield_assumption_requires_rationale() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        AgentField(
            value="x",
            basis=BasisKind.ASSUMPTION,
            assumption_rationale=None,
        )


def test_agentfield_direct_evidence_requires_anchors() -> None:
    with pytest.raises(Exception):
        AgentField(
            value="x",
            basis=BasisKind.DIRECT_EVIDENCE,
            evidence_anchors=[],
        )


def test_agentfield_user_input_requires_anchors() -> None:
    with pytest.raises(Exception):
        AgentField(
            value="x",
            basis=BasisKind.USER_INPUT,
            evidence_anchors=[],
        )


def test_agentfield_assumption_must_not_have_anchors() -> None:
    with pytest.raises(Exception):
        AgentField(
            value="x",
            basis=BasisKind.ASSUMPTION,
            evidence_anchors=[uuid4()],
            assumption_rationale="reason",
        )


def test_agentfield_direct_evidence_must_not_have_rationale() -> None:
    with pytest.raises(Exception):
        AgentField(
            value="x",
            basis=BasisKind.DIRECT_EVIDENCE,
            evidence_anchors=[uuid4()],
            assumption_rationale="should not be set",
        )


def test_agentfield_assumption_with_missing_evidence_link_ok() -> None:
    """Assumption may optionally link to a kind=missing evidence_item."""
    f = AgentField(
        value="x",
        basis=BasisKind.ASSUMPTION,
        assumption_rationale="reason",
        missing_evidence_link=uuid4(),
    )
    assert f.missing_evidence_link is not None


# ---------------------------------------------------------------------------
# LLMAgentDraft — schema-level invariants
# ---------------------------------------------------------------------------


def _evidence_anchor_field(eid: UUID, value: str = "Shopify Magic, freelancers") -> AgentField:
    return AgentField(
        value=value,
        basis=BasisKind.USER_INPUT,
        evidence_anchors=[eid],
    )


# ---------------------------------------------------------------------------
# Phase 5.5 helpers — minimal valid AgentTraits for fixture construction.
# ---------------------------------------------------------------------------


def _ct_assumption(level: str, rationale: str) -> CategoricalTrait:
    """Quick-build an assumption-basis CategoricalTrait."""
    return CategoricalTrait(
        level=level,  # type: ignore[arg-type]
        rationale=rationale,
        basis=BasisKind.ASSUMPTION,
        assumption_rationale=(
            "Inferred qualitatively from buyer-state cues; not directly stated in brief."
        ),
    )


def _ct_user_input(eid: UUID, level: str, rationale: str) -> CategoricalTrait:
    """Quick-build a user_input-basis CategoricalTrait anchored to eid."""
    return CategoricalTrait(
        level=level,  # type: ignore[arg-type]
        rationale=rationale,
        basis=BasisKind.USER_INPUT,
        evidence_anchors=[eid],
    )


def _minimal_agent_traits(eid: UUID) -> AgentTraits:
    """Build a valid AgentTraits with mostly user_input + assumption basis,
    suitable for tests. The eid is the user_description evidence id from the
    `evidence_build_result` fixture."""
    return AgentTraits(
        buyer_state=BuyerStateLayer(
            current_workflow=_evidence_anchor_field(eid, "manages Shopify apps and reviews freelancer output daily"),
            current_pain=_evidence_anchor_field(eid, "plugin bloat across 30+ apps"),
            category_familiarity=_ct_user_input(eid, "moderate", "the brief implies hands-on familiarity with Shopify ecosystem"),
        ),
        ocean=OCEANLayer(
            openness=_ct_assumption("moderate", "willing to consider new tools but cautious about novelty"),
            conscientiousness=_ct_assumption("high", "the brief mentions buyer demands proof and reliability signals"),
            extraversion=_ct_assumption("moderate", "Shopify merchant communities are active so peer feedback matters"),
            agreeableness=_ct_assumption("moderate", "skeptical of marketing claims but not hostile"),
            neuroticism_or_risk_sensitivity=_ct_assumption("moderate", "fear of brand damage suggests moderate risk sensitivity"),
        ),
        economic=EconomicLayer(
            willingness_to_pay=_evidence_anchor_field(eid, "willing to pay mid-tier monthly subscription"),
            roi_expectation=_evidence_anchor_field(eid, "expects clear merchant ROI signal within a quarter"),
            cost_of_current_alternative=_evidence_anchor_field(eid, "spends on Shopify apps and freelancers"),
            purchase_authority=_evidence_anchor_field(eid, "sole decision-maker for tooling"),
            time_to_value_expectation=_evidence_anchor_field(eid, "expects visible value in first month"),
        ),
        trust_proof_risk=TrustProofRiskLayer(
            proof_requirement=_evidence_anchor_field(eid, "live merchant case studies and control-safeguard demos"),
            skepticism_level=_ct_user_input(eid, "high", "the brief explicitly mentions fear of brand damage and unproven AI"),
            risk_tolerance=_ct_assumption("moderate", "open to switching only with clear ROI proof"),
            brand_control_sensitivity=_ct_user_input(eid, "high", "the brief states fear of losing brand identity"),
            required_credibility_signal=_evidence_anchor_field(eid, "merchants like them with similar volume publicly endorsing the product"),
            fear_of_downside=_evidence_anchor_field(eid, "AI publishing changes that hurt brand identity"),
        ),
        social_influence=SocialInfluenceLayer(
            status_sensitivity=_ct_assumption("moderate", "Shopify merchants compare with peers in communities"),
            word_of_mouth_likelihood=_ct_assumption("high", "active community sharing is typical for this segment"),
            trust_edges_placeholder=[],
        ),
        emotional_jtbd=EmotionalJTBDLayer(
            push_pain=_evidence_anchor_field(eid, "exhausted by plugin maintenance and freelancer coordination"),
            pull_attraction=_evidence_anchor_field(eid, "fewer plugins, more brand control, less daily ops"),
            anxiety=_evidence_anchor_field(eid, "AI may publish brand-damaging output without warning"),
            habit=_evidence_anchor_field(eid, "weekly plugin reconciliation routine"),
            desired_transformation=_evidence_anchor_field(eid, "from operator-maintainer to merchant-strategist"),
        ),
    )


def _all_anchored_fields(eid: UUID) -> dict[str, AgentField]:
    """Build the 9 buyer-state fields, each anchored to `eid`."""
    return {
        "current_alternatives": _evidence_anchor_field(eid, "Shopify Magic, freelancers"),
        "budget_level": _evidence_anchor_field(eid, "$10k-$80k MRR"),
        "trust_threshold": _evidence_anchor_field(eid, "needs proof of brand control"),
        "switching_trigger": _evidence_anchor_field(eid, "live merchant case studies"),
        "fear": _evidence_anchor_field(eid, "losing brand identity"),
        "desire": _evidence_anchor_field(eid, "fewer plugins, more brand control"),
        "price_sensitivity": _evidence_anchor_field(eid, "moderate; $49 acceptable"),
        "objection_pattern": _evidence_anchor_field(eid, "AI sounds unproven"),
        "emotional_state": _evidence_anchor_field(eid, "overwhelmed and skeptical"),
    }


def _make_agent_draft(
    *,
    eid: UUID,
    summary: str | None = None,
    influence_score: float = 0.4,
    traits: AgentTraits | None = None,
) -> LLMAgentDraft:
    return LLMAgentDraft(
        segment="overwhelmed mid-volume merchant",
        role="solo Shopify merchant",
        cluster="merchants",
        weight=0.0625,
        summary=(
            summary
            or (
                "A Shopify merchant doing mid-volume sales, currently using "
                "Shopify apps and freelancers, frustrated with plugin bloat, "
                "afraid of losing brand control, willing to switch only if "
                "trust and ROI are clear."
            )
        ),
        **_all_anchored_fields(eid),
        influence_score=influence_score,
        susceptibility_to_peer_shift=0.5,
        assumptions=[],
        missing_evidence_awareness=[],
        traits=traits or _minimal_agent_traits(eid),
    )


def test_llm_agent_draft_influence_score_bounded(evidence_ids: dict[str, UUID]) -> None:
    eid = evidence_ids["user_description"]
    with pytest.raises(Exception):
        _make_agent_draft(eid=eid, influence_score=1.5)
    with pytest.raises(Exception):
        _make_agent_draft(eid=eid, influence_score=-0.1)


def test_llm_agent_draft_short_summary_rejected(evidence_ids: dict[str, UUID]) -> None:
    """Pydantic-level min_length=75 on summary."""
    with pytest.raises(Exception):
        _make_agent_draft(eid=evidence_ids["user_description"], summary="Sarah, 24, likes skincare.")


def test_llm_edge_no_self_loops() -> None:
    with pytest.raises(Exception):
        LLMEdgeDraft(source_index=2, target_index=2, influence_strength=0.5)


# ---------------------------------------------------------------------------
# validate_society — substantive checks
# ---------------------------------------------------------------------------


def _make_society_with_agents(
    agents: list[LLMAgentDraft],
    edges: list[LLMEdgeDraft] | None = None,
) -> LLMSocietyDraft:
    return LLMSocietyDraft(agents=agents, edges=edges or [])


def test_validate_society_accepts_clean_draft(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    draft = _make_society_with_agents(
        [_make_agent_draft(eid=eid) for _ in range(8)]
    )
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    assert errors == []


def test_validate_society_rejects_unknown_evidence_anchor(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    bad_eid = UUID("99999999-9999-9999-9999-999999999999")  # not in ledger
    agent = _make_agent_draft(eid=bad_eid)
    draft = _make_society_with_agents(
        [agent] + [_make_agent_draft(eid=evidence_ids["user_description"]) for _ in range(7)]
    )
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert "society.unknown_evidence_anchor" in rules


def test_validate_society_rejects_generic_persona(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """Pydantic min_length=75 will reject the literal 'Sarah, 24...' first;
    so we craft a 75+ char summary that nevertheless starts with the persona
    pattern."""
    eid = evidence_ids["user_description"]
    summary = (
        "Sarah, 24, likes skincare and needs cheaper subscription pricing "
        "across all categories of products."
    )
    agent = _make_agent_draft(eid=eid, summary=summary)
    draft = _make_society_with_agents([agent for _ in range(8)])
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert "society.generic_persona" in rules


def test_validate_society_rejects_forced_verdict_in_field(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    bad_field = AgentField(
        value="we should kill this product positioning",
        basis=BasisKind.USER_INPUT,
        evidence_anchors=[eid],
    )
    agent = _make_agent_draft(eid=eid)
    agent = agent.model_copy(update={"objection_pattern": bad_field})
    draft = _make_society_with_agents([agent for _ in range(8)])
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert any(r.startswith("language.verdict") for r in rules)


def test_validate_society_rejects_objective_sentiment_in_field(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    bad_field = AgentField(
        value="customers want this and the market is positive overall",
        basis=BasisKind.USER_INPUT,
        evidence_anchors=[eid],
    )
    agent = _make_agent_draft(eid=eid)
    agent = agent.model_copy(update={"emotional_state": bad_field})
    draft = _make_society_with_agents([agent for _ in range(8)])
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert any(r.startswith("language.obj") for r in rules)


def test_validate_society_rejects_fake_metric_in_field(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    bad_field = AgentField(
        value="expected CTR is 3.2% based on prior cohorts",
        basis=BasisKind.USER_INPUT,
        evidence_anchors=[eid],
    )
    agent = _make_agent_draft(eid=eid)
    agent = agent.model_copy(update={"price_sensitivity": bad_field})
    draft = _make_society_with_agents([agent for _ in range(8)])
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert any(r.startswith("language.num") for r in rules)


def test_validate_society_rejects_society_too_small(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    draft = _make_society_with_agents(
        [_make_agent_draft(eid=eid) for _ in range(3)]
    )
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=8
    )
    rules = {e.rule for e in errors}
    assert "society.too_small" in rules


def test_validate_society_rejects_edge_index_out_of_range(
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    draft = _make_society_with_agents(
        [_make_agent_draft(eid=eid) for _ in range(7)],
        edges=[LLMEdgeDraft(source_index=0, target_index=999, influence_strength=0.5)],
    )
    errors = validate_society(
        draft, evidence=evidence_build_result, desired_size=7
    )
    rules = {e.rule for e in errors}
    assert "society.edge_index_out_of_range" in rules


# ---------------------------------------------------------------------------
# Default fallback influence graph
# ---------------------------------------------------------------------------


def _generated_from_draft(draft: LLMAgentDraft) -> GeneratedAgent:
    return GeneratedAgent(
        segment=draft.segment,
        role=draft.role,
        cluster=draft.cluster,
        weight=draft.weight,
        summary=draft.summary,
        current_alternatives=draft.current_alternatives,
        budget_level=draft.budget_level,
        trust_threshold=draft.trust_threshold,
        switching_trigger=draft.switching_trigger,
        fear=draft.fear,
        desire=draft.desire,
        price_sensitivity=draft.price_sensitivity,
        objection_pattern=draft.objection_pattern,
        emotional_state=draft.emotional_state,
        influence_score=draft.influence_score,
        susceptibility_to_peer_shift=draft.susceptibility_to_peer_shift,
        assumptions=list(draft.assumptions),
        missing_evidence_awareness=list(draft.missing_evidence_awareness),
        traits=draft.traits,
    )


def test_default_influence_edges_within_cluster(
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    drafts = [
        _make_agent_draft(eid=eid),
        _make_agent_draft(eid=eid),
        _make_agent_draft(eid=eid),
    ]
    agents = [_generated_from_draft(d) for d in drafts]
    # all in the same cluster ("merchants")
    edges = _default_influence_edges(agents)
    assert len(edges) == 6  # 3 agents × 2 directed = 6 edges
    assert all(e.cluster_label == "merchants" for e in edges)
    assert all(0.5 <= e.influence_strength <= 0.7 for e in edges)


def test_generated_agent_collects_all_evidence_anchors(
    evidence_ids: dict[str, UUID],
) -> None:
    eid = evidence_ids["user_description"]
    eid2 = evidence_ids["user_target_society"]
    fields = _all_anchored_fields(eid)
    fields["fear"] = _evidence_anchor_field(eid2, "losing brand identity")
    draft = LLMAgentDraft(
        segment="seg",
        role="role",
        cluster="cluster",
        weight=0.1,
        summary=(
            "A Shopify merchant doing mid-volume sales, frustrated with "
            "plugin bloat, afraid of losing brand control, willing to "
            "switch only if trust is clear."
        ),
        **fields,
        influence_score=0.4,
        susceptibility_to_peer_shift=0.5,
        traits=_minimal_agent_traits(eid),
    )
    ga = _generated_from_draft(draft)
    anchors = ga.all_evidence_anchors()
    assert eid in anchors
    assert eid2 in anchors


# ---------------------------------------------------------------------------
# build_society end-to-end with MockProvider
# ---------------------------------------------------------------------------


def _make_society_json(
    eid: UUID,
    n: int,
    *,
    include_assumption: bool = False,
    missing_eid: UUID | None = None,
) -> str:
    """Construct a valid LLMSocietyDraft JSON anchored to `eid`. Optionally
    includes an assumption-basis field paired with `missing_eid`."""
    agents = []
    summaries = [
        "A Shopify merchant doing mid-volume sales, currently using Shopify apps and freelancers, frustrated with plugin bloat, afraid of losing brand control, willing to switch only if trust and ROI are clear.",
        "A premium DTC brand operator running a curated catalog, currently working with a boutique agency, worried that automation will dilute brand voice, willing to consider tools only if they preserve creative control.",
        "A plugin-heavy operator running 30+ Shopify apps, currently spending most evenings reconciling integrations, hoping for consolidation, afraid of breaking established workflows, willing to switch only after a trial period.",
        "An agency-dependent merchant who outsources storefront work to a fractional team, currently paying retainer fees, hopeful about reducing dependency, anxious about losing the agency relationship, willing to switch when ROI overtakes retainer cost.",
        "A technical founder running a Shopify Plus account, currently writing custom Liquid templates, looking to offload routine ops, fearful of black-box AI, willing to adopt only after evaluating output quality first-hand.",
        "A non-technical merchant overwhelmed by daily store updates, currently relying on screenshots and friends for help, hoping for guided automation, afraid of pricing surprises, willing to commit only after seeing peers succeed.",
        "A growth-focused merchant pushing aggressive paid acquisition, currently testing landing pages weekly, eager to move faster, anxious about losing creative control to AI, willing to try if conversion lift is observable.",
        "A budget-sensitive operator running on a low MRR, currently using free or cheap tools, hopeful for affordable automation, afraid of monthly subscription drain, willing to try only with transparent pricing tiers.",
    ]
    for i in range(n):
        fields = _all_anchored_fields(eid)
        if include_assumption and i == 0:
            # Assumption-basis fields must be qualitative — no fabricated
            # dollar amounts (those would only be legitimate as user-input
            # quotes). The strict linter enforces this.
            assumption_field = AgentField(
                value="moderate sensitivity, expects mid-tier subscription pricing",
                basis=BasisKind.ASSUMPTION,
                assumption_rationale=(
                    "User did not state price sensitivity; inferred "
                    "qualitatively from category norms for similar SaaS tools."
                ),
                missing_evidence_link=missing_eid,
            )
            fields["price_sensitivity"] = assumption_field
        agents.append(
            LLMAgentDraft(
                segment=f"segment_{i}",
                role=f"role_{i}",
                cluster="merchants" if i < n // 2 else "premium",
                weight=1.0 / n,
                summary=summaries[i % len(summaries)],
                **fields,
                influence_score=0.3 + (i * 0.05) % 0.6,
                susceptibility_to_peer_shift=0.4,
                assumptions=(
                    ["price_sensitivity assumed from category baseline"]
                    if include_assumption and i == 0
                    else []
                ),
                missing_evidence_awareness=(
                    ["no public reviews available — caution about adoption rate"]
                    if i == 1
                    else []
                ),
                traits=_minimal_agent_traits(eid),
            )
        )
    edges = [
        LLMEdgeDraft(source_index=0, target_index=1, influence_strength=0.6, cluster_label="merchants"),
        LLMEdgeDraft(source_index=1, target_index=0, influence_strength=0.6, cluster_label="merchants"),
        LLMEdgeDraft(source_index=2, target_index=3, influence_strength=0.5, cluster_label="merchants"),
    ]
    society = LLMSocietyDraft(agents=agents, edges=edges)
    return society.model_dump_json()


@pytest.mark.asyncio
async def test_build_society_end_to_end_with_mock_provider(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    p = MockProvider()
    p.add_default(_make_society_json(eid, 8))

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=None,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=8,
    )

    # Required fields populated on every agent
    assert len(society.agents) == 8
    for agent in society.agents:
        # All 9 buyer-state fields present
        for name, field in agent.fields_iter():
            assert field.value, f"{name} on {agent.agent_id} has empty value"
        # Influence bounds enforced by Pydantic
        assert 0 <= agent.influence_score <= 1
        assert 0 <= agent.susceptibility_to_peer_shift <= 1
        # Agent UUIDs assigned
        assert isinstance(agent.agent_id, UUID)

    # Edges preserved + UUIDs translated
    agent_ids = {a.agent_id for a in society.agents}
    assert len(society.edges) >= 3
    for edge in society.edges:
        assert edge.source_agent_id in agent_ids
        assert edge.target_agent_id in agent_ids
        assert 0 <= edge.influence_strength <= 1


@pytest.mark.asyncio
async def test_build_society_assumptions_labeled_and_missing_evidence_linked(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    missing_eid = evidence_ids["missing_public_review"]
    p = MockProvider()
    p.add_default(
        _make_society_json(
            eid, 8, include_assumption=True, missing_eid=missing_eid
        )
    )

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=None,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=8,
    )

    # The first agent has an assumption-basis field paired with a missing-evidence link
    a0 = society.agents[0]
    assert a0.price_sensitivity.basis == BasisKind.ASSUMPTION
    assert a0.price_sensitivity.assumption_rationale
    assert a0.price_sensitivity.missing_evidence_link == missing_eid

    # Missing-evidence awareness preserved
    assert any(a.missing_evidence_awareness for a in society.agents)


@pytest.mark.asyncio
async def test_build_society_default_edges_when_llm_omits(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """If the LLM returns edges=[], default within-cluster + cross-cluster
    edges are generated and a warning is recorded."""
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    society_json = json.loads(_make_society_json(eid, 8))
    society_json["edges"] = []  # remove edges
    p = MockProvider()
    p.add_default(json.dumps(society_json))

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=None,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=8,
    )

    assert society.edges, "default edges should have been generated"
    assert any("default" in w.lower() for w in society.warnings)


@pytest.mark.asyncio
async def test_build_society_repairs_on_unknown_anchor(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """First response uses an invented anchor; second is clean."""
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    bad_eid = UUID("99999999-9999-9999-9999-999999999999")

    bad_json = _make_society_json(bad_eid, 8)
    good_json = _make_society_json(eid, 8)

    p = MockProvider()
    p.add_response(predicate=lambda *_: True, response=bad_json)
    p.add_default(good_json)

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=None,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=8,
        max_repair_attempts=2,
    )
    assert society.repair_attempts_used == 1
    assert len(society.agents) == 8


@pytest.mark.asyncio
async def test_build_society_exhausts_when_never_clean(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    sim_id = evidence_build_result.items[0].simulation_id
    bad_eid = UUID("99999999-9999-9999-9999-999999999999")
    p = MockProvider()
    p.add_default(_make_society_json(bad_eid, 8))

    with pytest.raises(LLMRepairExhausted):
        await build_society(
            simulation_id=sim_id,
        sessionmaker=None,
            brief=basic_brief,
            pio=valid_pio,
            evidence=evidence_build_result,
            provider=p,
            model="mock",
            desired_size=8,
            max_repair_attempts=1,
        )


# ---------------------------------------------------------------------------
# Persistence — integration test (requires Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture
async def _dispose_engine_after_persist_test():
    """Per-test engine dispose — same pattern as other integration tests in
    this repo. Keeps asyncpg connections from leaking across event loops."""
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover  defensive
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_persist_society_writes_agents_and_edges(
    bypass_cost_guarded_chat,
    _dispose_engine_after_persist_test,
    basic_brief,
    valid_pio,
    evidence_build_result,
    evidence_ids,
) -> None:
    """Round-trip: build society with MockProvider → persist_society → query
    DB → assert rows present.

    Uses the existing Phase-5 fixtures (basic_brief, valid_pio,
    evidence_build_result) so the seeded simulation_id matches the one
    `_user_input_evidence` baked into evidence_items, satisfying the FK on
    `agents.simulation_id`."""
    from uuid import uuid4

    from assembly.db import get_sessionmaker
    from assembly.models.agent import Agent as AgentORM
    from assembly.models.agent import AgentEdge as AgentEdgeORM
    from assembly.models.simulation import Simulation, SimulationInput
    from assembly.pipeline.society_builder import build_society, persist_society

    sessionmaker = get_sessionmaker()
    # Generate a fresh sim_id rather than reusing the fixture's constant —
    # other integration tests share that fixture, and reruns against the
    # same DB collide on simulations.id.
    sim_id = uuid4()
    eid = evidence_ids["user_description"]

    # Persist a Simulation row first so the agents FK is satisfied.
    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(
                id=sim_id, status="pending", progress={"stage": "pending"}
            )
            sim.input = SimulationInput(
                product_type=basic_brief.product_type,
                product_name=basic_brief.product_name,
                description=basic_brief.description,
                price_structure=basic_brief.price_structure.model_dump(),
                target_society=basic_brief.target_society.model_dump(),
                competitors=[c.model_dump() for c in basic_brief.competitors],
                product_url=None,
                additional_context=basic_brief.additional_context,
                raw_brief=basic_brief.model_dump(mode="json"),
            )
            session.add(sim)

    # Build a small society against a MockProvider.
    p = MockProvider()
    p.add_default(_make_society_json(eid, 6))

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=sessionmaker,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=6,
    )

    # Persist + verify rows exist with the right shape.
    async with sessionmaker() as session:
        async with session.begin():
            agent_rows, edge_rows = await persist_society(
                session, simulation_id=sim_id, society=society
            )

    assert len(agent_rows) == 6, "expected 6 agent rows persisted"
    assert len(edge_rows) >= 1, "expected at least one edge row persisted"

    # Read back via a fresh session to confirm the writes committed.
    from sqlalchemy import select
    async with sessionmaker() as session:
        roundtrip_agents = (
            await session.execute(
                select(AgentORM).where(AgentORM.simulation_id == sim_id)
            )
        ).scalars().all()
        roundtrip_edges = (
            await session.execute(
                select(AgentEdgeORM).where(AgentEdgeORM.simulation_id == sim_id)
            )
        ).scalars().all()

    assert len(roundtrip_agents) == 6
    assert len(roundtrip_edges) >= 1
    # Every agent has the six-layer trait dump and an evidence_anchors set.
    for a in roundtrip_agents:
        assert a.buyer_state is not None
        assert a.traits is not None
        assert isinstance(a.evidence_anchors, list)


# ---------------------------------------------------------------------------
# Phase 5.5 — Six-layer trait tests
# ---------------------------------------------------------------------------


def test_categorical_trait_assumption_requires_rationale() -> None:
    with pytest.raises(Exception):
        CategoricalTrait(
            level="moderate",
            rationale="some rationale here",
            basis=BasisKind.ASSUMPTION,
            # missing assumption_rationale
        )


def test_categorical_trait_evidence_basis_requires_anchors() -> None:
    with pytest.raises(Exception):
        CategoricalTrait(
            level="high",
            rationale="some rationale here",
            basis=BasisKind.DIRECT_EVIDENCE,
            evidence_anchors=[],
        )


def test_categorical_trait_assumption_must_not_have_anchors() -> None:
    with pytest.raises(Exception):
        CategoricalTrait(
            level="high",
            rationale="reason here",
            basis=BasisKind.ASSUMPTION,
            evidence_anchors=[uuid4()],
            assumption_rationale="reason",
        )


def test_categorical_trait_level_is_bounded() -> None:
    """level must be one of low/moderate/high — Literal enforces this."""
    with pytest.raises(Exception):
        CategoricalTrait(
            level="extreme",  # not in enum
            rationale="reason here",
            basis=BasisKind.ASSUMPTION,
            assumption_rationale="reason",
        )


def test_agent_traits_has_all_six_layers(evidence_ids: dict[str, UUID]) -> None:
    """AgentTraits must structurally contain all six layers."""
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    assert traits.buyer_state is not None
    assert traits.ocean is not None
    assert traits.economic is not None
    assert traits.trust_proof_risk is not None
    assert traits.social_influence is not None
    assert traits.emotional_jtbd is not None


def test_ocean_layer_has_all_five_traits(evidence_ids: dict[str, UUID]) -> None:
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    o = traits.ocean
    assert o.openness.level in ("low", "moderate", "high")
    assert o.conscientiousness.level in ("low", "moderate", "high")
    assert o.extraversion.level in ("low", "moderate", "high")
    assert o.agreeableness.level in ("low", "moderate", "high")
    assert o.neuroticism_or_risk_sensitivity.level in ("low", "moderate", "high")


def test_economic_layer_fields_present(evidence_ids: dict[str, UUID]) -> None:
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    e = traits.economic
    for f in ("willingness_to_pay", "roi_expectation", "cost_of_current_alternative",
              "purchase_authority", "time_to_value_expectation"):
        assert getattr(e, f).value, f"economic.{f} should be populated"


def test_trust_proof_risk_layer_fields_present(evidence_ids: dict[str, UUID]) -> None:
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    t = traits.trust_proof_risk
    assert t.proof_requirement.value
    assert t.skepticism_level.level in ("low", "moderate", "high")
    assert t.risk_tolerance.level in ("low", "moderate", "high")
    assert t.brand_control_sensitivity.level in ("low", "moderate", "high")
    assert t.required_credibility_signal.value
    assert t.fear_of_downside.value


def test_social_influence_layer_fields_present(evidence_ids: dict[str, UUID]) -> None:
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    s = traits.social_influence
    assert s.status_sensitivity.level in ("low", "moderate", "high")
    assert s.word_of_mouth_likelihood.level in ("low", "moderate", "high")
    assert s.trust_edges_placeholder == []  # Phase 6 populates this


def test_emotional_jtbd_layer_fields_present(evidence_ids: dict[str, UUID]) -> None:
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    j = traits.emotional_jtbd
    for f in ("push_pain", "pull_attraction", "anxiety", "habit", "desired_transformation"):
        assert getattr(j, f).value, f"emotional_jtbd.{f} should be populated"


def test_every_trait_field_has_basis(evidence_ids: dict[str, UUID]) -> None:
    """Every leaf in AgentTraits must carry a basis (no bare strings)."""
    traits = _minimal_agent_traits(evidence_ids["user_description"])
    for path, field in traits.all_agent_field_paths():
        assert field.basis in (
            BasisKind.DIRECT_EVIDENCE, BasisKind.USER_INPUT,
            BasisKind.ANALOGICAL_EVIDENCE, BasisKind.ASSUMPTION,
        ), f"{path} has no valid basis"
    for path, ct in traits.all_categorical_fields():
        assert ct.basis in (
            BasisKind.DIRECT_EVIDENCE, BasisKind.USER_INPUT,
            BasisKind.ANALOGICAL_EVIDENCE, BasisKind.ASSUMPTION,
        ), f"{path} has no valid basis"


def test_traits_evidence_anchors_roll_up_to_agent_level(
    evidence_ids: dict[str, UUID],
) -> None:
    """`GeneratedAgent.all_evidence_anchors()` must include traits anchors."""
    eid_a = evidence_ids["user_description"]
    eid_b = evidence_ids["user_target_society"]
    # Build a minimal agent whose traits anchor to a different eid (eid_b)
    # than the outer fields (eid_a). The roll-up should contain both.
    traits = _minimal_agent_traits(eid_b)
    draft = _make_agent_draft(eid=eid_a, traits=traits)
    ga = _generated_from_draft(draft)
    anchors = ga.all_evidence_anchors()
    assert eid_a in anchors  # from outer fields
    assert eid_b in anchors  # from traits


@pytest.mark.asyncio
async def test_build_society_validates_unknown_anchor_in_traits(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """Unknown evidence anchor INSIDE traits must trigger a validation error
    and a repair attempt, same as outer-field anchor errors."""
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    bad_eid = UUID("99999999-9999-9999-9999-999999999999")

    # Bad first response: traits anchored to a non-existent eid.
    bad_traits = _minimal_agent_traits(bad_eid)
    bad_json_obj = json.loads(_make_society_json(eid, 8))
    for a in bad_json_obj["agents"]:
        a["traits"] = json.loads(bad_traits.model_dump_json())
    bad_json = json.dumps(bad_json_obj)

    good_json = _make_society_json(eid, 8)

    p = MockProvider()
    p.add_response(predicate=lambda *_: True, response=bad_json)
    p.add_default(good_json)

    society = await build_society(
        simulation_id=sim_id,
        sessionmaker=None,
        brief=basic_brief,
        pio=valid_pio,
        evidence=evidence_build_result,
        provider=p,
        model="mock",
        desired_size=8,
        max_repair_attempts=2,
    )
    assert society.repair_attempts_used == 1
    assert len(society.agents) == 8


@pytest.mark.asyncio
async def test_build_society_rejects_forced_verdict_inside_traits(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """Forced-verdict language inside a traits free-text field must fail."""
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    p = MockProvider()

    # Build a valid base society, then poison one trait field with verdict.
    base = json.loads(_make_society_json(eid, 8))
    base["agents"][0]["traits"]["emotional_jtbd"]["push_pain"]["value"] = (
        "We should kill the current workflow and pivot to a new approach"
    )
    poisoned = json.dumps(base)

    p.add_default(poisoned)

    with pytest.raises(LLMRepairExhausted):
        await build_society(
            simulation_id=sim_id,
        sessionmaker=None,
            brief=basic_brief,
            pio=valid_pio,
            evidence=evidence_build_result,
            provider=p,
            model="mock",
            desired_size=8,
            max_repair_attempts=1,
        )


@pytest.mark.asyncio
async def test_build_society_rejects_objective_sentiment_inside_traits(
    bypass_cost_guarded_chat,
    basic_brief: SimulationBriefIn,
    valid_pio: ProductIntelligenceObject,
    evidence_build_result: EvidenceBuildResult,
    evidence_ids: dict[str, UUID],
) -> None:
    """Objective-sentiment language inside a traits free-text field must fail."""
    sim_id = evidence_build_result.items[0].simulation_id
    eid = evidence_ids["user_description"]
    p = MockProvider()

    base = json.loads(_make_society_json(eid, 8))
    base["agents"][0]["traits"]["economic"]["roi_expectation"]["value"] = (
        "customers want this and the market is positive overall"
    )
    poisoned = json.dumps(base)

    p.add_default(poisoned)

    with pytest.raises(LLMRepairExhausted):
        await build_society(
            simulation_id=sim_id,
        sessionmaker=None,
            brief=basic_brief,
            pio=valid_pio,
            evidence=evidence_build_result,
            provider=p,
            model="mock",
            desired_size=8,
            max_repair_attempts=1,
        )


def test_traits_jsonb_is_populated_at_persistence_time(
    evidence_ids: dict[str, UUID],
) -> None:
    """The model_dump_json round-trip used by persist_society must
    include the six layers (proves traits JSONB will be populated, not {})."""
    eid = evidence_ids["user_description"]
    draft = _make_agent_draft(eid=eid)
    ga = _generated_from_draft(draft)
    traits_dump = json.loads(ga.traits.model_dump_json())
    for layer in ("buyer_state", "ocean", "economic", "trust_proof_risk",
                  "social_influence", "emotional_jtbd"):
        assert layer in traits_dump, f"traits JSONB missing {layer}"
    # OCEAN level values present
    for trait in ("openness", "conscientiousness", "extraversion",
                  "agreeableness", "neuroticism_or_risk_sensitivity"):
        assert traits_dump["ocean"][trait]["level"] in ("low", "moderate", "high")
