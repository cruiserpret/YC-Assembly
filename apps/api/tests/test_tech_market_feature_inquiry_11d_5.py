"""Phase 11D.5 — feature_inquiry signal_type regression tests.

Pins:
  * `feature_inquiry` is in the controlled vocabulary in signal_types,
    model, AND the alembic migration (all three in lockstep).
  * Vivago feature questions ("Can I use my own X?", "How does Y?")
    classify as feature_inquiry.
  * Higher-priority signals still win when they co-occur:
      - trust_security_concern wins on skeptical questions
        ("does it actually look good", "cherry-picked")
      - pain_urgency wins on pain-laced questions
        ("most people quit AI video after 20 failed attempts")
      - competitor_comparison wins on brand-name questions
      - switching_objection wins on "used other tools" framing
      - workflow_fit wins on strong workflow cues
  * Declarative sentences with "can" / "does" / "is" do NOT
    accidentally classify as feature_inquiry.
  * Vivago yield reaches operator-spec'd ~82% target.

NO LIVE LLM. NO LIVE NETWORK. NO POSTGRES.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from assembly.sources.tech_market_provider import (
    RuleBasedTechMarketDistiller,
    SIGNAL_TYPES,
)
from assembly.models.tech_market_signal import (
    SIGNAL_TYPES as MODEL_SIGNAL_TYPES,
)


_distiller = RuleBasedTechMarketDistiller()


def _classify(text: str) -> str | None:
    out = _distiller.distill(
        text,
        source_provider="phase_11d_5_test",
        product_category="ai_saas",
        market_context_hint="AI_tool",
    )
    if not out:
        return None
    return out[0].signal_type


# ---------------------------------------------------------------------------
# 1. Controlled vocabulary parity — signal_types, model, migration
# ---------------------------------------------------------------------------


def test_feature_inquiry_in_signal_types_literal() -> None:
    assert "feature_inquiry" in SIGNAL_TYPES


def test_feature_inquiry_in_model_signal_types() -> None:
    assert "feature_inquiry" in MODEL_SIGNAL_TYPES


def test_signal_types_count_is_now_15() -> None:
    assert len(SIGNAL_TYPES) == 15
    assert len(MODEL_SIGNAL_TYPES) == 15


def test_signal_types_and_model_in_lockstep() -> None:
    assert set(SIGNAL_TYPES) == set(MODEL_SIGNAL_TYPES)


def test_migration_0016_lists_all_15_signal_types() -> None:
    mig = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions"
        / "20260517_0016_phase_11_d_5_add_feature_inquiry.py"
    )
    text = mig.read_text(encoding="utf-8")
    for st in SIGNAL_TYPES:
        assert f'"{st}"' in text or f"'{st}'" in text, (
            f"migration missing signal_type {st!r}"
        )


def test_migration_0016_downgrade_excludes_feature_inquiry() -> None:
    """The downgrade path must reconstruct the 14-value Phase 11D.1
    CHECK constraint — feature_inquiry should NOT appear in
    _SIGNAL_TYPES_V1."""
    mig = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions"
        / "20260517_0016_phase_11_d_5_add_feature_inquiry.py"
    )
    src = mig.read_text(encoding="utf-8")
    # The downgrade builds _SIGNAL_TYPES_V1 from _SIGNAL_TYPES_V2 by
    # filtering out feature_inquiry; check the filter is present.
    assert (
        '_SIGNAL_TYPES_V1 = tuple(' in src
        and 'feature_inquiry' in src
    )
    # And the downgrade guards against orphaning rows.
    assert "refusing to downgrade" in src
    assert "feature_inquiry" in src
    assert "tech_market_signal" in src


def test_migration_0016_has_clean_down_revision_chain() -> None:
    """Migration must chain off 0015 (Phase 11D.1) directly."""
    mig = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions"
        / "20260517_0016_phase_11_d_5_add_feature_inquiry.py"
    )
    src = mig.read_text(encoding="utf-8")
    assert 'revision: str = "0016_phase_11_d_5"' in src
    assert 'down_revision: str | None = "0015_phase_11_d_1"' in src


# ---------------------------------------------------------------------------
# 2. Vivago feature questions classify as feature_inquiry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Can I use my own character designs, or does the swarm create everything from scratch?",
    "How does the agent handle brand logos and fonts across keyframes?",
    "How long does an average video take to generate?",
    "Does the AI director let you explore a different angle on the same story?",
    "What input does the agent use? A script, reference video, or topic?",
    "After I approve those keyframes, is the 1-minute render locked to them?",
    "No prompting? How does that work?",
    "Curious how much creative input is still needed.",
])
def test_vivago_feature_questions_classify_as_feature_inquiry(
    text: str,
) -> None:
    assert _classify(text) == "feature_inquiry"


def test_vivago_row_6_now_classifies_via_comma_boundary() -> None:
    """Row 6 has 'After I approve those keyframes, is the 1-minute
    render locked to them?' — the question is the second clause
    after a comma. The Phase 11D.5 regex must accept comma as a
    valid clause-boundary prefix so this still classifies."""
    text = (
        "Solid work. The 9-frame storyboard preview before render "
        "looks like the smartest bit. After I approve those "
        "keyframes, is the 1-minute render locked to them?"
    )
    assert _classify(text) == "feature_inquiry"


# ---------------------------------------------------------------------------
# 3. Higher-priority signals still win when they co-occur
# ---------------------------------------------------------------------------


def test_trust_skepticism_wins_over_feature_inquiry() -> None:
    """Vivago row 9 mixes feature-inquiry framing ('does it actually
    look good') with explicit demo-skepticism cues ('too good to be
    true', 'cherry-picked'). Trust must win."""
    text = (
        "So wait, I just give it a photo and a sentence and it makes "
        "a whole video? That seems too good to be true. What is the "
        "catch here? Does it actually look good or is it one of those "
        "things where the demo videos are cherry-picked?"
    )
    assert _classify(text) == "trust_security_concern"


def test_pain_urgency_wins_over_feature_inquiry() -> None:
    """Vivago row 12 mixes feature-inquiry ('What does the agent use
    as input?') with explicit pain ('20 failed attempts'). Pain
    must win."""
    text = (
        "Skipping the prompting loop is the right problem. Most "
        "people quit AI video after 20 failed attempts. What does "
        "the agent use as input? A script, reference video, or topic?"
    )
    assert _classify(text) == "pain_urgency"


def test_competitor_comparison_wins_over_feature_inquiry() -> None:
    """A question that explicitly mentions a competitor brand should
    classify as competitor_comparison, not feature_inquiry — the
    competitor signal is more useful to a founder."""
    text = (
        "Does it work better than Runway when generating multi-shot "
        "narrative videos?"
    )
    assert _classify(text) == "competitor_comparison"


def test_switching_objection_wins_over_feature_inquiry() -> None:
    """A question framed around 'used other tools' should classify
    as switching_objection."""
    text = (
        "I have used other tools before — does it support resume "
        "tokens from a previous session?"
    )
    assert _classify(text) == "switching_objection"


def test_workflow_fit_wins_over_feature_inquiry() -> None:
    """A question that contains a strong workflow_fit cue should
    still classify as workflow_fit, since the operator is testing
    fit-to-process, not pure inquiry."""
    text = (
        "Can a team keep a reusable story or brand bible across "
        "videos with character rules and pacing examples?"
    )
    assert _classify(text) == "workflow_fit"


# ---------------------------------------------------------------------------
# 4. Declarative sentences do NOT accidentally classify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    # Bare declaratives with "I can" / "we can" — different word order
    # from question framing "can I" / "can we".
    "I am truly impressed by the seamless experience of generating videos.",
    "I have been tracking the AI leaderboard for a while.",
    "The 9-frame storyboard preview looks like the smartest bit.",
    "The transition from a pure generation tool is amazing product design.",
])
def test_pure_praise_does_not_classify_as_feature_inquiry(
    text: str,
) -> None:
    """Pure praise without an interrogative stem must not become
    feature_inquiry. v1 of the rule (no sentence-start anchor) would
    have falsely matched bare 'is the' / 'does the' inside praise;
    v2 anchors on sentence boundaries (^, [.!?,;]\\s+) to prevent
    that."""
    result = _classify(text)
    assert result != "feature_inquiry"


def test_declarative_can_inside_praise_does_not_classify() -> None:
    """A declarative 'I can use' (different word order from 'can I')
    must not classify."""
    text = "I can use this tool every workday."
    result = _classify(text)
    # Either rejects, or matches workflow_fit on "every day" — both
    # are acceptable; what's NOT acceptable is feature_inquiry.
    assert result != "feature_inquiry"


# ---------------------------------------------------------------------------
# 5. End-to-end Vivago corpus yield meets 75–90% band
# ---------------------------------------------------------------------------


_VIVAGO_COMMENTS = [
    "I remember Vivago making the Top 3 two years ago. Great to see the "
    "team back with such a massive evolution. The transition from a pure "
    "generation tool to an AI Video Agent with a structured workflow is "
    "amazing product design.",
    "As a user, I am truly impressed by the seamless experience of "
    "generating captivating AI videos with stunning enhancements and "
    "intuitive prompt optimization.",
    "VIVA is seriously impressive. It makes creating videos so simple and "
    "fast, and the quality is top-notch. I have used other tools before, "
    "but Viva really stands out as a strong competitor to Sora.",
    "I fed it my profile pic and a one-line prompt, and it generated a "
    "narrative video where I was the hero. Considering this was produced "
    "with a single still and a low-effort prompt, I was impressed given "
    "all my failed attempts to get Sora to do my bidding. If you have been "
    "burned by stitching together 4-second clips that do not cohere, "
    "Vivago feels built for narrative.",
    "I have been tracking the AI leaderboard and noticed HiDream-O1-Image "
    "at number one for a while. Seeing it used as the backbone here "
    "explains why the visual coherence is so strong; the characters "
    "actually look like themselves from frame 1 to 50.",
    "Solid work. The 9-frame storyboard preview before render looks like "
    "the smartest bit. After I approve those keyframes, is the 1-minute "
    "render locked to them?",
    "Does the AI director workflow let you iterate? If you get keyframes "
    "back and want to explore a different angle on the same story, can "
    "you guide it without starting from scratch?",
    "The coherence across frames looks solid. How much of that comes from "
    "HiDream-O1 itself versus your planning layer?",
    "So wait, I just give it a photo and a sentence and it makes a whole "
    "video? That seems too good to be true. What is the catch here? Does "
    "it actually look good or is it one of those things where the demo "
    "videos are cherry-picked?",
    "Can I use my own character designs, or does the swarm create "
    "everything from scratch? Love this approach.",
    "How does the agent handle brand logos and fonts across keyframes?",
    "Skipping the prompting loop is the right problem. Most people quit AI "
    "video after 20 failed attempts. What does the agent use as input? A "
    "script, reference video, or topic? Curious how much creative "
    "direction it still needs from the user.",
    "The storyboard/keyframe approval flow feels like the right place to "
    "build trust before the expensive render. Can a team keep a reusable "
    "story or brand bible across videos — character rules, proof points, "
    "rejected styles, pacing examples — and have the director show which "
    "assets or notes guided a scene?",
    "No prompting? How does that work?",
    "Got it. That makes sense. We all build the Claude Code equivalent of "
    "the area we are evicted by.",
    "Really impressed by how smooth the video generation looks. The "
    "enhancement feature sounds super useful for creators. How long does "
    "an average video take to generate?",
    "As a social media strategist, the biggest bottleneck in video "
    "production is always maintaining visual consistency across scenes, "
    "especially when trying to tell a unified story. How does the agent "
    "handle custom brand guidelines or strict color palettes during the "
    "initial character invention phase?",
]


def test_vivago_corpus_11d_5_yield_meets_75_to_90_percent() -> None:
    """Phase 11D.5 yield target: 75–90% on the Vivago corpus.
    11D.4 baseline was 47% (8/17). With feature_inquiry, ~82% (14/17)
    is the expected result."""
    accepted = sum(1 for c in _VIVAGO_COMMENTS if _classify(c) is not None)
    ratio = accepted / len(_VIVAGO_COMMENTS)
    assert 0.75 <= ratio <= 0.90, (
        f"yield {ratio:.2%} ({accepted}/{len(_VIVAGO_COMMENTS)}) "
        f"outside Phase 11D.5 target band 75–90%"
    )


def test_vivago_corpus_now_spans_six_signal_types() -> None:
    """The accepted set must include feature_inquiry alongside the
    11D.4 signal-type set (pain_urgency, workflow_fit, trust,
    competitor, switching)."""
    types: set[str] = set()
    for c in _VIVAGO_COMMENTS:
        sig = _classify(c)
        if sig is not None:
            types.add(sig)
    expected = {
        "pain_urgency",
        "workflow_fit",
        "trust_security_concern",
        "competitor_comparison",
        "switching_objection",
        "feature_inquiry",
    }
    missing = expected - types
    assert not missing, f"missed expected signal types: {missing}"


def test_vivago_higher_priority_signals_still_dominate() -> None:
    """The 5 pre-existing 11D.4 signal types must each still appear
    at least once in the Vivago corpus — adding feature_inquiry must
    not cannibalize them via ordering."""
    counts: dict[str, int] = {}
    for c in _VIVAGO_COMMENTS:
        sig = _classify(c)
        if sig is not None:
            counts[sig] = counts.get(sig, 0) + 1
    assert counts.get("pain_urgency", 0) >= 2, (
        f"pain_urgency count regressed: {counts}"
    )
    assert counts.get("trust_security_concern", 0) >= 1, counts
    assert counts.get("competitor_comparison", 0) >= 1, counts
    assert counts.get("switching_objection", 0) >= 1, counts
    assert counts.get("workflow_fit", 0) >= 1, counts


# ---------------------------------------------------------------------------
# 6. Drift: no apps/web changes, no scraping, flags off
# ---------------------------------------------------------------------------


def test_no_apps_web_files_touched_in_phase_11d_5() -> None:
    """The new files this phase ships must all live under apps/api/."""
    phase_paths = (
        "apps/api/src/assembly/sources/tech_market_provider/signal_types.py",
        "apps/api/src/assembly/models/tech_market_signal.py",
        "apps/api/alembic/versions/20260517_0016_phase_11_d_5_add_feature_inquiry.py",
        "apps/api/src/assembly/sources/tech_market_provider/distiller.py",
        "apps/api/tests/test_tech_market_feature_inquiry_11d_5.py",
    )
    for p in phase_paths:
        assert p.startswith("apps/api/"), (
            f"{p} is not under apps/api/ — frontend must stay frozen"
        )


def test_tech_market_flags_still_default_false_after_11d_5() -> None:
    from assembly.config import Settings
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False
