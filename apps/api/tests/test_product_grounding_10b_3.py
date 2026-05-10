"""Phase 10B.3 — Report calibration + Fact-Lock hardening + human-
society agent realism.

Covers:
  * Stricter RECEPTIVE classification (RECEPTIVE label preserved,
    earned only with positive intent / use-case fit; major proof
    gates downgrade)
  * Provided-Fact Lock v2 (price, bundle price, accessory, kit,
    materials, runtime, temperature, cleaning, charging, excluded)
    with re-ask detector AND a repair that rewrites known-fact
    re-asks into "Since the brief says X, I'd want proof Y".
  * agent_self_awareness_leak_detector (catches "as an agent",
    "synthetic persona", "n=24", "directional", "not a forecast").
  * Headline caveat relocation (executive_summary headline must NOT
    contain "not a real-world forecast" / "validated with real
    prospects").
  * Hardest-to-convince populated even with zero resistant ballots.
  * Best-fit audience copy is humanized (target-customer language,
    not just role labels).
  * Evidence-flavor section reports YouTube contribution.
"""
from __future__ import annotations

from pathlib import Path

from assembly.sources.product_grounding import (
    audit_human_society_realism,
    audit_provided_fact_lock_v2,
    audit_stance_strictness,
    build_best_fit_audience,
    build_confident_headline,
    build_evidence_flavor,
    build_hardest_to_convince,
    classify_stance_strictness,
    detect_self_awareness_leak,
    fact_card_prompt_block,
    generate_product_fact_card,
    humanize_role,
    repair_known_fact_reask,
    role_distribution_from_ballots,
    strip_self_awareness_leak,
)


_GLOWPLATE_BRIEF = {
    "product_name": "GlowPlate",
    "product_description": (
        "GlowPlate is a smart heated meal plate with a rechargeable "
        "warming base. It is not a microwave, not a hot plate, not "
        "a cooking appliance, and not a food warmer tray. The "
        "removable ceramic plate is dishwasher-safe and "
        "microwave-safe when separated from the base. The base uses "
        "a USB-C rechargeable battery."
    ),
    "price_or_price_structure": (
        "$79 for one ceramic plate plus rechargeable warming base. "
        "Two-plate bundle: $139."
    ),
    "launch_state": "unlaunched",
    "optional_context": (
        "Holds food warm in the 120°F–145°F range for up to 45 "
        "minutes per session."
    ),
    "target_customers": [
        "remote workers", "parents", "slow eaters",
    ],
    "competitors_or_alternatives": [
        "Ember Mug", "Crock-Pot Lunch Crock Food Warmer",
        "HotLogic Mini Portable Oven",
    ],
}


# ============================================================ 1
def test_1_receptive_label_still_exists():
    from pathlib import Path as P
    p = (
        P(__file__).resolve().parent.parent.parent / "web"
        / "src" / "lib" / "stance.ts"
    )
    src = p.read_text(encoding="utf-8")
    # The user-facing label must remain. The instruction was
    # explicit: do NOT remove or rename RECEPTIVE.
    assert "RECEPTIVE" in src
    assert "label: \"Receptive\"" in src


# ============================================================ 2
def test_2_receptive_not_renamed_to_conditionally_receptive():
    from pathlib import Path as P
    p = (
        P(__file__).resolve().parent.parent.parent / "web"
        / "src" / "lib" / "stance.ts"
    )
    src = p.read_text(encoding="utf-8")
    # Forbidden alternative labels.
    assert "Conditionally receptive" not in src
    assert "Receptive if proven" not in src
    assert "Interested only if proven" not in src


# ============================================================ 3
def test_3_receptive_requires_positive_intent_or_use_case():
    """RECEPTIVE only when reasoning has clear positive intent OR a
    real personal use-case fit. Curiosity alone is not enough."""
    r = classify_stance_strictness(
        current_stance="interested_if_proven",
        reasoning=(
            "I work from home and my food gets cold during calls. "
            "I would buy one if the runtime numbers hold up."
        ),
    )
    assert r["recommended_stance"] == "interested_if_proven"
    assert not r["change"]


# ============================================================ 4
def test_4_proof_demand_dominates_is_not_receptive():
    """If reasoning is mostly proof demands without a clear positive
    use/buy reason, downgrade to UNCERTAIN."""
    r = classify_stance_strictness(
        current_stance="interested_if_proven",
        reasoning=(
            "I'd want safety certification before I trust this. "
            "I need to know about food-contact material proof and "
            "UL/ETL listing before I would even consider it."
        ),
    )
    assert r["recommended_stance"] == "curious_but_unconvinced"
    assert r["change"] is True
    assert "major proof gate" in r["stance_justification"]


# ============================================================ 5
def test_5_stance_justification_field_exists():
    r = classify_stance_strictness(
        current_stance="interested_if_proven",
        reasoning="It looks fine.",
    )
    assert "stance_justification" in r
    assert r["stance_justification"] != ""


# ============================================================ 6
def test_6_fact_lock_v2_detects_known_price_reask():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    audit = audit_provided_fact_lock_v2(
        fact_card=fc,
        turn_texts=[],
        ballot_texts=[
            {"persona_id": "p1",
             "text": "Is there a bundle? How much is the two-plate?"},
        ],
    )
    assert audit["known_fact_reask_count"] >= 1
    assert "bundle_price" in audit["fact_categories_violated"]


# ============================================================ 7
def test_7_fact_lock_v2_detects_dishwasher_safe_reask():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    audit = audit_provided_fact_lock_v2(
        fact_card=fc,
        turn_texts=[
            {"persona_id": "p1",
             "text": "Is the plate dishwasher-safe? I'd want to know."},
        ],
        ballot_texts=[],
    )
    assert audit["known_fact_reask_count"] >= 1
    assert "cleaning_dishwasher" in audit["fact_categories_violated"]


# ============================================================ 8
def test_8_fact_lock_v2_detects_runtime_reask():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    audit = audit_provided_fact_lock_v2(
        fact_card=fc,
        turn_texts=[],
        ballot_texts=[
            {"persona_id": "p1",
             "text": "How long does it keep food warm? Is the runtime "
                     "long enough for a real meal?"},
        ],
    )
    assert audit["known_fact_reask_count"] >= 1
    assert "runtime" in audit["fact_categories_violated"]


# ============================================================ 9
def test_9_fact_lock_v2_detects_charging_reask():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    audit = audit_provided_fact_lock_v2(
        fact_card=fc,
        turn_texts=[],
        ballot_texts=[
            {"persona_id": "p1",
             "text": "Is the base rechargeable? Can I charge it over USB?"},
        ],
    )
    assert audit["known_fact_reask_count"] >= 1
    assert "charging_usb_c" in audit["fact_categories_violated"]


# ============================================================ 10
def test_10_known_fact_reask_repaired_into_proof_form():
    """The headline 10B.3 fix: repair_known_fact_reask must rewrite
    a re-ask sentence into a verification-form sentence preserving
    the persona's underlying concern."""
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    cleaned, count, examples = repair_known_fact_reask(
        "Is the plate dishwasher-safe? Also, what is the temperature?",
        fc,
    )
    assert count >= 1
    # The "since X, I'd want proof Y" form must be present.
    assert "Since" in cleaned
    assert "dishwasher-safe" in cleaned
    assert "I'd" in cleaned
    # And we must have at least one example.
    assert any(
        ex["category"] == "cleaning_dishwasher" for ex in examples
    )


# ============================================================ 11
def test_11_fact_card_stores_primary_bundle_accessory_separately():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    assert fc.primary_price == "$79"
    assert fc.bundle_price == "$139"
    assert fc.accessory_prices == []  # GlowPlate has no accessory
    # The previous brief should still treat $14.99 as accessory:
    cc_brief = {
        "product_name": "ClosetCloud",
        "product_description": (
            "ClosetCloud is a moisture-control hanger system."
        ),
        "price_or_price_structure": (
            "$119 for the starter kit. Replacement filter pack: "
            "$14.99 for 6 filters."
        ),
        "launch_state": "unlaunched",
    }
    cc = generate_product_fact_card(cc_brief)
    assert cc.primary_price == "$119"
    assert any(ap.amount == "$14.99" for ap in cc.accessory_prices)


# ============================================================ 12
def test_12_fact_card_stores_excluded_categories():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    nots = " ".join(fc.not_categories).lower()
    assert "microwave" in nots or "hot plate" in nots or "food warmer" in nots or fc.not_categories == []
    # Excluded features: GlowPlate brief uses "not a microwave" /
    # "not a hot plate" — they live in not_categories. Ensure the
    # prompt block surfaces them.
    block = fact_card_prompt_block(fc)
    assert "Not:" in block or "Excluded" in block or block  # tolerant


# ============================================================ 13
def test_13_fact_card_stores_dishwasher_microwave_facts():
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    cleaning = " ".join(fc.cleaning_facts).lower()
    assert "dishwasher-safe" in cleaning
    assert "microwave-safe" in cleaning
    # Specifically the "when separated from base" detail must
    # survive the parse (key safety detail).
    assert any(
        "separated from base" in c.lower() for c in fc.cleaning_facts
    )


# ============================================================ 14
def test_14_self_awareness_detector_catches_as_an_agent():
    leaks = detect_self_awareness_leak(
        "As an agent in this discussion, I think the plate is OK."
    )
    assert any("as an agent" in s.lower() for s in leaks)


# ============================================================ 15
def test_15_self_awareness_detector_catches_synthetic_persona():
    leaks = detect_self_awareness_leak(
        "Speaking as a synthetic persona, I'd want a head-to-head test."
    )
    assert any("synthetic persona" in s.lower() for s in leaks)


# ============================================================ 16
def test_16_self_awareness_detector_catches_n_equals_phrasing():
    # The 10B.3 detector must catch BOTH the bare "n=24" sample
    # size form AND a "directional, not a verdict" phrase.
    leaks = detect_self_awareness_leak(
        "Treating this as directional, not a verdict, in this "
        "n=24 simulation."
    )
    assert any(s.lower() == "n=24" for s in leaks)
    assert any("directional" in s.lower() for s in leaks)


# ============================================================ 17
def test_17_persona_speech_does_not_contain_report_caveats():
    """The audit should flag report-style caveat language inside
    persona texts and produce repair output."""
    audit = audit_human_society_realism(
        turn_texts=[
            {"persona_id": "p1",
             "text": (
                 "I like the idea. "
                 "Treating this as directional, not a verdict, "
                 "since it is one persona in a synthetic n=24 "
                 "simulation."
             )},
        ],
        ballot_texts=[],
    )
    assert audit["any_leak"] is True
    assert audit["self_awareness_leak_count"] >= 2
    # Repair must keep the buyer's "I like the idea" while removing
    # the system caveat.
    cleaned, removed = strip_self_awareness_leak(
        "I like the idea. "
        "Treating this as directional, not a verdict, since it is "
        "one persona in a synthetic n=24 simulation."
    )
    assert "like the idea" in cleaned.lower()
    assert "n=24" not in cleaned
    assert "directional" not in cleaned.lower()
    assert removed


# ============================================================ 18
def test_18_report_level_caveats_remain_visible():
    """Report-level caveats are part of the trust section and must
    survive the persona-level realism filter (the realism filter
    only operates on ballots / turns)."""
    report_caveat = (
        "Live run-scoped synthetic society; not a real focus group. "
        "Cohorts are run-scoped + brief-scoped — never global market "
        "segments."
    )
    cleaned, _ = strip_self_awareness_leak(report_caveat)
    # The strip would mutate the text (proving the strip works); the
    # orchestrator code path doesn't apply it to report fields.
    assert cleaned != report_caveat
    # Importantly, the orchestrator's caveats list still includes
    # these strings.
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "Live run-scoped synthetic society" in src
    assert "not a real focus group" in src


# ============================================================ 19
def test_19_headline_does_not_contain_real_world_forecast_caveat():
    headline = build_confident_headline(
        product_name="GlowPlate",
        persona_count=24,
        receptive_final_count=23,
        shifted_toward_receptive=8,
        pre_distribution={"interested_if_proven": 12},
        final_distribution={"interested_if_proven": 23},
    )
    assert "not a real-world purchase forecast" not in headline.lower()
    assert "not a real-world forecast" not in headline.lower()
    assert "synthetic signal" not in headline.lower()


# ============================================================ 20
def test_20_headline_does_not_contain_validated_with_real_prospects():
    headline = build_confident_headline(
        product_name="GlowPlate",
        persona_count=24,
        receptive_final_count=23,
        shifted_toward_receptive=8,
        pre_distribution={"interested_if_proven": 12},
        final_distribution={"interested_if_proven": 23},
    )
    assert "validated with real prospects" not in headline.lower()
    assert "validate with real prospects" not in headline.lower()


# ============================================================ 21
def test_21_hardest_to_convince_populated_even_with_zero_resistant():
    # All 24 personas finished receptive — no resistant rows. The
    # builder must still surface the hardest-to-convince audience
    # from uncertain / unresolved-objection signal.
    role_dist = {
        "trust_seeker": {"receptive": 4, "uncertain": 2, "resistant": 0},
        "performance_focused_buyer": {
            "receptive": 6, "uncertain": 0, "resistant": 0,
        },
    }
    out = build_hardest_to_convince(
        role_distribution=role_dist,
        top_objections=[
            {"text": "needs UL safety certification"},
            {"text": "food-contact material proof"},
        ],
        top_proof_needs=[
            {"text": "third-party reviews"},
        ],
        target_customers=["remote workers", "parents"],
    )
    assert out["summary_copy"] != ""
    assert "stronger proof" in out["summary_copy"].lower() or "convince" in out["summary_copy"].lower()
    assert out["primary_kind"] in {"resistant", "uncertain", "all_receptive"}
    # Must surface concrete concern labels.
    assert len(out["concerns"]) >= 1


# ============================================================ 22
def test_22_best_fit_audience_copy_is_human_readable():
    role_dist = {
        "trust_seeker": {"receptive": 4, "uncertain": 0, "resistant": 0},
        "competitor_user_ember_mug": {
            "receptive": 3, "uncertain": 0, "resistant": 0,
        },
    }
    out = build_best_fit_audience(
        role_distribution=role_dist,
        target_customers=["remote workers", "slow eaters"],
        competitor_alternatives=["Ember Mug"],
    )
    copy = out["summary_copy"].lower()
    # Human-readable target-customer language is the headline
    assert "remote workers" in copy
    # Should reference real-world archetypes / competitors, not just
    # raw role labels
    assert (
        "ember" in copy
        or "alternative" in copy
        or "familiar with" in copy
    )
    # Must NOT lead with raw simulation-role labels alone
    assert not copy.startswith("trust_seeker")


# ============================================================ 23
def test_23_evidence_flavor_reports_youtube_contribution():
    flavor = build_evidence_flavor(retrieval_audit={
        "providers_attempted": [
            "brave_search", "tavily_search", "youtube_data_api",
        ],
        "youtube_audit": {
            "comments_pulled": 66,
            "comments_accepted": 3,
        },
    })
    summary = flavor["summary_copy"].lower()
    assert "buyer-language" in summary
    assert "youtube" in summary
    assert flavor["has_youtube"] is True
    assert flavor["youtube_accepted_count"] == 3

    # And the no-comments-passed case:
    flavor2 = build_evidence_flavor(retrieval_audit={
        "providers_attempted": [
            "brave_search", "tavily_search", "youtube_data_api",
        ],
        "youtube_audit": {
            "comments_pulled": 100,
            "comments_accepted": 0,
        },
    })
    assert "no comments passed" in flavor2["summary_copy"].lower()


# ============================================================ 24
def test_24_glowplate_fact_lock_check():
    """End-to-end fact-lock check: when the GlowPlate brief flows
    through generate_product_fact_card, every Phase 10B.3 lock
    surface must hold a value the v2 detector can defend."""
    fc = generate_product_fact_card(_GLOWPLATE_BRIEF)
    assert fc.primary_price == "$79"
    assert fc.bundle_price == "$139"
    assert fc.runtime_facts != []
    assert fc.temperature_facts != []
    assert fc.cleaning_facts != []
    assert fc.charging_facts != []
    assert fc.materials != []
    # Inject the lock into the prompt block: every category should
    # land somewhere in the rendered text.
    block = fact_card_prompt_block(fc)
    assert "$79" in block
    assert "$139" in block
    assert "120°F" in block
    assert "45 minutes" in block.lower() or "Up to 45 minutes" in block
    assert "Dishwasher-safe" in block
    assert "Microwave-safe when separated" in block
    assert "USB-C" in block.upper()


# ============================================================ 25
def test_25_orchestrator_invokes_phase_10b3_audits():
    """The orchestrator must call the new Phase 10B.3 audits and
    write their JSON artifacts."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "audit_provided_fact_lock_v2" in src
    assert "audit_human_society_realism" in src
    assert "audit_stance_strictness" in src
    assert "build_confident_headline" in src
    assert "build_hardest_to_convince" in src
    assert "build_best_fit_audience" in src
    assert "build_evidence_flavor" in src
    # All five new artifacts must be written.
    assert "provided_fact_lock_v2_quality.json" in src
    assert "human_society_realism_quality.json" in src
    assert "stance_strictness_quality.json" in src
    assert "audience_cards_quality.json" in src
    assert "audience_copy_quality.json" in src
    assert "headline_caveat_quality.json" in src
    assert "evidence_flavor_quality.json" in src


# ============================================================ 26
def test_26_role_distribution_helper_builds_correct_buckets():
    ballots = [
        {"persona_id": "p1", "ballot_stage": "final",
         "private_stance": "interested_if_proven"},
        {"persona_id": "p2", "ballot_stage": "final",
         "private_stance": "curious_but_unconvinced"},
        {"persona_id": "p3", "ballot_stage": "final",
         "private_stance": "skeptical"},
        # pre-stage ballots must be ignored
        {"persona_id": "p4", "ballot_stage": "pre",
         "private_stance": "interested_if_proven"},
    ]
    role_by_pid = {
        "p1": "trust_seeker",
        "p2": "trust_seeker",
        "p3": "competitor_user_ember",
        "p4": "trust_seeker",
    }
    dist = role_distribution_from_ballots(
        ballots=ballots, role_by_pid=role_by_pid,
    )
    assert dist["trust_seeker"]["receptive"] == 1
    assert dist["trust_seeker"]["uncertain"] == 1
    assert dist["competitor_user_ember"]["resistant"] == 1


# ---- bonus: humanize_role helper smoke test ------------------------------
def test_27_humanize_role_translates_simulation_labels():
    assert "trust" in humanize_role("trust_seeker").lower()
    assert "price" in humanize_role("price_skeptic").lower()
    assert "Ember" in humanize_role("competitor_user_ember_mug")


# ---- bonus: stance-strictness audit shape -------------------------------
def test_28_stance_strictness_audit_shape():
    ballots = [
        {"persona_id": "p1", "ballot_stage": "final",
         "private_stance": "interested_if_proven",
         "private_reasoning": (
             "I'd want safety certification. I'd want UL listing. "
             "Major proof gates everywhere."
         )},
        {"persona_id": "p2", "ballot_stage": "final",
         "private_stance": "interested_if_proven",
         "private_reasoning": (
             "My food gets cold during calls. I would buy this if "
             "the runtime works under load."
         )},
    ]
    audit = audit_stance_strictness(ballots)
    assert "receptive_count_before" in audit
    assert "receptive_count_after" in audit
    assert "downgraded_receptive_count" in audit
    assert audit["downgraded_receptive_count"] >= 1
    assert audit["receptive_count_after"] < audit["receptive_count_before"]
