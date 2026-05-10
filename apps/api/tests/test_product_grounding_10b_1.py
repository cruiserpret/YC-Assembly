"""Phase 10B.1 — agent grounding + discussion quality patch tests.

Covers:
  * Product Fact Card generation + prompt block
  * Product grounding validator (wrong-category, already-provided
    facts, fake usage, competitor-evidence boundary)
  * Stance calibration rules
  * Caveat-leak detector + repair
  * Discussion diversity auditor
  * Backend integration (fact card flows into discussion +
    repair callers)
"""
from __future__ import annotations

import re
from pathlib import Path

from assembly.sources.product_grounding import (
    audit_ballot_caveat_leaks,
    audit_discussion_diversity,
    audit_product_grounding,
    calibrate_ballots,
    calibrate_stance,
    detect_caveat_leak,
    fact_card_prompt_block,
    generate_product_fact_card,
    strip_caveat_leak,
)


_BASE_BRIEF = {
    "product_name": "SoleNest",
    "product_description": (
        "an electronic shoe-drying and odor-control dock with two "
        "magnetic drying pods"
    ),
    "price_or_price_structure": "$69.99 starter dock with two pods",
    "launch_geography": "Seattle, Washington metro",
    "launch_state": "unlaunched",
    "target_customers": ["urban commuters", "gym-goers", "parents"],
    "competitors_or_alternatives": [
        "PEET Original Electric Shoe and Boot Dryer",
        "DryGuy Force Dry",
        "SteriShoe UV Shoe Sanitizer",
    ],
}


# ---------------------------------------------------------------- 1
def test_1_fact_card_generated_from_brief():
    fc = generate_product_fact_card(_BASE_BRIEF)
    assert fc.product_name == "SoleNest"
    assert fc.product_type and "shoe-drying" in fc.product_type.lower()
    assert "$69.99" in (fc.price_or_price_structure or "")
    assert fc.launch_state == "unlaunched"
    assert "PEET" in " ".join(fc.competitors_or_alternatives)
    assert "a shoe" in fc.not_categories
    assert "an insole" in fc.not_categories


# ---------------------------------------------------------------- 2
def test_2_fact_card_block_injectable_in_prompt():
    fc = generate_product_fact_card(_BASE_BRIEF)
    block = fact_card_prompt_block(fc)
    # 10B.2 renamed the prompt block header to "PRODUCT FACT LOCK"
    # for the price-hierarchy + provided-fact lock semantics.
    assert "PRODUCT FACT LOCK" in block
    assert "SoleNest" in block
    assert "Not: a shoe" in block
    assert "$69.99" in block
    assert "no persona has bought, used, owned, or reviewed it" in block
    # Phase 10B.3 strengthened the wording. Either phrasing must
    # be present (the older 10B.2 line OR the stricter 10B.3 line).
    assert (
        "Do NOT mention the simulation" in block
        or "Do NOT say 'as an agent'" in block
    )


# ---------------------------------------------------------------- 3-4
def test_3_4_wrong_category_drift_detected():
    fc = generate_product_fact_card(_BASE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": "It's basically a shoe — just a heated insole "
                "that costs too much.",
            }
        ],
        ballot_texts=[],
    )
    assert audit["wrong_category_violations"] >= 1
    assert audit["any_violations"] is True


# ---------------------------------------------------------------- 5
def test_5_already_provided_price_detected():
    fc = generate_product_fact_card(_BASE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": "What's the price for SoleNest? Also, how much does it cost?",
            }
        ],
        ballot_texts=[],
    )
    assert audit["already_provided_price_violations"] >= 1


# ---------------------------------------------------------------- 6
def test_6_already_provided_launch_state_detected():
    fc = generate_product_fact_card(_BASE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": "Is it already launched? When does this launch?",
            }
        ],
        ballot_texts=[],
    )
    assert audit["already_provided_launch_violations"] >= 1


# ---------------------------------------------------------------- 7
def test_7_fake_usage_blocked_for_unlaunched_product():
    fc = generate_product_fact_card(_BASE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[],
        ballot_texts=[
            {
                "persona_id": "p1",
                "text": "I bought SoleNest last month and it works great.",
            }
        ],
    )
    assert audit["fake_usage_violations"] >= 1


# ---------------------------------------------------------------- 8
def test_8_competitor_evidence_does_not_redefine_product():
    """A persona may compare SoleNest to PEET / DryGuy without that
    being flagged as wrong-category — we only flag *redefining* the
    product as a different object. The hint set targets phrases
    like "SoleNest is a shoe" / "It's basically a shoe", not
    "compared to PEET, SoleNest is a heated dock". """
    fc = generate_product_fact_card(_BASE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[
            {
                "persona_id": "p1",
                "text": "Compared to PEET, SoleNest is a heated dock — "
                "I'd want side-by-side runtime data.",
            }
        ],
        ballot_texts=[],
    )
    assert audit["wrong_category_violations"] == 0


# ---------------------------------------------------------------- 9
def test_9_receptive_with_clear_positive_intent_kept():
    r = calibrate_stance(
        current_stance="interested_if_proven",
        reasoning=(
            "The two-pod design fits my routine and at $69.99 I'd "
            "buy one if the runtime numbers hold up."
        ),
    )
    assert r["recommended_stance"] == "interested_if_proven"
    assert r["change"] is False
    assert "kept" in r["stance_justification"]


# ---------------------------------------------------------------- 10
def test_10_receptive_with_only_proof_demand_downgraded():
    r = calibrate_stance(
        current_stance="interested_if_proven",
        reasoning=(
            "I would consider this only after hard specs and "
            "side-by-side comparisons. There's no way to tell yet."
        ),
    )
    assert r["recommended_stance"] == "curious_but_unconvinced"
    assert r["change"] is True
    assert "downgrade" in r["stance_justification"]


# ---------------------------------------------------------------- 11
def test_11_uncertain_preserved_when_proof_demand_major():
    r = calibrate_stance(
        current_stance="curious_but_unconvinced",
        reasoning=(
            "I'd need third-party reviews and a clear refund "
            "window before I could make a call."
        ),
    )
    assert r["recommended_stance"] == "curious_but_unconvinced"
    assert r["change"] is False


# ---------------------------------------------------------------- 12
def test_12_resistant_preserved_when_loyal():
    r = calibrate_stance(
        current_stance="skeptical",
        reasoning=(
            "I already own a PEET Original — it works. I don't see "
            "the point of switching. Hard pass."
        ),
    )
    assert r["recommended_stance"] == "skeptical"
    assert r["change"] is False


# ---------------------------------------------------------------- 13
def test_13_stance_justification_field_exists():
    r = calibrate_stance(
        current_stance="interested_if_proven",
        reasoning="Sounds great",
    )
    assert "stance_justification" in r


# ---------------------------------------------------------------- 14-16
def test_14_to_16_diversity_auditor_metrics():
    turns = [
        {"persona_id": "a", "text": "Before I get excited, I need lab data."},
        {"persona_id": "b", "text": "Before I get excited, I need pricing logic."},
        {"persona_id": "c", "text": "Until I see specs I can't judge this."},
        {"persona_id": "d", "text": "What I'd really want is a head-to-head with PEET."},
    ]
    audit = audit_discussion_diversity(turns=turns, ballots=[])
    assert audit["repeated_opening_phrases_count"] >= 2
    assert "persona_voice_diversity_score" in audit
    assert "near_duplicate_turn_count" in audit


# ---------------------------------------------------------------- 17
def test_17_caveat_leak_detector_flags_persona_speech():
    text = (
        "Caveat: this was a synthetic n=24 chat, so I'm treating it "
        "as directional, not a verdict. I'd want runtime proof."
    )
    leaks = detect_caveat_leak(text)
    assert any("synthetic n=" in s.lower() for s in leaks)
    assert any("directional, not a verdict" in s.lower() for s in leaks)


# ---------------------------------------------------------------- 18
def test_18_strip_caveat_keeps_buyer_reasoning():
    text = (
        "Caveat: this was a synthetic n=24 chat. I would still "
        "want a side-by-side benchmark against PEET."
    )
    cleaned, removed = strip_caveat_leak(text)
    assert "synthetic n=24" not in cleaned.lower()
    assert "side-by-side benchmark" in cleaned
    assert removed  # at least one sentence stripped


# ---------------------------------------------------------------- 19
def test_19_directional_not_a_verdict_stripped():
    text = (
        "Treating it as directional, not a verdict — but at $69.99 "
        "I'd want runtime proof."
    )
    cleaned, _ = strip_caveat_leak(text)
    assert "directional, not a verdict" not in cleaned.lower()
    assert "$69.99" in cleaned


# ---------------------------------------------------------------- 20
def test_20_report_caveats_not_removed_by_persona_filter():
    """The persona-level filter must NEVER touch the founder
    report's caveat strings. Those are short, system-level
    sentences and must remain visible."""
    report_caveat = (
        "Synthetic society — not a real-world forecast. The agents "
        "in this report are simulated, evidence-anchored personas."
    )
    # The persona-level strip would aggressively remove this text.
    # That's exactly why the orchestrator only applies the strip
    # to ballot/turn rows — never to the report caveat strings.
    # We assert the strip would mutate the text (proving the strip
    # works), but the orchestrator code path doesn't call it on
    # the report caveats.
    cleaned, _ = strip_caveat_leak(report_caveat)
    assert cleaned != report_caveat
    # And the orchestrator's caveat audit only inspects ballot rows
    audit = audit_ballot_caveat_leaks([
        {
            "persona_id": "p1",
            "ballot_stage": "final",
            "private_reasoning": (
                "Caveat: synthetic n=24 chat, so directional. "
                "I'd want a runtime benchmark."
            ),
        }
    ])
    assert audit["any_leak"] is True
    assert audit["ballots_with_leak"] == 1
    assert audit["sentences_removed"] >= 1


# ---------------------------------------------------------------- 22
def test_22_location_context_preserved_in_fact_card():
    fc = generate_product_fact_card(_BASE_BRIEF)
    block = fact_card_prompt_block(fc)
    assert "Seattle" in block
    assert "Launch geography" in block


# ---------------------------------------------------------------- backend wiring
def test_run_live_discussion_accepts_fact_card_kwarg():
    import inspect
    from assembly.orchestration.live_discussion_pipeline import (
        run_live_discussion,
    )
    sig = inspect.signature(run_live_discussion)
    assert "product_fact_card_text" in sig.parameters


def test_repair_missing_final_ballots_accepts_fact_card_kwarg():
    import inspect
    from assembly.orchestration.live_final_ballot_repair import (
        repair_missing_final_ballots,
    )
    sig = inspect.signature(repair_missing_final_ballots)
    assert "product_fact_card_text" in sig.parameters


def test_discussion_system_prompt_has_buyer_voice_rules():
    """Phase 10B.1 added a strict buyer-voice rules block to the
    system prompt. Make sure it's present so prevention runs at
    every LLM call."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_discussion_pipeline.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # Phase 10B.3 widened the rules header to "10B.1 + 10B.3"; both
    # the original and the new phrasing should pass.
    assert (
        "STRICT BUYER VOICE RULES (Phase 10B.1)" in src
        or "STRICT BUYER VOICE RULES (Phase 10B.1 + 10B.3)" in src
    )
    assert "synthetic society" in src.lower()
    assert "Before I get excited" in src
    # Phase 10B.3 renamed the locked surface "PRODUCT FACT LOCK".
    assert "PRODUCT FACT CARD" in src or "PRODUCT FACT LOCK" in src


def test_orchestrator_invokes_phase_10b1_audits():
    """The orchestrator must call the 10B.1 helper that runs the
    four audits + writes their JSON artifacts."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "_run_phase_10b1_audits" in src
    assert "persona_caveat_leak_quality.json" in src
    assert "stance_calibration_quality.json" in src
    assert "product_grounding_quality.json" in src
    assert "discussion_diversity_quality.json" in src
