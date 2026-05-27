"""Scoped user-facing language scan tests.

The plain `scan_user_facing_language(text)` correctly enforces the
"no LLM verdicts" product rule for the report writer's prose. But
when the scan runs against the WHOLE main_report payload (including
verbatim persona quotes in the full debate transcript and persona
reasoning cards), it false-positives on legitimate persona-voice
content like "I'd kill this if my team built it" — that's evidence
of skepticism, not the report writer issuing a kill verdict.

These tests pin the structural fix: `scan_main_report_summary_language`
walks main_report and skips persona-voice subtrees, scanning only
LLM-summary fields. Persona quotes are still surfaced verbatim to
the founder (they're independently sanitized for fake-product-use
claims by forbidden_claim_audit in discussion_layer/validators.py).
"""

from __future__ import annotations

from assembly.orchestration.live_quality_gates import (
    _PERSONA_VOICE_REPORT_KEYS,
    _collect_summary_text,
    scan_main_report_summary_language,
    scan_user_facing_language,
)


# ---------- Plain scanner stays strict ----------


def test_plain_scanner_still_blocks_kill_verdict():
    """Sanity: the plain text-input scanner keeps the strict rule.
    The fix is at the scoping layer, not the pattern layer."""
    audit = scan_user_facing_language(
        "Recommendation: kill this product before launch."
    )
    assert audit["any_violations"] is True
    labels = [f["label"] for f in audit["findings"]]
    assert "kill verdict" in labels


def test_plain_scanner_still_blocks_launch_verdict():
    audit = scan_user_facing_language(
        "The data is conclusive — launch this immediately."
    )
    assert audit["any_violations"] is True


def test_plain_scanner_clean_on_neutral_text():
    audit = scan_user_facing_language(
        "The synthetic society finished with a mixed reaction."
    )
    assert audit["any_violations"] is False


# ---------- Scoped scanner — persona-voice subtrees ----------


def test_scoped_scanner_allows_kill_this_in_full_debate_transcript():
    """A persona saying 'I'd kill this' in the debate transcript is
    legitimate evidence. The scoped scanner must NOT flag it."""
    report = {
        "executive_summary": ["Mixed reaction across the synthetic society."],
        "full_debate": {
            "discussion_session": {"persona_count": 24},
            "discussion_transcript": {
                "groups": [
                    {
                        "group_index": 0,
                        "rounds": [
                            {
                                "round_number": 1,
                                "turns": [
                                    {
                                        "speaker_name": "Yarrow A.",
                                        "public_text": (
                                            "Honestly? I'd kill this "
                                            "before letting it ship. "
                                            "The DX is broken."
                                        ),
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        },
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is False, (
        f"persona-voice 'kill this' must NOT trip the scan; "
        f"got findings: {audit['findings']}"
    )
    assert "full_debate" in audit["scope_excluded_keys"]


def test_scoped_scanner_allows_kill_this_in_persona_reasoning_cards():
    report = {
        "executive_summary": ["Mixed reaction across the synthetic society."],
        "persona_reasoning_cards": [
            {
                "persona_id": "abc",
                "top_objection": {
                    "text": (
                        "I would kill this if it landed on my team. "
                        "Way too much friction for the value claimed."
                    ),
                },
            },
        ],
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is False
    assert "persona_reasoning_cards" in audit["scope_excluded_keys"]


def test_scoped_scanner_allows_kill_this_in_representative_debates():
    report = {
        "executive_summary": ["Skeptical room with hard objections."],
        "representative_debates": [
            {
                "persona_id": "p1",
                "private_reasoning_excerpt": (
                    "Kill this. Nothing in the proof addressed our "
                    "switching cost from the incumbent."
                ),
            },
        ],
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is False


def test_scoped_scanner_allows_market_will_in_persona_quote():
    """A persona predicting 'the market will reject this' is still
    persona-voice evidence, not a report-writer market forecast."""
    report = {
        "executive_summary": ["Mixed reaction."],
        "full_debate": {
            "groups": [
                {
                    "rounds": [
                        {
                            "turns": [
                                {
                                    "speaker_name": "Persona A",
                                    "public_text": (
                                        "The market will adopt the "
                                        "incumbent long before they "
                                        "trust this newcomer."
                                    ),
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is False


# ---------- Scoped scanner — LLM-summary fields still strict ----------


def test_scoped_scanner_blocks_kill_verdict_in_executive_summary():
    """If the report-writer LLM produces 'kill this' in the
    summary, the scan MUST still block. The strict rule for the
    report writer is unchanged."""
    report = {
        "executive_summary": [
            "Founder recommendation: kill this idea and pivot to "
            "the adjacency.",
        ],
        "full_debate": {"groups": []},  # persona-voice subtree, ignored
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is True
    assert any(
        f["label"] == "kill verdict" for f in audit["findings"]
    )


def test_scoped_scanner_blocks_launch_verdict_in_recommendations():
    report = {
        "recommended_next_tests": [
            "Launch this in San Francisco metro before Q4.",
        ],
        "full_debate": {"groups": []},
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is True


def test_scoped_scanner_blocks_market_percentage_forecast_in_summary():
    report = {
        "executive_summary": [
            "Conservative read: 30% of the market will adopt this "
            "within 18 months.",
        ],
        "full_debate": {"groups": []},
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is True


def test_scoped_scanner_blocks_outcome_guarantee_in_caveats():
    report = {
        "caveats": [
            "Founders following these recommendations are guaranteed "
            "to succeed in this market.",
        ],
        "full_debate": {"groups": []},
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is True


# ---------- Real-world regression: the exact user-reported scenario ----------


def test_regression_persona_voice_does_not_block_report_for_blunt_briefs():
    """The exact failure mode from production: a competitor-heavy
    brief produces a debate transcript where multiple personas use
    blunt rejection language. Pre-fix this caused a 'kill verdict' /
    'kill verdict' double-violation that aborted the report at the
    generating_report stage.

    Post-fix: persona-voice quotes are not in scan scope; the report
    completes and surfaces those quotes verbatim as evidence."""
    report = {
        "executive_summary": [
            "The synthetic society finished with a sceptical lean.",
        ],
        "top_objections": [
            {"bucket": "price_too_high", "weighted_score": 0.71},
        ],
        "full_debate": {
            "discussion_session": {"persona_count": 24},
            "discussion_transcript": {
                "groups": [
                    {
                        "group_index": 0,
                        "rounds": [
                            {
                                "round_number": 1,
                                "turns": [
                                    {
                                        "speaker_name": "Tanith C.",
                                        "public_text": (
                                            "I'd kill this before "
                                            "it shipped. The pricing "
                                            "alone is non-starter."
                                        ),
                                    },
                                    {
                                        "speaker_name": "Marlowe G.",
                                        "public_text": (
                                            "Honestly, kill this and "
                                            "spend the engineering "
                                            "hours on what AnyList "
                                            "already does for $10/year."
                                        ),
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        },
        "persona_reasoning_cards": [
            {
                "persona_id": "p1",
                "top_objection": {
                    "text": "I'd kill this if my team built it.",
                },
            },
        ],
    }
    audit = scan_main_report_summary_language(report)
    assert audit["any_violations"] is False, (
        f"the exact pre-fix failure mode is back: {audit['findings']}"
    )


# ---------- Walker correctness ----------


def test_walker_collects_strings_from_nested_summary_fields():
    report = {
        "executive_summary": ["lineA", "lineB"],
        "caveats": ["caveat1"],
        "nested": {"deeper": {"value": "deepLine"}},
    }
    texts = _collect_summary_text(report)
    assert "lineA" in texts
    assert "lineB" in texts
    assert "caveat1" in texts
    assert "deepLine" in texts


def test_walker_skips_persona_voice_subtree_entirely():
    report = {
        "executive_summary": ["keepThis"],
        "full_debate": {"any": {"depth": "skipThis"}},
        "persona_reasoning_cards": [{"text": "skipThisToo"}],
    }
    texts = _collect_summary_text(report)
    assert "keepThis" in texts
    assert "skipThis" not in texts
    assert "skipThisToo" not in texts


def test_persona_voice_keys_is_a_known_set():
    """Defensive: the deny-list of persona-voice keys is explicit and
    documented. Anyone widening main_report with new persona-voice
    fields must add them to _PERSONA_VOICE_REPORT_KEYS."""
    assert "full_debate" in _PERSONA_VOICE_REPORT_KEYS
    assert "persona_reasoning_cards" in _PERSONA_VOICE_REPORT_KEYS
    assert "representative_debates" in _PERSONA_VOICE_REPORT_KEYS
    # Keys we expect to ALWAYS be scanned (LLM-summary). If anyone
    # ever adds these to the persona-voice deny-list, that would
    # silently disable the verdict-language scan and this test will
    # surface it.
    assert "executive_summary" not in _PERSONA_VOICE_REPORT_KEYS
    assert "recommended_next_tests" not in _PERSONA_VOICE_REPORT_KEYS
    assert "caveats" not in _PERSONA_VOICE_REPORT_KEYS


def test_scoped_scanner_audit_includes_scope_metadata():
    """The audit dict must self-describe its scoping so the artifact
    on disk makes it obvious to a future reader that persona-voice
    subtrees were intentionally excluded."""
    audit = scan_main_report_summary_language({"executive_summary": ["ok"]})
    assert "scope" in audit
    assert "scope_excluded_keys" in audit
    assert isinstance(audit["scope_excluded_keys"], list)
    assert "full_debate" in audit["scope_excluded_keys"]
