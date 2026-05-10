"""Phase 10B.4 — Negation-scope Fact Lock + RECEPTIVE strictness v3
acceptance tests.

Covers:
  * Negation-scope sensing facts (has_camera vs records_video etc.)
  * Camera/privacy fact-inversion detector + repair
  * Input-mechanism fact-lock + "no scanning" inversion detector
  * Stricter RECEPTIVE classifier v3 (killer-proof + proof-demand
    domination + conditional-receptive collapse)
  * Human-speech quality (regression of 10B.3 self-awareness leak)
  * Headline / audience copy human-readability
"""
from __future__ import annotations

from pathlib import Path

from assembly.sources.product_grounding import (
    audit_input_mechanism,
    audit_negation_scope,
    audit_receptive_strictness_v3,
    classify_stance_strictness_v3,
    detect_self_awareness_leak,
    fact_card_prompt_block,
    generate_product_fact_card,
    repair_negation_scope_inversion,
)


_PANTRYPULSE_BRIEF = {
    "product_name": "PantryPulse",
    "product_description": (
        "PantryPulse is an unlaunched smart kitchen inventory "
        "scanner. The starter kit includes one slim magnetic pantry "
        "scanner, one fridge-door scanner, and eight reusable NFC "
        "food tags. The scanners use a tiny wide-angle camera plus "
        "barcode/NFC scanning. PantryPulse does not record video, "
        "does not livestream, and does not identify people. It "
        "captures still images of shelves/labels during scan "
        "events. The device has a physical camera shutter and a "
        "visible LED when scanning."
    ),
    "price_or_price_structure": (
        "$149 one-time for starter kit. Optional: $7.99/month "
        "subscription. Accessory: $19.99 for 12-pack NFC tags."
    ),
    "launch_state": "unlaunched",
    "target_customers": ["urban renters", "busy parents"],
    "competitors_or_alternatives": [
        "Samsung Family Hub refrigerator",
        "FridgeCam by Smarter",
        "AnyList grocery list app",
    ],
}


# ============================================================ 1
def test_1_fact_lock_separates_has_camera_from_records_video():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.sensing_facts.get("has_camera") is True
    assert fc.sensing_facts.get("records_video") is False


# ============================================================ 2
def test_2_fact_lock_distinguishes_still_images_from_livestream():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.sensing_facts.get("captures_still_images") is True
    assert fc.sensing_facts.get("livestreams") is False


# ============================================================ 3
def test_3_does_not_record_video_does_not_become_no_camera():
    """The fact lock must keep the two facts independent — adding
    'records_video=False' must NOT remove 'has_camera=True'."""
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.sensing_facts.get("has_camera") is True
    assert "Camera present" in (
        fc.sensing_fact_details.get("has_camera") or []
    )


# ============================================================ 4
def test_4_does_not_identify_people_is_not_no_camera():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.sensing_facts.get("identifies_people") is False
    assert fc.sensing_facts.get("has_camera") is True


# ============================================================ 5
def test_5_camera_inversion_detector_catches_no_camera():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    audit = audit_negation_scope(
        fact_card=fc,
        turn_texts=[
            {"persona_id": "p1",
             "text": "I love that PantryPulse has no camera and only "
                     "uses NFC."},
        ],
        ballot_texts=[],
    )
    assert audit["camera_fact_inversion_count"] >= 1
    assert "no_camera" in audit["by_kind"]


# ============================================================ 6
def test_6_camera_inversion_repair_rewrites_to_still_image_form():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    cleaned, count, examples = repair_negation_scope_inversion(
        "I like the privacy story. I love that PantryPulse has no "
        "camera and only uses NFC.",
        fc,
    )
    assert count >= 1
    # The buyer's "I like the privacy story" must survive.
    assert "privacy story" in cleaned
    # The inversion sentence must be replaced with the still-image
    # / privacy framing.
    assert "still" in cleaned.lower()
    assert "no camera" not in cleaned.lower()
    assert any(ex["kind"] == "no_camera" for ex in examples)


# ============================================================ 7
def test_7_input_mechanism_lock_stores_barcode_scanning():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.input_mechanism_facts.get("has_barcode_scanning") is True


# ============================================================ 8
def test_8_input_mechanism_lock_stores_nfc_scanning():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert fc.input_mechanism_facts.get("has_nfc_scanning") is True


# ============================================================ 9
def test_9_input_mechanism_lock_stores_reusable_nfc_tags():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    assert (
        fc.input_mechanism_facts.get("has_reusable_nfc_tags") is True
    )


# ============================================================ 10
def test_10_validator_catches_no_scanning_when_scanning_exists():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    audit = audit_input_mechanism(
        fact_card=fc,
        turn_texts=[
            {"persona_id": "p1",
             "text": "If there is no scanning, how does PantryPulse "
                     "know what is in my pantry?"},
        ],
        ballot_texts=[],
    )
    assert audit["input_inversion_count"] >= 1
    assert "no_scanning" in audit["by_kind"]


# ============================================================ 11
def test_11_receptive_label_still_exists():
    p = (
        Path(__file__).resolve().parent.parent.parent / "web"
        / "src" / "lib" / "stance.ts"
    )
    src = p.read_text(encoding="utf-8")
    assert "RECEPTIVE" in src
    assert 'label: "Receptive"' in src


# ============================================================ 12
def test_12_receptive_not_renamed():
    p = (
        Path(__file__).resolve().parent.parent.parent / "web"
        / "src" / "lib" / "stance.ts"
    )
    src = p.read_text(encoding="utf-8")
    assert "Conditionally receptive" not in src
    assert "Receptive if proven" not in src


# ============================================================ 13
def test_13_receptive_requires_clear_positive_use_or_buy_driver():
    """Reasoning with a clear personal use-case + willingness-to-buy
    line and no killer-proof must stay RECEPTIVE."""
    r = classify_stance_strictness_v3(
        current_stance="interested_if_proven",
        reasoning=(
            "I have two kids and I already forget what's in the "
            "pantry. This would actually solve a real annoyance for "
            "me. I would buy one if the workflow is real."
        ),
    )
    assert r["recommended_stance"] == "interested_if_proven"
    assert r["change"] is False


# ============================================================ 14
def test_14_proof_demand_dominates_downgrades_to_uncertain():
    """The PantryPulse 'Emerson G.' shape: ONE positive use case
    sandwiched between a killer-proof line. v3 downgrades."""
    r = classify_stance_strictness_v3(
        current_stance="interested_if_proven",
        reasoning=(
            "I have two kids and grocery waste is real for us. "
            "If the answer is you scan every item going in, I'm out. "
            "Without that, $149 is just a magnet and a promise."
        ),
    )
    assert r["recommended_stance"] == "curious_but_unconvinced"
    assert r["change"] is True
    assert "v3_killer_proof" in r["rule_applied"]


# ============================================================ 15
def test_15_stance_justification_field_exists():
    r = classify_stance_strictness_v3(
        current_stance="interested_if_proven",
        reasoning="It looks fine.",
    )
    assert "stance_justification" in r
    assert r["stance_justification"] != ""
    assert "rule_applied" in r


# ============================================================ 16
def test_16_human_speech_validator_catches_as_an_agent():
    leaks = detect_self_awareness_leak(
        "As an agent in this discussion, I'd want a side-by-side."
    )
    assert any("as an agent" in s.lower() for s in leaks)


# ============================================================ 17
def test_17_human_speech_validator_catches_synthetic_society():
    leaks = detect_self_awareness_leak(
        "In this synthetic society, I'd want runtime proof."
    )
    assert any("synthetic society" in s.lower() for s in leaks)


# ============================================================ 18
def test_18_fake_usage_of_unlaunched_product_blocked():
    """Phase 10B.1 already enforces no-fake-target-product-use via
    audit_product_grounding. Phase 10B.4 keeps that guarantee."""
    from assembly.sources.product_grounding import (
        audit_product_grounding,
    )
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    audit = audit_product_grounding(
        fact_card=fc,
        turn_texts=[],
        ballot_texts=[
            {"persona_id": "p1",
             "text": "I bought PantryPulse last week and it's great."},
        ],
    )
    assert audit["fake_usage_violations"] >= 1


# ============================================================ 19
def test_19_headline_does_not_contain_real_world_forecast_caveat():
    from assembly.sources.product_grounding import (
        build_confident_headline,
    )
    h = build_confident_headline(
        product_name="PantryPulse",
        persona_count=24,
        receptive_final_count=12,
        shifted_toward_receptive=4,
        pre_distribution={},
        final_distribution={},
    )
    low = h.lower()
    assert "not a real-world purchase forecast" not in low
    assert "not a real-world forecast" not in low
    assert "validated with real prospects" not in low
    assert "synthetic signal" not in low


# ============================================================ 20
def test_20_report_level_caveats_remain_visible():
    """The orchestrator still emits the four-line caveats list +
    the trust-section header_caveat. Check the source contains
    them."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "Live run-scoped synthetic society" in src
    assert "not a real focus group" in src
    # The header_caveat line is split across two source-string
    # literals; check both fragments instead of the joined form.
    assert "alongside real customer" in src
    assert "Assembly results describe" in src


# ============================================================ 21
def test_21_best_fit_audience_copy_is_human_readable():
    from assembly.sources.product_grounding import (
        build_best_fit_audience,
    )
    out = build_best_fit_audience(
        role_distribution={
            "competitor_user_anylist": {
                "receptive": 4, "uncertain": 0, "resistant": 0,
            },
            "use_case_focused_buyer": {
                "receptive": 3, "uncertain": 0, "resistant": 0,
            },
        },
        target_customers=["busy households", "smart-fridge users"],
        competitor_alternatives=["AnyList"],
    )
    copy = out["summary_copy"].lower()
    # Target-customer language present, role labels not at start.
    assert "busy households" in copy
    assert (
        "anylist" in copy
        or "alternative" in copy
        or "familiar with" in copy
    )
    assert not copy.startswith("competitor_user")


# ============================================================ 22
def test_22_hardest_to_convince_copy_is_human_readable():
    from assembly.sources.product_grounding import (
        build_hardest_to_convince,
    )
    out = build_hardest_to_convince(
        role_distribution={
            "price_skeptic": {
                "receptive": 0, "uncertain": 4, "resistant": 0,
            },
            "competitor_user_anylist": {
                "receptive": 0, "uncertain": 3, "resistant": 0,
            },
        },
        top_objections=[
            {"text": "$149 is just hardware around a free habit"},
            {"text": "needs proof it reduces manual logging"},
        ],
        top_proof_needs=[
            {"text": "side-by-side workflow vs AnyList"},
        ],
        target_customers=["busy households"],
    )
    copy = out["summary_copy"]
    # Must NOT lead with the raw role label
    assert not copy.lower().startswith("price_skeptic")
    # Must contain a real-world descriptor
    assert (
        "AnyList" in copy
        or "alternative" in copy
        or "still required" in copy.lower()
        or "manual logging" in copy.lower()
        or "stronger proof" in copy.lower()
    )


# ============================================================ 23
def test_23_glowplate_camera_invariant_zero_inversions_after_repair():
    """Synthetic acceptance for the negation-scope repair: a turn
    containing 'no camera' phrasing is rewritten so the audit
    counts zero inversions in the cleaned text."""
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    bad_text = (
        "I love that this is a no-camera tracker. "
        "The privacy story sounds solid."
    )
    cleaned, count, _ = repair_negation_scope_inversion(bad_text, fc)
    audit_after = audit_negation_scope(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": cleaned}],
        ballot_texts=[],
    )
    assert count >= 1
    assert audit_after["camera_fact_inversion_count"] == 0
    assert audit_after["unrepaired_count"] == 0


# ============================================================ 24
def test_24_glowplate_input_invariant_zero_inversions_after_repair():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    bad = "Without scanning, $149 is just a magnet and a promise."
    cleaned, count, _ = repair_negation_scope_inversion(bad, fc)
    audit_after = audit_input_mechanism(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": cleaned}],
        ballot_texts=[],
    )
    assert count >= 1
    assert audit_after["input_inversion_count"] == 0


# ============================================================ 25
def test_25_orchestrator_invokes_phase_10b4_audits():
    """The orchestrator must call the new 10B.4 audits and write
    their JSON artifacts."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "audit_negation_scope" in src
    assert "audit_input_mechanism" in src
    assert "audit_receptive_strictness_v3" in src
    assert "repair_negation_scope_inversion" in src
    assert "negation_scope_fact_quality.json" in src
    assert "input_mechanism_fact_quality.json" in src
    assert "receptive_strictness_quality.json" in src
    assert "human_speech_quality.json" in src
    assert "report_summary_calibration_quality.json" in src


# ============================================================ 26
def test_26_audit_receptive_strictness_v3_audit_shape():
    ballots = [
        # Killer-proof: should downgrade
        {"persona_id": "p1", "ballot_stage": "final",
         "private_stance": "interested_if_proven",
         "private_reasoning": (
             "Without that, $149 is just a magnet and a promise. "
             "If it's manual I'm out."
         )},
        # Strong receptive: should keep
        {"persona_id": "p2", "ballot_stage": "final",
         "private_stance": "interested_if_proven",
         "private_reasoning": (
             "I have two kids and grocery waste is real. This would "
             "solve a real problem for me. I would buy one if the "
             "workflow is real."
         )},
    ]
    audit = audit_receptive_strictness_v3(ballots)
    assert audit["receptive_before"] == 2
    assert audit["downgraded_receptive_count"] >= 1
    assert audit["receptive_after"] < audit["receptive_before"]
    assert "rule_counter" in audit
    assert audit["rule_counter"].get("v3_killer_proof", 0) >= 1


# ---- bonus: fact-card prompt block surfaces the negation scope ----
def test_27_prompt_block_shows_negation_scope_explicitly():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    block = fact_card_prompt_block(fc)
    assert "Sensing capabilities (the product HAS these)" in block
    assert "Sensing behaviors the product DOES NOT do" in block
    assert "NEGATION SCOPE" in block
    assert "Do NOT say 'no camera'" in block
    assert "Input mechanisms" in block


# ---- bonus: privacy phrasing must NOT count as no-camera ----
def test_28_privacy_phrasing_does_not_count_as_no_camera():
    fc = generate_product_fact_card(_PANTRYPULSE_BRIEF)
    audit = audit_negation_scope(
        fact_card=fc,
        turn_texts=[
            {"persona_id": "p1",
             "text": (
                 "I like that PantryPulse does not record video and "
                 "does not identify people. Since it captures still "
                 "shelf images, I'd want clarity on the deletion "
                 "workflow."
             )},
        ],
        ballot_texts=[],
    )
    assert audit["camera_fact_inversion_count"] == 0
    assert audit["scanning_fact_inversion_count"] == 0
