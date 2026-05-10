"""Phase 10B.6 — Explicit Negative-Feature Fact Lock acceptance tests.

Covers the 7 required tests from the Phase 10B.6 spec:

  1. CalmCue-style "no camera / no microphone" brief produces
     forbidden_features containing camera + microphone.
  2. Agent text mentioning camera/mic is flagged and repaired.
  3. A different product with "does not have GPS" blocks GPS
     mentions.
  4. A different product with "does not record audio" blocks
     audio-recording claims.
  5. A product that actually has a camera is still allowed to
     discuss camera correctly.
  6. No production code branches on product_name == "CalmCue".
  7. CalmCue appears only in tests, fixtures, audit reports, or
     examples — never in production runtime code.
"""
from __future__ import annotations

from pathlib import Path

from assembly.sources.product_grounding import (
    audit_forbidden_features,
    expand_forbidden_tokens,
    extract_forbidden_features,
    fact_card_prompt_block,
    generate_product_fact_card,
    repair_forbidden_feature_mentions,
)


_CALMCUE_BRIEF = {
    "product_name": "CalmCue",
    "product_description": (
        "CalmCue is an unlaunched haptic wristband. It does not have "
        "a microphone, does not have a camera, does not record "
        "conversations, and does not listen to the user. It uses "
        "heart-rate, skin-temperature, and motion sensing. It is "
        "not a medical device."
    ),
    "price_or_price_structure": "$89 one-time",
    "launch_state": "unlaunched",
    "target_customers": ["college students"],
    "competitors_or_alternatives": ["Apollo Neuro"],
}


# ============================================================ 1
def test_1_calmcue_brief_produces_has_camera_false_has_microphone_false():
    """The fact lock must record both 'camera' and 'microphone' as
    forbidden features for a CalmCue-style brief."""
    fc = generate_product_fact_card(_CALMCUE_BRIEF)
    names = [f.canonical_name for f in fc.forbidden_features]
    assert "camera" in names
    assert "microphone" in names
    # feature_exists is the structured "no" signal the spec asks for
    for f in fc.forbidden_features:
        if f.canonical_name in {"camera", "microphone"}:
            assert f.feature_exists is False
            assert f.feature_forbidden is True
            # Source sentence preserved verbatim
            assert "does not have" in f.source_sentence.lower()


# ============================================================ 2
def test_2_agent_text_mentioning_camera_or_mic_flagged_and_repaired():
    fc = generate_product_fact_card(_CALMCUE_BRIEF)
    bad_text = (
        "What is the camera doing on my wrist? "
        "Also, does the mic listen all the time? "
        "I do like that it uses heart-rate sensing though."
    )
    audit = audit_forbidden_features(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": bad_text}],
        ballot_texts=[],
    )
    assert audit["positive_mention_count"] >= 2
    assert "camera" in audit["by_feature"]
    assert "microphone" in audit["by_feature"]
    cleaned, repair_count, examples = repair_forbidden_feature_mentions(
        bad_text, fc,
    )
    assert repair_count >= 2
    # Both forbidden mentions must be gone from the cleaned text
    assert "camera doing on my wrist" not in cleaned
    assert "the mic listen" not in cleaned
    # The buyer's heart-rate sentence must survive
    assert "heart-rate sensing" in cleaned
    # The replacement sentence references the brief
    assert "the brief says CalmCue does not have" in cleaned
    # Examples carry before/after pairs
    assert any(ex["feature"] == "camera" for ex in examples)
    assert any(ex["feature"] == "microphone" for ex in examples)


# ============================================================ 3
def test_3_different_product_with_no_gps_blocks_gps_mentions():
    """Universality check: a privacy-first watch with 'does not have
    GPS' must extract gps as forbidden AND flag agents that mention
    a GPS chip."""
    brief = {
        "product_name": "NoTrack",
        "product_description": (
            "NoTrack is a privacy-first fitness watch. It does not "
            "have GPS. It uses step-count and heart-rate only."
        ),
        "price_or_price_structure": "$59",
        "launch_state": "unlaunched",
        "target_customers": ["privacy-conscious runners"],
        "competitors_or_alternatives": ["Garmin"],
    }
    fc = generate_product_fact_card(brief)
    names = [f.canonical_name for f in fc.forbidden_features]
    assert "gps" in names
    bad = "How accurate is the GPS chip in this thing?"
    audit = audit_forbidden_features(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": bad}],
        ballot_texts=[],
    )
    assert audit["positive_mention_count"] >= 1
    assert "gps" in audit["by_feature"]


# ============================================================ 4
def test_4_different_product_with_no_audio_recording_blocks_audio_claims():
    brief = {
        "product_name": "WhisperHome",
        "product_description": (
            "WhisperHome is a smart-home hub. It does not record "
            "audio. The microphone is muted by default and only "
            "wakes on a physical button press."
        ),
        "price_or_price_structure": "$129",
        "launch_state": "unlaunched",
        "target_customers": ["privacy-conscious households"],
        "competitors_or_alternatives": ["Amazon Echo"],
    }
    fc = generate_product_fact_card(brief)
    names = [f.canonical_name for f in fc.forbidden_features]
    # "does not record audio" extracts canonical_name = "audio"
    assert "audio" in names
    bad = "I want to know how often it records audio in the background."
    audit = audit_forbidden_features(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": bad}],
        ballot_texts=[],
    )
    # The agent's "records audio" mention must be flagged
    assert audit["positive_mention_count"] >= 1


# ============================================================ 5
def test_5_product_with_a_camera_is_still_allowed_to_discuss_camera():
    """A product that AFFIRMS having a camera (PantryPulse-style)
    must NOT add 'camera' to the forbidden list. Agent text
    discussing the camera positively must not be flagged."""
    brief = {
        "product_name": "PantryPulse",
        "product_description": (
            "PantryPulse uses a tiny wide-angle camera plus "
            "barcode/NFC scanning. It captures still images of "
            "shelves and labels during scan events. It does not "
            "record video."
        ),
        "price_or_price_structure": "$149",
        "launch_state": "unlaunched",
        "target_customers": ["urban renters"],
        "competitors_or_alternatives": ["AnyList"],
    }
    fc = generate_product_fact_card(brief)
    names = [f.canonical_name for f in fc.forbidden_features]
    assert "camera" not in names
    # 'video' should be forbidden (does not record video)
    assert "video" in names
    # Sensing fact lock affirms the camera
    assert fc.sensing_facts.get("has_camera") is True
    # Agent positively discussing the camera is fine
    good = (
        "I like that PantryPulse uses a camera for still shelf "
        "captures during scans."
    )
    audit = audit_forbidden_features(
        fact_card=fc,
        turn_texts=[{"persona_id": "p1", "text": good}],
        ballot_texts=[],
    )
    assert audit["positive_mention_count"] == 0


# ============================================================ 6
def test_6_no_production_code_branches_on_product_name_calmcue():
    """No production source file may branch on the literal string
    'CalmCue'. CalmCue is a test fixture, not a runtime case."""
    src_root = Path(__file__).resolve().parent.parent / "src"
    forbidden_pattern_substrings = (
        'product_name == "CalmCue"',
        "product_name == 'CalmCue'",
        '== "CalmCue"',
        "== 'CalmCue'",
        'if "CalmCue"',
        "if 'CalmCue'",
        '"CalmCue":',
        "'CalmCue':",
        "in [\"CalmCue\"",
        "in ['CalmCue'",
    )
    findings: list[str] = []
    for path in src_root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if "CalmCue" not in text:
            continue
        for needle in forbidden_pattern_substrings:
            if needle in text:
                findings.append(f"{path.relative_to(src_root)}: {needle}")
    assert findings == [], f"production code branches on CalmCue: {findings}"


# ============================================================ 7
def test_7_calmcue_appears_only_in_tests_fixtures_audits_examples():
    """CalmCue may appear in test files, scripts, audit reports, or
    docstring examples — never as a runtime case in production
    source. Reuse the same forbidden-substring sweep as test 6
    and additionally assert that any remaining matches are in
    comments / docstrings (not in active code)."""
    src_root = Path(__file__).resolve().parent.parent / "src"
    runtime_matches: list[str] = []
    for path in src_root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        if "CalmCue" not in text:
            continue
        # Walk the file line-by-line. A "runtime" mention is one that
        # is NOT inside a string literal acting as a docstring/comment
        # and NOT inside a Python comment (#). Heuristic: skip any
        # line whose stripped form starts with `#` or `"""` or sits
        # between triple-quote pairs.
        in_triple = False
        for raw in text.splitlines():
            line = raw.strip()
            triple_count = line.count('"""') + line.count("'''")
            if triple_count % 2 == 1:
                in_triple = not in_triple
                continue
            if in_triple:
                continue
            if line.startswith("#"):
                continue
            if "CalmCue" in line:
                runtime_matches.append(
                    f"{path.relative_to(src_root)}: {line[:120]}"
                )
    assert runtime_matches == [], (
        f"CalmCue found in non-comment production lines: "
        f"{runtime_matches}"
    )


# ---- bonus: prompt block surfaces the forbidden features so the
# LLM can avoid them at generation time, not just at audit time. ----
def test_prompt_block_lists_forbidden_features():
    fc = generate_product_fact_card(_CALMCUE_BRIEF)
    block = fact_card_prompt_block(fc)
    assert "Features the brief explicitly says the product does NOT have" in block
    assert "camera" in block
    assert "microphone" in block
    assert "FORBIDDEN FEATURES" in block


# ---- bonus: extractor handles all 5 negation kinds from the spec ----
def test_extractor_handles_all_negation_kinds():
    for blob, expected in [
        ("It does not have a camera.", "camera"),
        ("It doesn't have a microphone.", "microphone"),
        ("It does not use GPS.", "gps"),
        ("It does not record audio.", "audio"),
        ("It does not listen.", "audio recording"),
        ("It is not a medical device.", "medical device"),
        ("No camera. No microphone.", "camera"),
    ]:
        found = extract_forbidden_features(product_description=blob)
        names = [f.canonical_name for f in found]
        assert expected in names, f"failed on {blob!r}: got {names}"


# ---- bonus: synonym expansion is universal ----
def test_synonym_expansion_for_camera_covers_lens_and_cam():
    fc = generate_product_fact_card(_CALMCUE_BRIEF)
    cam_feature = next(
        f for f in fc.forbidden_features if f.canonical_name == "camera"
    )
    tokens = expand_forbidden_tokens(cam_feature)
    assert "camera" in tokens
    assert "cam" in tokens
    assert "lens" in tokens


# ---- bonus: orchestrator invokes the 10B.6 audit + writes JSON ----
def test_orchestrator_invokes_forbidden_features_audit():
    src_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    src = src_path.read_text(encoding="utf-8")
    assert "audit_forbidden_features" in src
    assert "repair_forbidden_feature_mentions" in src
    assert "forbidden_features_quality.json" in src
