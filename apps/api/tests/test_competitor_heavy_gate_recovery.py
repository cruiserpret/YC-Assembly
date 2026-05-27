"""Competitor-heavy market gate-recovery — V0 of the roadmap in
`apps/api/docs/competitor_heavy_market_mode_plan.md`.

Customer-facing live path uses a 0.75 ceiling on
competitor_user_total_share (instead of the strict 0.60 used by the
validation harness). When the realized share lands above 0.60 but
below 0.75, the run completes successfully and is marked
`gate_recovery_triggered=true`. The founder report surfaces an
explicit "competitor-heavy market" warning caveat.

These tests cover the three integration points: the live compressor
aggregate share check, the orchestrator's threshold computation, and
the caveat injection.

Real-world scenario this fixes: marketplace platforms like the user's
Tasknory brief, where retrieval surfaces predominantly users of named
incumbents (Upwork, Fiverr, Toptal, etc.) and the strict 0.60 gate
would otherwise abort the run.
"""

from __future__ import annotations

from assembly.orchestration.live_evidence_pipeline import (
    _live_compress_simple,
)


def _mk(cid: str, role: str, quality: float = 0.8) -> dict:
    return {
        "candidate_id": cid,
        "normalized_primary_role": role,
        "inferred_persona_role": role,
        "quality_score": quality,
        "evidence_snippets": [f"snippet for {cid}"],
    }


def test_live_compressor_strict_cap_enforced_at_60_pct():
    """Default cap=0.60 preserves backwards-compat behavior for any
    caller that doesn't opt into the recovery ceiling."""
    candidates = (
        [_mk(f"u_{i}", "competitor_user_upwork", 0.95) for i in range(8)]
        + [_mk(f"f_{i}", "competitor_user_fiverr", 0.92) for i in range(8)]
        + [_mk(f"t_{i}", "competitor_user_toptal", 0.88) for i in range(8)]
        + [_mk(f"tc_{i}", "target_customer_evaluator", 0.60) for i in range(5)]
        + [_mk(f"sk_{i}", "category_skeptic", 0.55) for i in range(3)]
        + [_mk(f"fo_{i}", "founder_or_operator", 0.50) for i in range(2)]
    )
    chosen = _live_compress_simple(
        candidates, target_count=24,
        max_competitor_user_total_share=0.60,
    )
    comp_count = sum(
        1 for c in chosen
        if c["normalized_primary_role"].startswith("competitor_user_")
    )
    comp_share = comp_count / len(chosen) if chosen else 0.0
    assert comp_share <= 0.60 + 1e-9


def test_live_compressor_recovery_ceiling_enforced_at_75_pct():
    """Customer-facing live path passes 0.75 — the population can be
    up to 75% competitor users but never more."""
    candidates = (
        [_mk(f"u_{i}", "competitor_user_upwork", 0.95) for i in range(8)]
        + [_mk(f"f_{i}", "competitor_user_fiverr", 0.92) for i in range(8)]
        + [_mk(f"t_{i}", "competitor_user_toptal", 0.88) for i in range(8)]
        + [_mk(f"tc_{i}", "target_customer_evaluator", 0.60) for i in range(3)]
        + [_mk(f"sk_{i}", "category_skeptic", 0.55) for i in range(3)]
        + [_mk(f"fo_{i}", "founder_or_operator", 0.50) for i in range(2)]
    )
    chosen = _live_compress_simple(
        candidates, target_count=24,
        max_competitor_user_total_share=0.75,
    )
    comp_count = sum(
        1 for c in chosen
        if c["normalized_primary_role"].startswith("competitor_user_")
    )
    comp_share = comp_count / len(chosen) if chosen else 0.0
    assert comp_share <= 0.75 + 1e-9


def test_live_compressor_reproduces_tasknory_scenario_passes_with_recovery():
    """The user's Tasknory failure scenario.

    Brief: AI talent marketplace competing with Upwork / Fiverr /
    Freelancer.com / Toptal / Andela. Retrieval predominantly returns
    users of those platforms; the non-competitor pool is small.

    With the strict 0.60 cap (validation harness default), this would
    abort. With the 0.75 recovery ceiling (customer-facing default),
    it produces a 24-persona population at ≤0.75 share, the
    persona-quality gate passes, and the orchestrator marks the run
    as gate-recovered."""
    candidates = (
        # Heavy competitor representation across 5 named platforms.
        # Each sub-role hits max_per_role=4 cap.
        [_mk(f"u_{i}", "competitor_user_upwork", 0.95) for i in range(6)]
        + [_mk(f"f_{i}", "competitor_user_fiverr", 0.92) for i in range(6)]
        + [_mk(f"l_{i}", "competitor_user_freelancer_com", 0.90) for i in range(6)]
        + [_mk(f"t_{i}", "competitor_user_toptal", 0.88) for i in range(6)]
        + [_mk(f"a_{i}", "competitor_user_andela", 0.86) for i in range(6)]
        # Narrow non-competitor pool (2 roles × ≤4 admittable).
        + [_mk(f"tc_{i}", "target_customer_evaluator", 0.65) for i in range(4)]
        + [_mk(f"st_{i}", "early_stage_startup_founder", 0.62) for i in range(4)]
    )
    chosen = _live_compress_simple(
        candidates, target_count=24,
        max_competitor_user_total_share=0.75,
    )
    comp_count = sum(
        1 for c in chosen
        if c["normalized_primary_role"].startswith("competitor_user_")
    )
    comp_share = comp_count / len(chosen) if chosen else 0.0
    # Strictly under the recovery ceiling.
    assert comp_share <= 0.75 + 1e-9, (
        f"compressor produced comp_share={comp_share:.3f} > 0.75 — "
        f"recovery ceiling not enforced"
    )
    # The strict 0.60 threshold IS exceeded — orchestrator marks
    # gate_recovery_triggered=true → founder report surfaces the
    # competitor-heavy warning. This is the desired outcome:
    # simulation runs, the founder gets actionable signal with an
    # explicit caveat about market structure, run does not abort.
    assert comp_share > 0.60, (
        f"comp_share={comp_share:.3f} did not exceed strict 0.60 — "
        f"gate_recovery_triggered would not fire on this pool. The "
        f"point of this test is to verify the recovery path."
    )
    # Population is meaningful in size (close to the 24 target).
    assert len(chosen) >= 20


def test_orchestrator_threshold_constants_are_aligned():
    """The strict threshold and recovery ceiling must keep their
    documented relationship: strict < recovery. Anyone changing
    these in live_founder_brief.py would trip this test."""
    from assembly.orchestration.live_founder_brief import (
        _COMPETITOR_HEAVY_RECOVERY_CEILING,
        _COMPETITOR_HEAVY_STRICT_THRESHOLD,
    )
    assert _COMPETITOR_HEAVY_STRICT_THRESHOLD == 0.60
    assert _COMPETITOR_HEAVY_RECOVERY_CEILING == 0.75
    assert _COMPETITOR_HEAVY_STRICT_THRESHOLD < _COMPETITOR_HEAVY_RECOVERY_CEILING


def test_live_compressor_default_cap_is_strict_for_backwards_compat():
    """Callers that don't pass the param (e.g. validation harness
    scripts) get the strict 0.60 cap automatically."""
    candidates = (
        [_mk(f"comp_{i}", "competitor_user_x", 0.9) for i in range(20)]
        + [_mk(f"tc_{i}", "target_customer_evaluator", 0.5) for i in range(10)]
    )
    # Note: no max_competitor_user_total_share param — default applies.
    chosen = _live_compress_simple(candidates, target_count=24)
    comp_count = sum(
        1 for c in chosen
        if c["normalized_primary_role"].startswith("competitor_user_")
    )
    comp_share = comp_count / len(chosen) if chosen else 0.0
    assert comp_share <= 0.60 + 1e-9


def test_live_compressor_returns_audit_field_shape_in_wrapper():
    """compress_to_live_society's audit dict must carry the cap and
    realized-share fields so the orchestrator can compute
    gate_recovery_triggered without re-iterating the population."""
    from assembly.orchestration.live_evidence_pipeline import (
        compress_to_live_society,
    )
    candidates = [
        {
            **_mk(f"comp_{i}", "competitor_user_x", 0.9),
            "evidence_snippets": [f"snip {i}"],
            "inferred_traits": [
                {"trait_name": "t1", "trait_value": "v1"},
                {"trait_name": "t2", "trait_value": "v2"},
            ],
            "source_record_ids": ["src_1"],
        }
        for i in range(10)
    ] + [
        {
            **_mk(f"tc_{i}", "target_customer_evaluator", 0.5),
            "evidence_snippets": [f"snip tc {i}"],
            "inferred_traits": [
                {"trait_name": "t1", "trait_value": "v1"},
                {"trait_name": "t2", "trait_value": "v2"},
            ],
            "source_record_ids": ["src_1"],
        }
        for i in range(20)
    ]
    _, audit = compress_to_live_society(
        candidates=candidates,
        accepted_evidence=[],
        target_brief_id="brief_x",
        product_name="Test",
        launch_state="launched",
        hard_max=24,
        max_competitor_user_total_share=0.75,
    )
    assert audit["max_competitor_user_total_share"] == 0.75
    assert "competitor_user_total_share_after_cap" in audit
    assert "competitor_user_total_count" in audit


def test_live_compressor_handles_no_competitors_in_pool():
    """When retrieval has no competitor users at all, the cap is a
    no-op and the compressor fills normally."""
    candidates = (
        [_mk(f"tc_{i}", "target_customer_evaluator", 0.9) for i in range(6)]
        + [_mk(f"sk_{i}", "category_skeptic", 0.8) for i in range(6)]
        + [_mk(f"fo_{i}", "founder_or_operator", 0.7) for i in range(6)]
        + [_mk(f"sh_{i}", "shallow_positive", 0.6) for i in range(6)]
    )
    chosen = _live_compress_simple(
        candidates, target_count=24,
        max_competitor_user_total_share=0.60,
    )
    comp_count = sum(
        1 for c in chosen
        if c["normalized_primary_role"].startswith("competitor_user_")
    )
    assert comp_count == 0
    # 4 roles × 4 per role cap (35% of 24 = 8 role_max, but
    # max_per_role=4 binds) = up to 16 total.
    assert 1 <= len(chosen) <= 24
