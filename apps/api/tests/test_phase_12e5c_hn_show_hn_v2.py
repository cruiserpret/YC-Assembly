"""Phase 12E.5C — opt-in `hn_show_hn_v2` profile tests.

Confirms:
  1. `hn_show_hn_v2` exists as a SOURCE_PROFILES key (opt-in).
  2. Legacy `hn_show_hn` (v1) is unchanged byte-for-byte.
  3. v2 weights sum to 1.0.
  4. All 10 audience roles are present in v2.
  5. No negative weights.
  6. Profile selection: v2 is only returned when explicitly requested
     (and the augmenter respects the override path).
  7. The brief schema accepts `launch_source="hn_show_hn_v2"`.
  8. No `apps/web` changes, no DB migration, no new LLM call surface.

Pure-python: no DB, no LLM, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from assembly.schemas.founder_brief import FounderBriefIn
from assembly.sources.audience.role_taxonomy import (
    AUDIENCE_ROLES,
    SOURCE_PROFILES,
    get_profile,
    resolve_launch_source,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# Phase 12E v1 baseline — must remain byte-for-byte stable.
_HN_SHOW_HN_V1_EXPECTED: dict[str, float] = {
    "target_customer_evaluator": 0.22,
    "existing_competitor_user": 0.15,
    "proof_seeker_only": 0.12,
    "industry_observer": 0.18,
    "technical_or_legal_explainer": 0.08,
    "meta_commenter": 0.08,
    "category_skeptic": 0.06,
    "incumbent_defender": 0.05,
    "casual_bystander": 0.04,
    "off_topic_noise_candidate": 0.02,
}


# Phase 12E.5B-derived v2 — the offline-recalibrated profile.
_HN_SHOW_HN_V2_EXPECTED: dict[str, float] = {
    "target_customer_evaluator": 0.2000,
    "existing_competitor_user": 0.1875,
    "proof_seeker_only": 0.1200,
    "industry_observer": 0.0800,
    "technical_or_legal_explainer": 0.1000,
    "meta_commenter": 0.1000,
    "category_skeptic": 0.0750,
    "incumbent_defender": 0.0625,
    "casual_bystander": 0.0500,
    "off_topic_noise_candidate": 0.0250,
}


# ---------------------------------------------------------------------------
# 1. hn_show_hn_v2 exists as opt-in
# ---------------------------------------------------------------------------


def test_hn_show_hn_v2_key_present_in_source_profiles():
    assert "hn_show_hn_v2" in SOURCE_PROFILES


def test_hn_show_hn_v2_matches_calibration_value():
    """The profile shipped must match the Phase 12E.5B output. Any
    drift signals an accidental edit and should fail loudly."""
    actual = SOURCE_PROFILES["hn_show_hn_v2"]
    for role, w in _HN_SHOW_HN_V2_EXPECTED.items():
        assert actual.get(role) == pytest.approx(w, abs=1e-9), (
            f"v2 weight drift on {role}: "
            f"expected {w}, got {actual.get(role)}"
        )


# ---------------------------------------------------------------------------
# 2. v1 unchanged
# ---------------------------------------------------------------------------


def test_hn_show_hn_v1_unchanged_byte_for_byte():
    """Phase 12E.5C is additive — v1 must not have shifted by a single
    weight. Any drift would silently change the prior baseline."""
    actual = SOURCE_PROFILES["hn_show_hn"]
    for role, expected_w in _HN_SHOW_HN_V1_EXPECTED.items():
        assert actual.get(role) == pytest.approx(expected_w, abs=1e-12), (
            f"v1 weight drifted on {role}: "
            f"expected {expected_w}, got {actual.get(role)}"
        )


def test_hn_show_hn_v1_is_not_v2():
    """Sanity: v1 and v2 must be materially different profiles, else
    the recalibration was a no-op and 12E.5C is pointless."""
    v1 = SOURCE_PROFILES["hn_show_hn"]
    v2 = SOURCE_PROFILES["hn_show_hn_v2"]
    assert v1 != v2, "v1 and v2 are identical — recalibration was a no-op"
    # The diagnosed-largest delta: industry_observer dropped 10pp.
    delta_io = v2["industry_observer"] - v1["industry_observer"]
    assert delta_io == pytest.approx(-0.10, abs=1e-9), (
        f"v2 industry_observer delta vs v1 is {delta_io:+.4f}; "
        "expected -0.10 (the 12E.5B headline change)"
    )


# ---------------------------------------------------------------------------
# 3 + 4 + 5. Weight validity
# ---------------------------------------------------------------------------


def test_hn_show_hn_v2_weights_sum_to_one():
    s = sum(SOURCE_PROFILES["hn_show_hn_v2"].values())
    assert abs(s - 1.0) < 1e-9, f"v2 sums to {s}, not 1.0"


def test_hn_show_hn_v2_has_all_ten_roles():
    v2 = SOURCE_PROFILES["hn_show_hn_v2"]
    assert set(v2.keys()) == set(AUDIENCE_ROLES.keys())


def test_hn_show_hn_v2_no_negative_weights():
    for role, w in SOURCE_PROFILES["hn_show_hn_v2"].items():
        assert w >= 0, f"v2.{role} has negative weight: {w}"


def test_hn_show_hn_v2_no_weight_exceeds_one():
    for role, w in SOURCE_PROFILES["hn_show_hn_v2"].items():
        assert w <= 1.0, f"v2.{role} exceeds 1.0: {w}"


# ---------------------------------------------------------------------------
# 6. Profile selection — opt-in semantics
# ---------------------------------------------------------------------------


def test_get_profile_returns_v2_only_when_requested():
    """Explicit `hn_show_hn_v2` must return the v2 dict; explicit
    `hn_show_hn` must return the v1 dict; they must NOT alias."""
    v1 = get_profile("hn_show_hn")
    v2 = get_profile("hn_show_hn_v2")
    assert v1 == SOURCE_PROFILES["hn_show_hn"]
    assert v2 == SOURCE_PROFILES["hn_show_hn_v2"]
    assert v1 != v2


def test_default_launch_source_remains_default_not_v2():
    """Missing `launch_source` must NOT silently promote to v2."""
    resolved = resolve_launch_source(None)
    assert resolved == "default"
    assert resolved != "hn_show_hn_v2"


def test_unknown_launch_source_falls_back_to_default():
    """Resolver must not silently route unknown values to v2."""
    resolved = resolve_launch_source("hn_show_hn_v3_speculative")
    assert resolved == "default"


def test_augmenter_uses_v2_only_when_explicitly_requested():
    """The augmenter's profile lookup must respect the launch_source
    string. Requesting v1 produces v1's role mix; requesting v2
    produces v2's role mix; they must differ."""
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
        split_view_distributions,
    )
    # Build a minimal intent_drafts payload (enough to trigger the
    # synthetic injection path on both v1 and v2).
    drafts = []
    for i in range(24):
        drafts.append({
            "persona_id": f"p{i:02d}",
            "cohort_id": "test_cohort",
            "stance_label": "curious_but_unconvinced",
            "simulated_intent": (
                "loyal_to_current_alternative" if i < 6
                else "would_consider_if_proven"
            ),
            "intent_strength": "medium",
            "switching_status": "weakly_attached_to_alternative",
            "current_alternative": None,
            "conditions_to_buy": [],
            "reason_for_rejection": None,
            "proof_needed": [],
            "evidence_basis": "rule:test",
            "discussion_turn_ids": [],
            "ballot_ids": [],
            "memory_atom_ids": [],
            "confidence": "medium",
            "caveat": "test",
            "intent_signal": None,
            "intent_signal_basis": None,
        })
    meta = {
        f"p{i:02d}": {
            "segment_label": (
                "competitor_user_alt" if i < 6 else "trust_seeker"
            )
        }
        for i in range(24)
    }
    aug_v1, audit_v1 = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="hn_show_hn", run_scope_id="t",
    )
    aug_v2, audit_v2 = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="hn_show_hn_v2", run_scope_id="t",
    )
    # Both runs claim their respective launch_source in the audit.
    assert audit_v1["launch_source_used"] == "hn_show_hn"
    assert audit_v2["launch_source_used"] == "hn_show_hn_v2"
    # The augmented populations must differ.
    v1_view = split_view_distributions(aug_v1)
    v2_view = split_view_distributions(aug_v2)
    assert v1_view["source_audience_reaction"] != v2_view[
        "source_audience_reaction"
    ], "v1 and v2 produced identical source-audience distributions"


# ---------------------------------------------------------------------------
# 7. Brief schema accepts v2
# ---------------------------------------------------------------------------


def _minimal_brief_kwargs() -> dict:
    return {
        "product_name": "TestProduct",
        "product_description": "A product for testing the brief schema.",
        "price_or_price_structure": "$10",
        "launch_geography": "US",
        "target_customers": ["test users"],
        "launch_state": "unlaunched",
    }


def test_brief_accepts_hn_show_hn_v2():
    """The FounderBriefIn schema must allow `hn_show_hn_v2`."""
    b = FounderBriefIn.model_validate({
        **_minimal_brief_kwargs(),
        "launch_source": "hn_show_hn_v2",
    })
    assert b.launch_source == "hn_show_hn_v2"


def test_brief_still_accepts_hn_show_hn_v1():
    """Backwards compat: existing briefs still validate."""
    b = FounderBriefIn.model_validate({
        **_minimal_brief_kwargs(),
        "launch_source": "hn_show_hn",
    })
    assert b.launch_source == "hn_show_hn"


def test_brief_rejects_unknown_launch_source():
    """`extra="forbid"` discipline carries forward; speculative
    launch_source values must be rejected."""
    with pytest.raises(ValidationError):
        FounderBriefIn.model_validate({
            **_minimal_brief_kwargs(),
            "launch_source": "hn_show_hn_v3_speculative",
        })


# ---------------------------------------------------------------------------
# 8. Discipline
# ---------------------------------------------------------------------------


def test_no_apps_web_changes_in_phase_12e5c():
    apps_web = REPO_ROOT / "apps" / "web"
    if not apps_web.exists():
        pytest.skip("apps/web not present in this checkout")
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "apps/web"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available in this env")
    changes = (r.stdout or "").strip()
    if changes:
        raise AssertionError(
            f"apps/web touched during 12E.5C:\n{changes}"
        )


def test_no_new_alembic_migration_in_12e5c():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in (
            "phase_12e5c", "hn_show_hn_v2",
            "source_profile_recalibration",
        ):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}"
            )


def test_role_taxonomy_module_has_no_provider_calls():
    """Static grep — adding v2 must not introduce LLM/network surfaces."""
    p = (
        API_ROOT / "src" / "assembly" / "sources" / "audience"
        / "role_taxonomy.py"
    )
    text = p.read_text(encoding="utf-8")
    for needle in (
        "provider.chat(", "provider.structured_output(",
        ".messages.create(", "with_cost_guard(",
        "import anthropic", "import openai",
        "import httpx", "import requests",
    ):
        assert needle not in text
