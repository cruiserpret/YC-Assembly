"""Phase 12E.5O — opt-in `product_hunt_v1` profile tests.

Confirms:
  1. `product_hunt_v1` exists as a SOURCE_PROFILES key (opt-in).
  2. `product_hunt` alias resolves to `product_hunt_v1`.
  3. Legacy profiles (default, hn_show_hn, hn_show_hn_v2) are NOT
     mutated by the addition of v5O roles — byte-for-byte stable.
  4. product_hunt_v1 weights sum to 1.0.
  5. All AudienceRoles (including the 3 new ones) are present in
     every profile (even at 0.0 in legacy profiles).
  6. No negative weights anywhere.
  7. Three new AudienceRole types exist with proper specs.
  8. Profile selection: product_hunt_v1 only used when requested.
  9. The brief schema accepts `launch_source="product_hunt_v1"` and
     the friendly alias `"product_hunt"`.
 10. No `apps/web` changes, no DB migration, no new LLM call surface.

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
    AudienceRoleSpec,
    get_profile,
    get_role_spec,
    is_hard_resistant_role,
    is_scorable_role,
    resolve_launch_source,
    role_locked_default_bucket,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]


# Expected weights — must match the operator-approved
# product_hunt_v1 spec byte-for-byte. Any drift signals an accidental
# edit and should fail loudly.
_PRODUCT_HUNT_V1_EXPECTED: dict[str, float] = {
    "target_customer_evaluator": 0.2200,
    "existing_competitor_user": 0.1100,
    "proof_seeker_only": 0.0700,
    "industry_observer": 0.0500,
    "technical_or_legal_explainer": 0.0400,
    "meta_commenter": 0.1200,
    "category_skeptic": 0.0300,
    "incumbent_defender": 0.0250,
    "casual_bystander": 0.0900,
    "off_topic_noise_candidate": 0.0450,
    "shallow_positive_commenter": 0.0900,
    "founder_network_supporter": 0.0700,
    "early_adopter": 0.0400,
}


# ---------------------------------------------------------------------------
# 1. product_hunt_v1 exists as opt-in profile
# ---------------------------------------------------------------------------


def test_product_hunt_v1_key_present_in_source_profiles():
    assert "product_hunt_v1" in SOURCE_PROFILES


def test_product_hunt_v1_matches_spec():
    actual = SOURCE_PROFILES["product_hunt_v1"]
    for role, expected_w in _PRODUCT_HUNT_V1_EXPECTED.items():
        assert actual.get(role) == pytest.approx(expected_w, abs=1e-9), (
            f"product_hunt_v1 weight drift on {role}: "
            f"expected {expected_w}, got {actual.get(role)}"
        )


def test_product_hunt_v1_weights_sum_to_one():
    s = sum(SOURCE_PROFILES["product_hunt_v1"].values())
    assert abs(s - 1.0) < 1e-9, f"product_hunt_v1 sums to {s}, not 1.0"


def test_product_hunt_v1_no_negative_weights():
    for role, w in SOURCE_PROFILES["product_hunt_v1"].items():
        assert w >= 0, f"product_hunt_v1.{role} has negative weight: {w}"


def test_product_hunt_v1_no_weight_exceeds_one():
    for role, w in SOURCE_PROFILES["product_hunt_v1"].items():
        assert w <= 1.0, f"product_hunt_v1.{role} exceeds 1.0: {w}"


# ---------------------------------------------------------------------------
# 2. `product_hunt` alias resolves correctly
# ---------------------------------------------------------------------------


def test_product_hunt_alias_resolves_to_v1():
    assert resolve_launch_source("product_hunt") == "product_hunt_v1"


def test_product_hunt_alias_returns_v1_profile():
    via_alias = get_profile("product_hunt")
    direct = get_profile("product_hunt_v1")
    assert via_alias == direct
    assert via_alias == SOURCE_PROFILES["product_hunt_v1"]


def test_product_hunt_alias_case_insensitive():
    """Operator hands in `Product_Hunt` or `PRODUCT_HUNT` — resolver
    normalizes via lowercase."""
    for v in ("Product_Hunt", "PRODUCT_HUNT", " product_hunt "):
        assert resolve_launch_source(v) == "product_hunt_v1"


# ---------------------------------------------------------------------------
# 3. Legacy profiles are byte-for-byte stable
# ---------------------------------------------------------------------------


_HN_V1_BASELINE: dict[str, float] = {
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


_HN_V2_BASELINE: dict[str, float] = {
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


def test_hn_show_hn_v1_unchanged_byte_for_byte_after_12e5o():
    """Phase 12E.5O is additive — v1 must not have shifted by a single
    weight on the 10 original roles."""
    actual = SOURCE_PROFILES["hn_show_hn"]
    for role, expected_w in _HN_V1_BASELINE.items():
        assert actual.get(role) == pytest.approx(expected_w, abs=1e-12), (
            f"v1 weight drifted on {role}: "
            f"expected {expected_w}, got {actual.get(role)}"
        )


def test_hn_show_hn_v2_unchanged_byte_for_byte_after_12e5o():
    """Phase 12E.5O must not silently re-tune v2's 12E.5B-calibrated
    weights."""
    actual = SOURCE_PROFILES["hn_show_hn_v2"]
    for role, expected_w in _HN_V2_BASELINE.items():
        assert actual.get(role) == pytest.approx(expected_w, abs=1e-9), (
            f"v2 weight drifted on {role}: "
            f"expected {expected_w}, got {actual.get(role)}"
        )


def test_hn_show_hn_v1_has_new_roles_at_zero():
    v1 = SOURCE_PROFILES["hn_show_hn"]
    for new_role in (
        "shallow_positive_commenter",
        "founder_network_supporter",
        "early_adopter",
    ):
        assert v1.get(new_role) == 0.0, (
            f"v1 must keep new role {new_role!r} at 0.0; got {v1.get(new_role)}"
        )


def test_hn_show_hn_v2_has_new_roles_at_zero():
    v2 = SOURCE_PROFILES["hn_show_hn_v2"]
    for new_role in (
        "shallow_positive_commenter",
        "founder_network_supporter",
        "early_adopter",
    ):
        assert v2.get(new_role) == 0.0, (
            f"v2 must keep new role {new_role!r} at 0.0; got {v2.get(new_role)}"
        )


def test_default_profile_has_new_roles_at_zero():
    d = SOURCE_PROFILES["default"]
    for new_role in (
        "shallow_positive_commenter",
        "founder_network_supporter",
        "early_adopter",
    ):
        assert d.get(new_role) == 0.0


def test_every_profile_sums_to_one_after_12e5o():
    for src, profile in SOURCE_PROFILES.items():
        s = sum(profile.values())
        assert abs(s - 1.0) < 1e-9, (
            f"profile {src!r} no longer sums to 1.0: {s}"
        )


def test_every_profile_has_all_thirteen_roles():
    """All four profiles must declare a weight (possibly 0.0) for every
    AudienceRole. This is what prevents silent breakage of the
    augmenter's largest-remainder allocation logic when new roles ship.
    """
    expected_roles = set(AUDIENCE_ROLES.keys())
    for src, profile in SOURCE_PROFILES.items():
        assert set(profile.keys()) == expected_roles, (
            f"profile {src!r} has role-keys mismatch: "
            f"missing {expected_roles - set(profile.keys())}, "
            f"extra {set(profile.keys()) - expected_roles}"
        )


# ---------------------------------------------------------------------------
# 4. Three new role types: specs are valid
# ---------------------------------------------------------------------------


def test_new_role_types_are_in_audience_roles():
    for role in (
        "shallow_positive_commenter",
        "founder_network_supporter",
        "early_adopter",
    ):
        assert role in AUDIENCE_ROLES, (
            f"new role {role!r} missing from AUDIENCE_ROLES"
        )
        spec = AUDIENCE_ROLES[role]
        assert isinstance(spec, AudienceRoleSpec)
        assert spec.role == role


def test_shallow_positive_commenter_locked_to_receptive():
    spec = get_role_spec("shallow_positive_commenter")
    assert spec is not None
    assert spec.default_bucket == "receptive"
    assert spec.allowed_buckets == frozenset({"receptive"})
    assert spec.is_scorable is True
    assert spec.is_hard_resistant is False
    assert role_locked_default_bucket("shallow_positive_commenter") == "receptive"


def test_founder_network_supporter_is_noise():
    spec = get_role_spec("founder_network_supporter")
    assert spec is not None
    assert spec.is_scorable is False  # noise — excluded from scorable
    assert spec.default_bucket == "uncertain"
    assert spec.allowed_buckets == frozenset({"uncertain"})
    assert spec.is_hard_resistant is False


def test_early_adopter_is_bucket_flexible():
    spec = get_role_spec("early_adopter")
    assert spec is not None
    assert spec.is_scorable is True
    assert spec.default_bucket == "receptive"
    assert spec.allowed_buckets == frozenset(
        {"buyer", "receptive", "uncertain"}
    )
    # bucket-flexible — NOT locked
    assert role_locked_default_bucket("early_adopter") is None


def test_new_roles_scorability_helpers():
    assert is_scorable_role("shallow_positive_commenter") is True
    assert is_scorable_role("founder_network_supporter") is False
    assert is_scorable_role("early_adopter") is True
    assert is_hard_resistant_role("shallow_positive_commenter") is False
    assert is_hard_resistant_role("founder_network_supporter") is False
    assert is_hard_resistant_role("early_adopter") is False


# ---------------------------------------------------------------------------
# 5. Profile selection — opt-in semantics
# ---------------------------------------------------------------------------


def test_get_profile_returns_product_hunt_v1_only_when_requested():
    v2 = get_profile("hn_show_hn_v2")
    ph = get_profile("product_hunt_v1")
    assert ph == SOURCE_PROFILES["product_hunt_v1"]
    assert ph != v2


def test_default_launch_source_does_not_promote_to_product_hunt_v1():
    """Missing `launch_source` must NOT silently route to PH."""
    resolved = resolve_launch_source(None)
    assert resolved == "default"
    assert resolved != "product_hunt_v1"


def test_unknown_launch_source_falls_back_to_default():
    resolved = resolve_launch_source("product_hunt_v2_speculative")
    assert resolved == "default"
    resolved2 = resolve_launch_source("indie_hackers")
    assert resolved2 == "default"


def test_product_hunt_v1_does_not_mutate_hn_show_hn_v2_profile():
    """Sanity: requesting PH must NOT alter the v2 profile dict in
    memory (no shared references, no copy-on-read mutation)."""
    before = dict(SOURCE_PROFILES["hn_show_hn_v2"])
    _ = get_profile("product_hunt_v1")
    after = dict(SOURCE_PROFILES["hn_show_hn_v2"])
    assert before == after


def test_product_hunt_v1_dict_distinct_from_hn_show_hn_v2():
    """If the two profiles share a reference, that's a wiring bug."""
    ph = SOURCE_PROFILES["product_hunt_v1"]
    v2 = SOURCE_PROFILES["hn_show_hn_v2"]
    assert ph is not v2
    assert ph != v2


# ---------------------------------------------------------------------------
# 6. Augmenter respects product_hunt_v1
# ---------------------------------------------------------------------------


def test_augmenter_uses_product_hunt_v1_when_requested():
    """The augmenter's profile lookup must respect launch_source.
    Requesting product_hunt_v1 must produce a different
    source-audience distribution than hn_show_hn_v2."""
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
        split_view_distributions,
    )
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
    aug_v2, audit_v2 = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="hn_show_hn_v2", run_scope_id="t",
    )
    aug_ph, audit_ph = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="product_hunt_v1", run_scope_id="t",
    )
    assert audit_v2["launch_source_used"] == "hn_show_hn_v2"
    assert audit_ph["launch_source_used"] == "product_hunt_v1"
    v2_view = split_view_distributions(aug_v2)
    ph_view = split_view_distributions(aug_ph)
    assert v2_view["source_audience_reaction"] != ph_view[
        "source_audience_reaction"
    ], "v2 and product_hunt_v1 produced identical source-audience distributions"


def test_augmenter_alias_product_hunt_routes_to_v1():
    """Brief says `launch_source="product_hunt"`; the augmenter must
    record the canonical key `product_hunt_v1` in the audit."""
    from assembly.sources.audience.augmenter import (
        augment_intent_drafts_with_source_audience,
    )
    drafts = []
    for i in range(12):
        drafts.append({
            "persona_id": f"p{i:02d}",
            "cohort_id": "test_cohort",
            "stance_label": "curious_but_unconvinced",
            "simulated_intent": "would_consider_if_proven",
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
        f"p{i:02d}": {"segment_label": "trust_seeker"} for i in range(12)
    }
    _, audit = augment_intent_drafts_with_source_audience(
        intent_drafts=drafts, persona_metadata_by_pid=meta,
        launch_source="product_hunt", run_scope_id="t",
    )
    assert audit["launch_source_used"] == "product_hunt_v1"


# ---------------------------------------------------------------------------
# 7. Brief schema accepts product_hunt_v1 + product_hunt
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


def test_brief_accepts_product_hunt_v1():
    b = FounderBriefIn.model_validate({
        **_minimal_brief_kwargs(),
        "launch_source": "product_hunt_v1",
    })
    assert b.launch_source == "product_hunt_v1"


def test_brief_accepts_product_hunt_alias():
    b = FounderBriefIn.model_validate({
        **_minimal_brief_kwargs(),
        "launch_source": "product_hunt",
    })
    assert b.launch_source == "product_hunt"


def test_brief_still_accepts_hn_show_hn_v2():
    b = FounderBriefIn.model_validate({
        **_minimal_brief_kwargs(),
        "launch_source": "hn_show_hn_v2",
    })
    assert b.launch_source == "hn_show_hn_v2"


def test_brief_rejects_unknown_launch_source_after_12e5o():
    with pytest.raises(ValidationError):
        FounderBriefIn.model_validate({
            **_minimal_brief_kwargs(),
            "launch_source": "indie_hackers",
        })


# ---------------------------------------------------------------------------
# 8. Discipline — no DB / web / model-routing / cache changes
# ---------------------------------------------------------------------------


def test_no_apps_web_changes_in_phase_12e5o():
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
            f"apps/web touched during 12E.5O:\n{changes}"
        )


def test_no_new_alembic_migration_in_12e5o():
    versions = API_ROOT / "alembic" / "versions"
    if not versions.exists():
        pytest.skip("alembic/versions not present")
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in (
            "phase_12e5o", "product_hunt_v1", "product_hunt_alias",
        ):
            assert needle not in text, (
                f"unexpected migration {f.name} mentions {needle!r}"
            )


def test_role_taxonomy_module_has_no_provider_calls_after_12e5o():
    """Static grep — adding PH must not introduce LLM/network surfaces."""
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


def test_no_model_routing_changes_in_role_taxonomy():
    """The role-taxonomy module must not reference model selection.
    Routing is the orchestrator's job; this layer is pure-data."""
    p = (
        API_ROOT / "src" / "assembly" / "sources" / "audience"
        / "role_taxonomy.py"
    )
    text = p.read_text(encoding="utf-8")
    for needle in (
        "ASSEMBLY_LLM_", "claude-", "gpt-", "model=",
        "prompt_cache", "prompt-cache",
        "intent_signal", "intent-signal",
        "devtools_b2b",
    ):
        assert needle not in text.lower(), (
            f"role_taxonomy must not reference {needle!r}"
        )
