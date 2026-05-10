"""Phase 7 quality gate — leakage guard unit tests.

The leakage guard catches sentences in `summary` text that pair a
competitor name with a factual signal (price, "free", feature attribution)
without binding to evidence. Subjective interpretation must not fire it.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from assembly.pipeline.aggregation.leakage_guard import (
    scan_for_unbound_factual_claims,
)
from assembly.pipeline.aggregation.section_schema import (
    PublicOpinionSentimentSection,
    SectionAOut,
    PersuadedSection,
    NotPersuadedSection,
    MarketAcceptanceRequirementSection,
)


def _section_a_with_summary(summary: str) -> SectionAOut:
    """Build a minimal valid SectionAOut with the supplied sentiment summary
    (other section summaries are neutral subjective placeholders)."""
    return SectionAOut(
        public_opinion_sentiment=PublicOpinionSentimentSection(
            summary=summary,
            evidence_anchors=[],
            simulation_references=[],
            confidence="moderate",
            validator_notes=[],
        ),
        persuaded=PersuadedSection(
            summary="Agents seemed cautiously interested.",
            evidence_anchors=[],
            simulation_references=[],
            confidence="moderate",
            validator_notes=[],
            factual_claims=[],
        ),
        not_persuaded=NotPersuadedSection(
            summary="Agents portraying X tended to resist.",
            evidence_anchors=[],
            simulation_references=[],
            confidence="moderate",
            validator_notes=[],
            factual_claims=[],
        ),
        market_acceptance_requirement=MarketAcceptanceRequirementSection(
            summary="The simulated society seemed to need clearer trust signals.",
            evidence_anchors=[],
            simulation_references=[],
            confidence="moderate",
            validator_notes=[],
            factual_claims=[],
        ),
    )


# ---------------------------------------------------------------------------
# Cases that should be flagged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leaky_sentence",
    [
        "Shopify Magic costs $0 per month and includes generative AI for ad copy.",
        "Conversion AI Tool offers a free tier for new merchants.",
        "Shopify Magic supports product-description generation and is free in Shopify admin.",
        "Conversion AI Tool has 10000+ reviews on the App Store.",
        "Reviewers said Shopify Magic was the best free generative tool.",
        "Shopify Magic priced at $0 makes it the obvious starting point.",
    ],
)
def test_unbound_factual_claim_in_summary_is_flagged(leaky_sentence: str) -> None:
    section = _section_a_with_summary(leaky_sentence)
    hits = scan_for_unbound_factual_claims(
        section, competitor_names=["Shopify Magic", "Conversion AI Tool"],
    )
    assert hits, f"expected leakage_guard to flag: {leaky_sentence!r}"


# ---------------------------------------------------------------------------
# Cases that should NOT be flagged (subjective interpretation passes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subjective_sentence",
    [
        # Agent-framed factuality — qualified.
        "Agents in the simulation treated Shopify Magic as a free, native baseline already in Shopify admin.",
        "Agents portraying premium operators framed Conversion AI Tool as too narrow for their full stack.",
        "In this simulation, Shopify Magic seemed to function as the zero-cost native baseline agents reached for.",
        "The simulated society appeared to view Shopify Magic as an assist-style tool rather than an operator.",
        # No competitor name → can't be a competitor leak.
        "The supplied starter price seemed reasonable to most agents.",
        # Subjective with explicit framing.
        "Agents framed Shopify Magic as something they already had at zero switching cost.",
    ],
)
def test_subjective_interpretation_passes(subjective_sentence: str) -> None:
    section = _section_a_with_summary(subjective_sentence)
    hits = scan_for_unbound_factual_claims(
        section, competitor_names=["Shopify Magic", "Conversion AI Tool"],
    )
    assert hits == [], (
        f"subjective sentence should NOT trigger leakage_guard: {subjective_sentence!r}; "
        f"got hits: {hits}"
    )


def test_no_competitors_no_hits() -> None:
    """When no competitor names are configured, the guard is silent — even
    on otherwise-suspicious factual signals (those are caught by other
    rules)."""
    section = _section_a_with_summary("Some product costs $0 and is free.")
    hits = scan_for_unbound_factual_claims(section, competitor_names=[])
    assert hits == []


def test_summary_with_uuid_substring_does_not_flag() -> None:
    """UUID-shaped strings (which can appear inside evidence_anchors lists)
    should never be misread as factual prose."""
    section = _section_a_with_summary(
        "Agents portrayed Shopify Magic as already-embedded; see " + str(uuid4()) + "."
    )
    hits = scan_for_unbound_factual_claims(
        section, competitor_names=["Shopify Magic"],
    )
    assert hits == []


def test_factual_claims_subtree_excluded_from_scan() -> None:
    """The scan deliberately skips `factual_claims` and `source_excerpt`
    fields — those are the claim_validator's domain. A factual quotation
    inside factual_claims is fine; we only patrol summary text."""
    from assembly.pipeline.aggregation.section_schema import FactualClaim
    section = SectionAOut(
        public_opinion_sentiment=PublicOpinionSentimentSection(
            summary="Agents portrayed the alternative as adjacent.",
        ),
        persuaded=PersuadedSection(
            summary="Agents who softened seemed to do so for consolidation reasons.",
            factual_claims=[
                # This excerpt names a competitor with a factual signal
                # inside a factual_claim — that's exactly where it should
                # live, so leakage_guard should NOT flag it.
                FactualClaim(
                    text="Shopify Magic is free for Shopify users.",
                    source_evidence_id=uuid4(),
                    source_excerpt="Shopify Magic is free",
                    claim_type="observation",
                    basis="direct",
                    confidence=0.8,
                ),
            ],
        ),
        not_persuaded=NotPersuadedSection(
            summary="Agents tended to resist on brand-control grounds.",
        ),
        market_acceptance_requirement=MarketAcceptanceRequirementSection(
            summary="The simulated society seemed to need stronger proof of control.",
        ),
    )
    hits = scan_for_unbound_factual_claims(
        section, competitor_names=["Shopify Magic"],
    )
    assert hits == [], (
        f"factual_claims subtree must be excluded from leakage scan; got: {hits}"
    )
