"""Phase 12F.1 — markdown renderer extension tests.

Covers the 10 invariants for the additive markdown renderer:

  1.  founder_report.md (rendered string) includes the explainability section.
  2.  confidence.limited_by entries appear in the rendered markdown.
  3.  Persona reasoning cards render when present (one block per card,
      with sourced anchors).
  4.  Niche signals render when present (minority objections,
      unexpected segments, edge-case use cases, one question).
  5.  Sparse / empty data renders gracefully — explicit "not provided"
      lines instead of silent hiding.
  6.  No forbidden chain-of-thought keys leak into the markdown.
  7.  No apps/web changes (git-status guard).
  8.  No new alembic migration was added.
  9.  No new LLM calls — renderer module imports without provider SDK.
  10. The renderer is purely additive — input dicts are not mutated.

All tests are pure-python: no DB, no LLM, no network.
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from assembly.explainability.markdown_render import (
    render_12f1_markdown_section,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


def _explainability_full() -> dict:
    return {
        "phase": "12f.1",
        "decision_being_tested": "Should we prioritize migration tools?",
        "what_would_change_founder_mind": (
            "If 2/3 of interviewees prefer SSO over migration."
        ),
        "inputs_used": {
            "fields_provided": ["company_stage", "founder_hypothesis"],
            "fields_missing": ["uploaded_artifacts", "optional_context"],
            "n_provided": 2,
            "n_total_optional": 4,
        },
        "source_audience_profile": {
            "profile_used": "hn_show_hn",
            "role_mix_pct": {"target_customer_evaluator": 22.0,
                             "existing_competitor_user": 15.0},
            "rationale": (
                "Brief specified launch_source=hn_show_hn; profile "
                "proportions are calibration-stage priors."
            ),
        },
        "persona_composition": {
            "n_total": 24,
            "by_audience_role": {"target_customer_evaluator": 18,
                                 "existing_competitor_user": 6},
            "by_segment_label": {"trust_seeker_0": 12, "competitor_user_docusign": 6},
            "by_scorable_status": {"scorable": 24},
            "n_synthetic_non_customer_voices": 2,
        },
        "evidence_snapshot": {
            "snapshot_present": True,
            "evidence_snapshot_id": "evsnap_xyz_123",
            "snapshot_hash": "abc",
            "brief_hash": "def",
            "raw_result_count": 47,
            "accepted_evidence_count": 32,
            "by_source": {"hn": 12, "blog": 8},
        },
        "assumptions_in_play": [
            {"id": "source_profile_prior_hn_show_hn",
             "statement": "Weak prior calibrated on a single product.",
             "impact": "Could over- or under-estimate non-customer voice mass."},
        ],
        "bucket_explanations": {
            "buyer": {"count": 0, "pct": 0.0,
                      "top_drivers": [], "top_blockers": [],
                      "evidence_anchors_sample": []},
            "receptive": {"count": 8, "pct": 33.3,
                          "top_drivers": [{"text": "self-serve trial", "raised_by_count": 4}],
                          "top_blockers": [{"text": "case_study_needed", "raised_by_count": 3}],
                          "evidence_anchors_sample": ["rule:would_consider_if_proven (role=target)"]},
            "uncertain": {"count": 10, "pct": 41.7,
                          "top_drivers": [],
                          "top_blockers": [{"text": "audit_trail_proof", "raised_by_count": 5}],
                          "evidence_anchors_sample": ["rule:wait_and_see"]},
            "skeptical": {"count": 6, "pct": 25.0,
                          "top_drivers": [],
                          "top_blockers": [{"text": "migration_guide", "raised_by_count": 4}],
                          "evidence_anchors_sample": ["rule:loyal_to_current_alternative"]},
        },
        "confidence": {
            "level": "medium",
            "score": 0.683,
            "score_raw": 0.683,
            "limited_by": [
                "source_audience_profile_only_weakly_calibrated",
                "no_uploaded_customer_evidence",
            ],
            "would_increase_if": [
                "upload 5+ real customer quotes",
                "specify tiered pricing",
            ],
            "breakdown": {
                "company_context_completeness": 0.5,
                "evidence_quality": 0.681,
                "source_audience_profile_confidence": 0.55,
                "validation_support_count": 0.431,
                "persona_diversity_health": 0.65,
                "input_ambiguity": 0.6,
                "pricing_specificity": 1.0,
                "competitor_clarity": 1.0,
                "uploaded_customer_evidence_count": 0.0,
            },
            "weights": {
                "company_context_completeness": 0.15,
                "evidence_quality": 0.2,
                "source_audience_profile_confidence": 0.15,
                "validation_support_count": 0.1,
                "persona_diversity_health": 0.1,
                "input_ambiguity": 0.1,
                "pricing_specificity": 0.05,
                "competitor_clarity": 0.05,
                "uploaded_customer_evidence_count": 0.1,
            },
            "cap_applied": False,
            "cap": 0.85,
        },
        "_caveat": "Structured reasoning artifacts only.",
    }


def _persona_cards_full() -> list[dict]:
    return [
        {
            "persona_id": "p00",
            "audience_role": "target_customer_evaluator",
            "segment_label": "trust_seeker_0",
            "is_synthetic_non_customer_voice": False,
            "initial_stance": "curious_but_unconvinced",
            "final_stance": "interested_if_proven",
            "final_bucket": "receptive",
            "top_objection": {
                "text": "pricing seems high for small teams",
                "evidence_anchor": "rule:would_consider_if_proven (role=target)",
            },
            "top_proof_need": {
                "text": "case_study_from_similar_company",
                "evidence_anchor": "rule:would_consider_if_proven (role=target)",
            },
            "what_moved_or_failed_to_move_them": {
                "summary": "Moved from `curious_but_unconvinced` to `interested_if_proven`.",
                "triggered_by_kind": "private_stance_delta",
                "evidence_anchor": "rule:would_consider_if_proven (role=target)",
            },
            "adoption_trigger": {
                "text": "3+ similar logos visible",
                "evidence_anchor": "rule:would_consider_if_proven (role=target)",
            },
            "stayed_x_because": None,
            "confidence_in_this_persona": "medium",
            "bucket_routing_note": None,
        },
        {
            "persona_id": "p04",
            "audience_role": "existing_competitor_user",
            "segment_label": "competitor_user_docusign",
            "is_synthetic_non_customer_voice": False,
            "initial_stance": "skeptical",
            "final_stance": "skeptical",
            "final_bucket": "skeptical",
            "top_objection": {
                "text": "switching cost too high",
                "evidence_anchor": "rule:loyal_to_current_alternative (role=competitor)",
            },
            "top_proof_need": None,
            "what_moved_or_failed_to_move_them": {
                "summary": "Stayed at `skeptical` across the synthetic discussion.",
                "triggered_by_kind": "no_change",
                "evidence_anchor": "rule:loyal_to_current_alternative (role=competitor)",
            },
            "adoption_trigger": None,
            "stayed_x_because": {
                "text": "current vendor already solves this",
                "evidence_anchor": "rule:loyal_to_current_alternative (role=competitor)",
            },
            "confidence_in_this_persona": "medium",
            "bucket_routing_note": None,
        },
    ]


def _niche_signals_full() -> dict:
    return {
        "phase": "12f.1",
        "minority_objections": [
            {
                "cluster_id": "no soc2 audit certification visible",
                "representative_text": "no SOC2 audit certification visible yet",
                "raised_by_count": 2,
                "raised_by_roles": ["existing_competitor_user", "target_customer_evaluator"],
                "raised_by_persona_ids": ["p00", "p04"],
                "evidence_anchors": [
                    "rule:loyal_to_current_alternative (role=competitor)",
                    "rule:would_consider_if_proven (role=target)",
                ],
            },
        ],
        "unexpected_segments": [
            {
                "cohort_index": 2,
                "cohort_label": "legal_ops::interested_if_proven",
                "n_personas": 4,
                "bucket_distribution_pct": {"buyer": 0.0, "receptive": 75.0,
                                            "uncertain": 25.0, "skeptical": 0.0},
                "global_bucket_distribution_pct": {"buyer": 0.0, "receptive": 33.3,
                                                   "uncertain": 41.7, "skeptical": 25.0},
                "diverges_from_global_by_tvd": 0.417,
                "evidence_anchors": ["rule:would_consider_if_proven (role=target)"],
                "interpretation_hint": "Legal ops skews receptive; worth investigating.",
            },
        ],
        "edge_case_use_cases": [
            {
                "use_case": "use it for redlining only, not signatures",
                "raised_by_persona_id": "p00",
                "evidence_anchor": "rule:would_consider_if_proven (role=target)",
            },
        ],
        "one_question_for_real_customers": (
            "Has anyone ever raised this with you: no SOC2 audit "
            "certification visible yet?"
        ),
        "_caveat": "Niche signals are aggregated from the same synthetic ballots.",
    }


# -----------------------------------------------------------------------
# 1. Explainability section present in markdown
# -----------------------------------------------------------------------


def test_markdown_includes_explainability_section():
    md = render_12f1_markdown_section(
        explainability=_explainability_full(),
        persona_cards=_persona_cards_full(),
        niche_signals=_niche_signals_full(),
    )
    assert "# Phase 12F.1 — Trust, Reasoning & Niche Signals" in md
    assert "## 7. Why Assembly predicted this" in md
    assert "Should we prioritize migration tools?" in md
    assert "### Inputs used" in md
    assert "### Source audience profile" in md
    assert "### Persona composition" in md
    assert "### Evidence snapshot" in md
    assert "### Assumptions in play" in md
    assert "### Bucket explanations" in md
    assert "### Confidence" in md


# -----------------------------------------------------------------------
# 2. confidence.limited_by appears
# -----------------------------------------------------------------------


def test_markdown_renders_confidence_limited_by():
    md = render_12f1_markdown_section(
        explainability=_explainability_full(),
        persona_cards=None, niche_signals=None,
    )
    assert "Limited by:" in md
    assert "source_audience_profile_only_weakly_calibrated" in md
    assert "no_uploaded_customer_evidence" in md
    assert "Would increase if:" in md
    assert "upload 5+ real customer quotes" in md


def test_markdown_renders_confidence_score_and_level():
    md = render_12f1_markdown_section(
        explainability=_explainability_full(),
        persona_cards=None, niche_signals=None,
    )
    assert "Level: **medium**" in md
    assert "0.683" in md
    assert "cap: 0.85" in md


# -----------------------------------------------------------------------
# 3. Persona cards render when present
# -----------------------------------------------------------------------


def test_markdown_renders_persona_cards_with_anchors():
    md = render_12f1_markdown_section(
        explainability=None,
        persona_cards=_persona_cards_full(),
        niche_signals=None,
    )
    assert "## 8. Representative persona reasoning" in md
    # One sub-section per card
    assert "### Card 1 — `target_customer_evaluator`" in md
    assert "### Card 2 — `existing_competitor_user`" in md
    # Stance arrows
    assert "`curious_but_unconvinced` → `interested_if_proven`" in md
    assert "`skeptical` (no change)" in md
    # Buckets
    assert "Final bucket: **receptive**" in md
    assert "Final bucket: **skeptical**" in md
    # Anchors must appear on every sourced field
    assert "pricing seems high for small teams" in md
    assert "anchor: `rule:would_consider_if_proven (role=target)`" in md
    # Anchor must appear for "what moved" field too
    assert "Moved from `curious_but_unconvinced`" in md


# -----------------------------------------------------------------------
# 4. Niche signals render when present
# -----------------------------------------------------------------------


def test_markdown_renders_niche_signals():
    md = render_12f1_markdown_section(
        explainability=None,
        persona_cards=None,
        niche_signals=_niche_signals_full(),
    )
    assert "## 9. Niche signals worth investigating" in md
    assert "### Minority objections" in md
    assert "no SOC2 audit certification visible yet" in md
    # Cross-role attribution
    assert "across roles `existing_competitor_user`, `target_customer_evaluator`" in md
    # Unexpected segments
    assert "### Unexpected micro-segments" in md
    assert "legal_ops::interested_if_proven" in md
    assert "Δ_TVD = 0.417" in md
    # Edge-case use cases
    assert "### Edge-case use cases" in md
    assert "use it for redlining only, not signatures" in md
    # One question — ALWAYS a question (ends with `?`)
    assert "### One question for real customers" in md
    assert "no SOC2 audit certification visible yet?" in md
    # Caveat surfaced
    assert "Niche signals are aggregated from the same synthetic ballots." in md


def test_one_question_renders_as_blockquote():
    md = render_12f1_markdown_section(
        explainability=None,
        persona_cards=None,
        niche_signals=_niche_signals_full(),
    )
    # The question should appear in a `> ...` blockquote so a reader
    # can scan it without parsing the surrounding section.
    assert "> Has anyone ever raised this with you: no SOC2 audit" in md


# -----------------------------------------------------------------------
# 5. Sparse / empty data renders gracefully
# -----------------------------------------------------------------------


def test_empty_inputs_render_explicit_missing_messages():
    """Renderer must NEVER silently hide data — every absent section
    must produce a visible 'not present' marker."""
    md = render_12f1_markdown_section(
        explainability=None, persona_cards=None, niche_signals=None,
    )
    # All three section headers must still appear.
    assert "## 7. Why Assembly predicted this" in md
    assert "## 8. Representative persona reasoning" in md
    assert "## 9. Niche signals worth investigating" in md
    # And each must include an explicit "not present" message.
    assert "Explainability panel not present in this run" in md
    assert "No representative persona reasoning cards produced" in md
    assert "Niche signals panel not produced for this run" in md


def test_partially_populated_explainability_renders_missing_fields():
    """If decision_being_tested is missing but other fields are set,
    the renderer must show 'not provided' rather than omit the line."""
    partial = _explainability_full()
    partial["decision_being_tested"] = None
    partial["what_would_change_founder_mind"] = None
    md = render_12f1_markdown_section(
        explainability=partial, persona_cards=None, niche_signals=None,
    )
    assert "**Decision being tested:** _not provided_" in md
    assert "**What would change the founder's mind:** _not provided_" in md


def test_empty_minority_objections_render_threshold_explanation():
    signals = {
        "minority_objections": [],
        "unexpected_segments": [],
        "edge_case_use_cases": [],
        "one_question_for_real_customers": None,
    }
    md = render_12f1_markdown_section(
        explainability=None, persona_cards=None, niche_signals=signals,
    )
    assert "No minority objections cleared the threshold" in md
    assert "No micro-segment diverges" in md
    assert "No edge-case use cases surfaced" in md
    assert "No standout question surfaced" in md


def test_card_with_no_top_objection_renders_not_provided():
    cards = _persona_cards_full()
    cards[0]["top_objection"] = None
    cards[0]["adoption_trigger"] = None
    md = render_12f1_markdown_section(
        explainability=None, persona_cards=cards, niche_signals=None,
    )
    # The not-provided markers must appear.
    assert "Top objection: _not provided_" in md
    assert "Adoption trigger: _not provided_" in md


# -----------------------------------------------------------------------
# 6. No forbidden chain-of-thought keys appear in the markdown
# -----------------------------------------------------------------------


def test_no_chain_of_thought_keys_in_markdown_output():
    """The markdown must NOT mention any of the forbidden raw-LLM
    keys. Even if a stray key snuck into the input dict, the renderer
    only reads the structured artifacts."""
    explainability = _explainability_full()
    # Inject a "reasoning" key into the input — the renderer should
    # ignore it because it doesn't read that field.
    explainability["raw_output"] = "DO NOT RENDER ME — internal LLM trace"
    explainability["reasoning"] = "private thinking chain"
    cards = _persona_cards_full()
    cards[0]["private_reasoning"] = "private internal monologue"
    cards[0]["raw_output"] = "raw_llm_output_should_not_leak"
    md = render_12f1_markdown_section(
        explainability=explainability,
        persona_cards=cards,
        niche_signals=_niche_signals_full(),
    )
    forbidden = (
        "DO NOT RENDER ME",
        "private thinking chain",
        "private internal monologue",
        "raw_llm_output_should_not_leak",
    )
    for needle in forbidden:
        assert needle not in md, (
            f"forbidden chain-of-thought leaked into markdown: {needle!r}"
        )


# -----------------------------------------------------------------------
# 7. No apps/web changes
# -----------------------------------------------------------------------


def test_no_apps_web_changes_in_12f1_markdown_extension():
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present in checkout")
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    changes = (result.stdout or "").strip()
    if changes:
        raise AssertionError(
            f"apps/web touched during 12F.1 markdown work:\n{changes}"
        )


# -----------------------------------------------------------------------
# 8. No new alembic migration
# -----------------------------------------------------------------------


def test_no_new_db_migration_in_markdown_extension():
    versions_dir = API_ROOT / "alembic" / "versions"
    if not versions_dir.exists():
        pytest.skip("alembic/versions not present")
    for f in versions_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        # The markdown extension must not introduce any new migration.
        for needle in ("phase_12f", "explainability", "company_context"):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}"
            )


# -----------------------------------------------------------------------
# 9. No new LLM calls — module imports cleanly without provider
# -----------------------------------------------------------------------


def test_markdown_module_has_no_provider_calls():
    """Static grep: no `provider.chat(` / `structured_output(` /
    `cost_guard(` in the markdown renderer source."""
    path = API_ROOT / "src" / "assembly" / "explainability" / "markdown_render.py"
    text = path.read_text(encoding="utf-8")
    forbidden = (
        "provider.chat(",
        "provider.structured_output(",
        ".completions.create(",
        ".messages.create(",
        "with_cost_guard(",
        "import anthropic",
        "from anthropic",
        "import openai",
        "from openai",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"markdown_render.py uses forbidden LLM surface: {needle!r}"
        )


def test_markdown_render_does_not_mutate_inputs():
    """Pure-function invariant — inputs must be unchanged after render."""
    e = _explainability_full()
    c = _persona_cards_full()
    n = _niche_signals_full()
    e_copy = copy.deepcopy(e)
    c_copy = copy.deepcopy(c)
    n_copy = copy.deepcopy(n)
    render_12f1_markdown_section(
        explainability=e, persona_cards=c, niche_signals=n,
    )
    assert e == e_copy, "explainability dict was mutated"
    assert c == c_copy, "persona_cards list was mutated"
    assert n == n_copy, "niche_signals dict was mutated"


# -----------------------------------------------------------------------
# 10. Smoke: live_founder_brief.py wires the extension correctly.
# -----------------------------------------------------------------------


def test_live_founder_brief_imports_render_12f1_markdown_section():
    """Sanity check the orchestrator wiring exists. Cheap source grep
    so we don't have to instantiate the full pipeline."""
    lfb = (
        API_ROOT / "src" / "assembly" / "orchestration"
        / "live_founder_brief.py"
    )
    text = lfb.read_text(encoding="utf-8")
    assert "render_12f1_markdown_section" in text
    # And the call site appends after the legacy markdown render.
    assert (
        "md = render_intent_and_debate_report_markdown(shaped)"
        in text
    )
