"""Phase 10B.2 — YouTube Tier-1 + Product Fact Lock + Price
Hierarchy + extended provided-fact accuracy.

Covers spec tests 1–32.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from assembly.sources.product_grounding import (
    audit_price_hierarchy,
    audit_provided_fact_accuracy,
    calibrate_stance,
    fact_card_prompt_block,
    generate_product_fact_card,
    repair_price_confusion,
)
from assembly.sources.product_grounding.product_fact_card import (
    AccessoryPrice,
)


# Brief used by all the ClosetCloud-style tests.
_CLOSETCLOUD_BRIEF = {
    "product_name": "ClosetCloud",
    "product_description": (
        "ClosetCloud is a compact electronic garment-refresh and "
        "moisture-control hanger system for small apartments. It "
        "is not a washing machine, not a dryer, not a steamer, and "
        "not a dry-cleaning replacement. The rail plugs into a "
        "normal wall outlet. The hangers charge magnetically when "
        "placed on the rail and run wirelessly for up to 6 hours "
        "per cycle. It does not use heat, steam, water, detergent, "
        "or UV light."
    ),
    "price_or_price_structure": (
        "$119 for the starter kit:\n"
        "- 1 wall-mounted charging rail\n"
        "- 3 smart garment-refresh hangers\n"
        "- 3 activated-carbon filters\n\n"
        "Replacement filter pack: $14.99 for 6 filters."
    ),
    "launch_geography": "New York City metro area",
    "launch_state": "unlaunched",
    "target_customers": [
        "urban renters",
        "college students",
        "gym-goers",
    ],
    "competitors_or_alternatives": [
        "LG Styler", "Samsung AirDresser", "Dryel",
    ],
    "optional_context": "",
}


# ====================================================================
# YouTube Tier-1 wiring (tests 1, 2, 3, 9, 10, 11)
# ====================================================================


def test_1_youtube_runs_in_tier_1_when_configured():
    """The retrieval pipeline must call YouTube unconditionally
    when the API key is configured — not just on Tier-1 escalation."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # The call to _retrieve_youtube must NOT be inside the
    # `if escalate:` block. We grep for it appearing before
    # `# ---- Tier 2 escalation decision`.
    tier_2_marker = "# ---- Tier 2 escalation decision"
    youtube_call_idx = src.find("_retrieve_youtube(")
    tier_2_idx = src.find(tier_2_marker)
    assert youtube_call_idx > 0, "YouTube call site missing"
    assert tier_2_idx > 0, "Tier 2 marker missing"
    assert youtube_call_idx < tier_2_idx, (
        "YouTube must be invoked before the Tier 2 escalation block"
    )


def test_2_brave_and_tavily_remain_tier_1():
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tier_2_idx = src.find("# ---- Tier 2 escalation decision")
    assert "_retrieve_brave(" in src[:tier_2_idx]
    assert "_retrieve_tavily(" in src[:tier_2_idx]


def test_3_firecrawl_remains_escalation_only():
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    tier_2_idx = src.find("# ---- Tier 2 escalation decision")
    # Firecrawl must NOT be CALLED in Tier 1 — only its definition
    # may appear above the Tier 2 marker. We look for the call
    # form (`= _retrieve_firecrawl(` or `, _retrieve_firecrawl(`)
    # rather than the bare name to avoid matching `def`.
    pre = src[:tier_2_idx]
    post = src[tier_2_idx:]
    call_pattern = re.compile(r"=\s*_retrieve_firecrawl\(|,\s*_retrieve_firecrawl\(")
    assert not call_pattern.search(pre), (
        "Firecrawl is being CALLED in Tier 1 — it must stay "
        "escalation-only"
    )
    assert call_pattern.search(post), (
        "Firecrawl call is missing from the Tier 2 escalation block"
    )


def test_4_youtube_comment_length_filter():
    from assembly.orchestration.live_evidence_pipeline import (
        _yt_comment_passes_quality,
    )
    p, r = _yt_comment_passes_quality(
        "too short", anchor_terms=["closetcloud"],
    )
    assert p is False
    assert r == "too_short"


def test_5_youtube_anchor_filter_rejects_unrelated():
    from assembly.orchestration.live_evidence_pipeline import (
        _yt_comment_passes_quality,
    )
    text = (
        "This was a great explanation about gardening tips for "
        "small apartments — really helpful for spring planting."
    )
    p, r = _yt_comment_passes_quality(
        text, anchor_terms=["closet", "garment", "hanger"],
    )
    assert p is False
    assert r == "no_anchor_match"


def test_6_youtube_spam_promo_generic_rejected():
    from assembly.orchestration.live_evidence_pipeline import (
        _yt_comment_passes_quality,
    )
    cases = [
        "First!",
        "Subscribe to my channel for more reviews like this",
        "Wow",
        "🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌🙌",
    ]
    for text in cases:
        p, _ = _yt_comment_passes_quality(text, anchor_terms=[])
        assert p is False, f"should reject: {text!r}"


def test_7_youtube_audit_has_required_fields():
    """The YouTube audit dict produced by `_retrieve_youtube` carries
    the fields documented in the spec (videos_searched, found,
    pulled, accepted, rejected, rejection_reasons,
    video_search_queries)."""
    # Inspect the source — the function returns a dict literal at
    # call time; we just verify the keys are present in code.
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    for key in (
        "videos_searched",
        "videos_found",
        "comments_pulled",
        "comments_accepted",
        "comments_rejected",
        "rejection_reasons",
        "video_search_queries",
    ):
        assert f'"{key}"' in src, f"YouTube audit missing key: {key}"


def test_8_youtube_signal_diversity_capability():
    """A YouTube comment with explicit objection language ("the steam
    function leaks") that includes an anchor must pass the filter
    so signal_extractor can later turn it into an objection signal."""
    from assembly.orchestration.live_evidence_pipeline import (
        _yt_comment_passes_quality,
    )
    text = (
        "I bought the Styler for my apartment and the steam function "
        "works but the unit is huge — it doesn't fit in my closet "
        "space and the price was too much for the value."
    )
    p, _ = _yt_comment_passes_quality(
        text, anchor_terms=["styler", "closet", "steam"],
    )
    assert p is True


def test_9_amazon_reviews_not_wired():
    """No Amazon adapter is wired in this phase."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # We allow a comment string "amazon" but NOT a function call to
    # an Amazon retrieval helper.
    assert "_retrieve_amazon(" not in src
    assert "_retrieve_amazon_reviews(" not in src


def test_10_no_reddit_x_tiktok_apify_wired():
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_evidence_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    for forbidden in (
        "_retrieve_reddit(",
        "_retrieve_twitter(",
        "_retrieve_x_(",
        "_retrieve_tiktok(",
        "_retrieve_apify(",
        "_retrieve_brightdata(",
    ):
        assert forbidden not in src


def test_11_provider_keys_returned_only_as_booleans():
    from assembly.orchestration.live_evidence_pipeline import (
        provider_keys_summary,
    )
    summary = provider_keys_summary()
    for k, v in summary.items():
        assert isinstance(v, bool), f"{k} is not a boolean"


# ====================================================================
# Product Fact Lock (tests 12–17)
# ====================================================================


def test_12_product_fact_lock_generated():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    assert fc.product_name == "ClosetCloud"
    assert fc.primary_price == "$119"
    assert any(
        ap.amount == "$14.99" for ap in fc.accessory_prices
    )
    assert fc.kit_contents
    assert "a washing machine" in fc.not_categories
    assert any("plug" in p.lower() for p in fc.power_facts)
    assert any("wireless" in c.lower() for c in fc.charging_facts)
    assert "No heat" in fc.excluded_features
    assert "No UV light" in fc.excluded_features


def test_13_primary_vs_accessory_price_distinguished():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    assert fc.primary_price == "$119"
    accessory_amounts = {ap.amount for ap in fc.accessory_prices}
    assert "$14.99" in accessory_amounts
    assert fc.primary_price != "$14.99"


def test_14_accessory_price_cannot_be_used_as_primary():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_price_hierarchy(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": (
                    "At $14.99 the hanger is a no-brainer for me — "
                    "I'd buy it tomorrow."
                ),
            },
            {
                "persona_id": "p2",
                "text": (
                    "Fifteen bucks for a hanger that does that? "
                    "Sounds interesting."
                ),
            },
        ],
        ballot_texts=[],
    )
    assert audit["price_confusion_count"] >= 2
    assert audit["any_violations"] is True


def test_15_recurring_filter_cost_can_be_discussed():
    """A persona may correctly discuss the $14.99 filter pack as a
    recurring cost — that should NOT trigger price-confusion."""
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_price_hierarchy(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": (
                    "At $119 for the starter kit I want to know how "
                    "often the $14.99 filter pack needs replacing."
                ),
            }
        ],
        ballot_texts=[],
    )
    assert audit["price_confusion_count"] == 0


def test_16_power_charging_facts_locked_in_prompt_block():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    block = fact_card_prompt_block(fc)
    assert "Power: " in block
    assert "Charging" in block
    assert "wireless" in block.lower()


def test_17_excluded_features_locked_in_prompt_block():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    block = fact_card_prompt_block(fc)
    assert "Excluded features" in block
    assert "No heat" in block
    assert "No UV light" in block


# ====================================================================
# Provided-fact accuracy (tests 18–21)
# ====================================================================


def test_18_known_fact_reask_detected_power():
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_provided_fact_accuracy(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": "Is it plug-in or battery?",
            },
            {
                "persona_id": "p2",
                "text": "Does it use heat or UV?",
            },
        ],
        ballot_texts=[],
    )
    assert audit["power_fact_reask_count"] >= 1
    assert audit["excluded_feature_reask_count"] >= 1
    assert audit["known_fact_reask_count"] >= 2


def test_19_known_fact_reask_not_flagged_when_credibility_question():
    """The persona is allowed to question whether the no-heat odor
    claim is credible — that wording is a credibility challenge, not
    a fact re-ask, and should NOT be flagged."""
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_provided_fact_accuracy(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": (
                    "Since it does not use heat, UV, or steam, I'm "
                    "skeptical the odor-control claim is strong "
                    "enough without proof."
                ),
            }
        ],
        ballot_texts=[],
    )
    assert audit["excluded_feature_reask_count"] == 0


def test_20_competitor_evidence_does_not_redefine_product():
    """A persona may compare ClosetCloud to LG Styler / AirDresser
    without that being flagged as wrong-category — the grounding
    validator only flags `ClosetCloud is a [shoe|insole|...]` style
    claims, not comparisons."""
    from assembly.sources.product_grounding import audit_product_grounding
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": (
                    "Compared to the LG Styler, ClosetCloud is much "
                    "smaller and runs without steam — I'd want to see "
                    "side-by-side odor-removal results."
                ),
            }
        ],
        ballot_texts=[],
    )
    assert audit["wrong_category_violations"] == 0


def test_21_youtube_does_not_override_product_facts():
    """A YouTube-style comment that talks about a competitor must
    not be allowed to redefine the target product. We test this at
    the validator level — when an agent quotes a YouTube comment
    about LG Styler features, that's a comparison, not a
    redefinition."""
    from assembly.sources.product_grounding import audit_product_grounding
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": (
                    "A YouTube reviewer said the Styler uses steam "
                    "and runs hot. ClosetCloud explicitly avoids "
                    "those, so the comparison is uneven."
                ),
            }
        ],
        ballot_texts=[],
    )
    assert audit["wrong_category_violations"] == 0


# ====================================================================
# Stance calibration (tests 22–24)
# ====================================================================


def test_22_receptive_with_unresolved_proof_demands_downgraded():
    r = calibrate_stance(
        current_stance="interested_if_proven",
        reasoning=(
            "I'm willing to be convinced. I'd want to understand "
            "what's actually inside, the runtime curve, and "
            "side-by-side comparisons before I move."
        ),
    )
    assert r["recommended_stance"] == "curious_but_unconvinced"
    assert r["change"] is True


def test_23_receptive_based_on_wrong_price_premise_can_be_repaired():
    """When the persona's positive intent is anchored to the wrong
    price ('I'd buy at $14.99'), the price-confusion repair strips
    that sentence — and the calibrator (running on the cleaned
    text) downgrades the now-objections-only ballot to UNCERTAIN.
    """
    fc = generate_product_fact_card(_CLOSETCLOUD_BRIEF)
    primary_value = float(fc.primary_price.replace("$", ""))
    accessory_values = [
        float(ap.amount.replace("$", ""))
        for ap in fc.accessory_prices
    ]
    text = (
        "At $14.99, I'd buy the hanger right now. But I'd also want "
        "to see independent runtime numbers before I commit."
    )
    cleaned, removed = repair_price_confusion(
        text, primary_value, accessory_values,
    )
    assert removed >= 1
    # The cleaned text should now read as proof-demanding only,
    # which the calibrator downgrades.
    r = calibrate_stance(
        current_stance="interested_if_proven", reasoning=cleaned,
    )
    assert r["recommended_stance"] in {
        "curious_but_unconvinced", "skeptical",
    }


def test_24_stance_justification_field_persists():
    r = calibrate_stance(
        current_stance="interested_if_proven",
        reasoning="I'd want to see the runtime curve before I commit.",
    )
    assert "stance_justification" in r
    assert r["stance_justification"]


# ====================================================================
# Repetition / diversity (tests 25–27)
# ====================================================================


def test_25_repeated_stock_phrases_counted():
    from assembly.sources.product_grounding import (
        audit_discussion_diversity,
    )
    turns = [
        {"persona_id": "a", "text": "Here's what's bugging me about the price hierarchy story."},
        {"persona_id": "b", "text": "Here's what's bugging me about the recurring filter cost."},
        {"persona_id": "c", "text": "I keep circling back to the no-heat claim."},
        {"persona_id": "d", "text": "What I want pinned down is whether the runtime is real."},
    ]
    audit = audit_discussion_diversity(turns=turns, ballots=[])
    hits = audit.get("repeated_opener_pattern_hits", {})
    # At least one of the 10B.2 banned phrases should fire.
    assert any(
        pattern in hits
        for pattern in (
            "here's\\ what's\\ bugging\\ me",
            "i\\ keep\\ circling\\ back",
            "what\\ i\\ want\\ pinned\\ down",
        )
    )


def test_26_near_duplicate_turns_counted():
    from assembly.sources.product_grounding import (
        audit_discussion_diversity,
    )
    text = (
        "I'd want to see independent runtime data and a "
        "side-by-side comparison before I commit to the kit."
    )
    turns = [
        {"persona_id": "a", "text": text},
        {"persona_id": "b", "text": text + " It really matters."},
    ]
    audit = audit_discussion_diversity(turns=turns, ballots=[])
    assert audit["near_duplicate_turn_count"] >= 1


def test_27_voice_diversity_score_exists():
    from assembly.sources.product_grounding import (
        audit_discussion_diversity,
    )
    audit = audit_discussion_diversity(
        turns=[
            {"persona_id": "a", "text": "Some unique thoughtful turn here."},
        ],
        ballots=[],
    )
    assert "persona_voice_diversity_score" in audit
    assert 0.0 <= audit["persona_voice_diversity_score"] <= 1.0


# ====================================================================
# Orchestrator wiring (tests 31–32 happen at the suite level)
# ====================================================================


def test_orchestrator_invokes_10b_2_audits():
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "audit_price_hierarchy" in src
    assert "audit_provided_fact_accuracy" in src
    assert "price_hierarchy_quality.json" in src
    assert "provided_fact_accuracy_quality.json" in src
    # Anchor terms must be threaded into retrieval for the YouTube
    # quality filter.
    assert "anchor_terms=" in src


def test_run_live_retrieval_accepts_anchor_terms():
    import inspect
    from assembly.orchestration.live_evidence_pipeline import (
        run_live_retrieval,
    )
    sig = inspect.signature(run_live_retrieval)
    assert "anchor_terms" in sig.parameters


def test_repair_price_confusion_strips_only_confused_sentences():
    """The price-confusion repair must drop the confused sentence
    and preserve the rest of the buyer reasoning."""
    text = (
        "At $14.99 I'd buy the hanger tomorrow. I do still want "
        "the runtime curve, though."
    )
    cleaned, removed = repair_price_confusion(
        text, primary_value=119.0, accessory_values=[14.99],
    )
    assert removed >= 1
    assert "$14.99" not in cleaned
    assert "runtime curve" in cleaned
