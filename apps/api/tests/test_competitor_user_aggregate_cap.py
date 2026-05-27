"""Aggregate competitor_user_* cap in the compressor.

The persona-quality gate in live_quality_gates.py rejects runs whose
`competitor_user_share` exceeds 0.60. Until this fix the compressor
only enforced a PER-ROLE 35% cap, which let three competitor sub-roles
at 25% each pass compression but fail the aggregate gate downstream.

These tests cover the new aggregate cap in _apply_hard_cap_stratified.
"""

from __future__ import annotations

from assembly.sources.persona_set_compressor.compressor import (
    _apply_hard_cap_stratified,
)
from assembly.sources.persona_set_compressor.schemas import (
    CompressedPersonaCandidate,
)


def _mk(
    cid: str,
    role: str,
    quality: float = 0.8,
    provider: str = "brave_search",
    theme: str = "competitor",
) -> CompressedPersonaCandidate:
    return CompressedPersonaCandidate(
        candidate_id=cid,
        target_brief="brief_x",
        generated_for_phase="test",
        pre_normalization_role=role,
        normalized_primary_role=role,
        secondary_persona_roles=[],
        role_inference_basis=["test"],
        segment_label="segment_x",
        source_record_ids=["src_1"],
        evidence_summary="ev",
        evidence_snippets=["snippet"],
        evidence_theme=theme,
        source_provider_family=provider,
        inferred_traits=[
            {"trait_name": "t1", "trait_value": "v1"},
            {"trait_name": "t2", "trait_value": "v2"},
        ],
        inferred_preferences=[],
        inferred_objections=[],
        inferred_behaviors=[],
        hypothetical_target_product_reaction="reaction",
        confidence="medium",
        evidence_strength="moderate",
        quality_score=quality,
        caveats=[],
        simulation_usefulness_summary="ok",
        persistence_recommendation="PERSIST_IN_8_5D_2",
        kept_reason="quality",
    )


def test_aggregate_cap_blocks_three_competitor_subroles_summing_above_60_pct():
    """3 competitor sub-roles at 25% each individually pass the 35% per-role
    cap, but their sum (75%) must be blocked by the new aggregate cap."""
    candidates = (
        [_mk(f"comp_a_{i}", "competitor_user_a", 0.95) for i in range(8)]
        + [_mk(f"comp_b_{i}", "competitor_user_b", 0.90) for i in range(8)]
        + [_mk(f"comp_c_{i}", "competitor_user_c", 0.85) for i in range(8)]
        + [_mk(f"target_{i}", "target_customer_evaluator", 0.50) for i in range(10)]
        + [_mk(f"skept_{i}", "category_skeptic", 0.40) for i in range(8)]
    )
    kept, dropped, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    assert len(kept) == 24
    comp_count = sum(
        1 for c in kept
        if c.normalized_primary_role.startswith("competitor_user_")
    )
    comp_share = comp_count / 24
    # Aggregate cap is 60% of 24 = floor(14.4) = 14 → share ≤ 14/24 ≈ 0.583.
    assert comp_share <= 0.60, (
        f"competitor share {comp_share:.2f} exceeds 0.60 — aggregate "
        f"cap not enforced"
    )
    assert audit["competitor_user_total_cap"] == 14
    assert audit["competitor_user_total_used"] <= 14


def test_aggregate_cap_audit_exposes_share_and_cap():
    candidates = (
        [_mk(f"comp_{i}", "competitor_user_x", 0.9) for i in range(20)]
        + [_mk(f"other_{i}", "target_customer_evaluator", 0.5) for i in range(10)]
    )
    _, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    assert "competitor_user_total_cap" in audit
    assert "competitor_user_total_used" in audit
    assert "competitor_user_total_share_after_cap" in audit
    assert "max_competitor_user_total_share" in audit
    assert audit["max_competitor_user_total_share"] == 0.60
    assert audit["competitor_user_total_share_after_cap"] <= 0.60


def test_aggregate_cap_not_relaxed_when_underfilled():
    """If retrieval really cannot supply enough non-competitor voices, the
    compressor must underfill rather than violate the aggregate cap.
    Failing the count gate downstream is the honest signal."""
    candidates = (
        [_mk(f"comp_{i}", f"competitor_user_x{i % 3}", 0.95) for i in range(30)]
        + [_mk(f"target_{i}", "target_customer_evaluator", 0.5) for i in range(3)]
    )
    kept, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    comp_count = sum(
        1 for c in kept
        if c.normalized_primary_role.startswith("competitor_user_")
    )
    # competitor cap = 14 ⇒ kept count at most 14 + 3 non-competitor = 17.
    # Hard_max is 24 but we DON'T fill to 24 by relaxing the competitor cap.
    assert comp_count <= 14
    assert len(kept) <= 17
    # The downstream count_in_range gate (min_count=21) will catch this
    # honestly and tell the founder the retrieval pool was too narrow.


def test_aggregate_cap_passes_when_pool_is_balanced():
    """When retrieval is properly diverse, the cap shouldn't drop quality
    non-competitor candidates."""
    candidates = (
        [_mk(f"comp_{i}", f"competitor_user_x{i % 3}", 0.9) for i in range(9)]
        + [_mk(f"target_{i}", "target_customer_evaluator", 0.85) for i in range(8)]
        + [_mk(f"skept_{i}", "category_skeptic", 0.85) for i in range(8)]
        + [_mk(f"founder_{i}", "founder_persona", 0.85) for i in range(8)]
    )
    kept, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    assert len(kept) == 24
    comp_share = audit["competitor_user_total_share_after_cap"]
    assert comp_share <= 0.60
    # And we still admitted some competitors — the cap doesn't ban them.
    assert audit["competitor_user_total_used"] > 0


def test_aggregate_cap_does_not_break_when_no_competitors_in_pool():
    # Use 4+ diverse roles so the per-role 35% cap doesn't underfill.
    candidates = (
        [_mk(f"target_{i}", "target_customer_evaluator", 0.9) for i in range(10)]
        + [_mk(f"skept_{i}", "category_skeptic", 0.8) for i in range(10)]
        + [_mk(f"founder_{i}", "founder_or_operator", 0.7) for i in range(10)]
        + [_mk(f"shallow_{i}", "shallow_positive", 0.6) for i in range(10)]
    )
    kept, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    assert len(kept) == 24
    assert audit["competitor_user_total_used"] == 0
    assert audit["competitor_user_total_share_after_cap"] == 0.0


def test_audit_passes_describe_competitor_cap():
    candidates = (
        [_mk(f"comp_{i}", "competitor_user_x", 0.9) for i in range(10)]
        + [_mk(f"target_{i}", "target_customer_evaluator", 0.5) for i in range(20)]
    )
    _, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    passes_blob = " ".join(audit.get("passes", []))
    assert "competitor aggregate cap" in passes_blob
    assert "competitor_user_*" in audit["selection_rule"]


def test_custom_aggregate_cap_can_be_set_via_param():
    # Diverse non-competitor roles so the per-role 35% cap doesn't
    # confound the test of the aggregate competitor cap.
    candidates = (
        [_mk(f"comp_{i}", f"competitor_user_x{i % 3}", 0.9) for i in range(20)]
        + [_mk(f"target_{i}", "target_customer_evaluator", 0.6) for i in range(8)]
        + [_mk(f"skept_{i}", "category_skeptic", 0.55) for i in range(8)]
        + [_mk(f"founder_{i}", "founder_or_operator", 0.5) for i in range(8)]
    )
    kept_tight, _, audit_tight = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
        max_competitor_user_total_share=0.25,
    )
    # Tight cap: max(1, int(0.25 × 24)) = 6 competitors allowed.
    assert audit_tight["competitor_user_total_cap"] == 6
    assert audit_tight["competitor_user_total_used"] <= 6
    kept_loose, _, audit_loose = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
        max_competitor_user_total_share=0.80,
    )
    # Loose cap: max(1, int(0.80 × 24)) = 19.
    assert audit_loose["competitor_user_total_cap"] == 19
    assert (
        audit_loose["competitor_user_total_used"]
        > audit_tight["competitor_user_total_used"]
    )


def test_aggregate_cap_recreates_user_reported_failure_scenario():
    """Reproduces the bug from production: three competitor sub-roles at
    ~22-23% each (sum 0.67) tripping the gate. With the fix, the
    compressor pre-emptively caps the aggregate at 60% so the gate
    passes."""
    candidates = (
        # 8 upwork-like competitor users
        [_mk(f"u_{i}", "competitor_user_upwork", 0.9) for i in range(8)]
        # 8 fiverr-like competitor users
        + [_mk(f"f_{i}", "competitor_user_fiverr", 0.88) for i in range(8)]
        # 8 toptal-like competitor users
        + [_mk(f"t_{i}", "competitor_user_toptal", 0.86) for i in range(8)]
        # 10 non-competitor candidates (target customers, skeptics, etc.)
        + [_mk(f"tc_{i}", "target_customer_evaluator", 0.6) for i in range(5)]
        + [_mk(f"sk_{i}", "category_skeptic", 0.55) for i in range(3)]
        + [_mk(f"fo_{i}", "founder_or_operator", 0.5) for i in range(2)]
    )
    kept, _, audit = _apply_hard_cap_stratified(
        compressed=candidates, hard_max=24,
    )
    comp_count = sum(
        1 for c in kept
        if c.normalized_primary_role.startswith("competitor_user_")
    )
    comp_share = comp_count / len(kept) if kept else 0.0
    # The pre-fix bug had comp_share = 0.67. Post-fix must be ≤ 0.60.
    assert comp_share <= 0.60, (
        f"competitor share {comp_share:.2f} would still trip the "
        f"persona-quality gate"
    )
    # And we still filled the hard cap because non-competitor candidates
    # exist in the pool.
    assert len(kept) == 24
