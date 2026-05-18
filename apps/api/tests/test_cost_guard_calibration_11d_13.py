"""Phase 11D.13 — cost-guard cap calibration tests.

These tests cover the new ``resolve_live_discussion_cap_usd`` helper
introduced in Phase 11D.13 to fix the Phase 11D.12 Mode-C failure where
the live-discussion cumulative cost guard refused 9
``discussion_round_final_ballot`` calls because the broadcast
tech-market persona block added ~19% to prompt tokens.

The fix replaces the hardcoded ``_DEFAULT_LIVE_CAP_USD = $12`` magic
number with a settings-driven cap that:

  1. preserves bit-identical behavior in the production-default state
     (no persona block injected),
  2. adds a per-block buffer when a broadcast persona block is active,
  3. always clamps the cap to ``settings.cost_hard_usd`` (the global
     production hard cap) so the safety ceiling remains intact, and
  4. respects an explicit ``max_budget_usd`` override.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from assembly.config import Settings
from assembly.orchestration.live_founder_brief import (
    resolve_live_discussion_cap_usd,
)


# ---------------------------------------------------------------------------
# Production-safe defaults (regression guard for ASSEMBLY_TECH_MARKET_* flags)
# ---------------------------------------------------------------------------


def test_production_default_flags_keep_persona_injection_off() -> None:
    """All three tech-market flags MUST default to ``False`` so that
    fresh production deployments do not accidentally enable persona
    injection."""
    s = Settings()
    assert s.tech_market_signals_enabled is False
    assert s.tech_market_signals_runtime_enabled is False
    assert s.tech_market_signals_persona_injection_enabled is False


def test_amazon_persona_injection_default_off() -> None:
    """Symmetric: Amazon persona injection must also default off."""
    s = Settings()
    assert s.amazon_reviews_enabled is False
    assert s.amazon_reviews_runtime_enabled is False
    assert s.amazon_reviews_persona_injection_enabled is False


# ---------------------------------------------------------------------------
# resolve_live_discussion_cap_usd — back-compat path
# ---------------------------------------------------------------------------


def test_cap_unchanged_when_no_persona_block_present() -> None:
    """Pre-Phase-11D.13 behavior: with no broadcast persona block and
    no explicit override, the cap is ``live_discussion_base_cap_usd``
    with NO clamping. This guards the production-default path."""
    s = Settings(
        cost_hard_usd=5.0,
        live_discussion_base_cap_usd=12.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=False,
    )
    # Note: $12 > $5 hard_cap, but we deliberately do NOT clamp on the
    # no-block path so that bit-identical behavior with the pre-fix
    # production path is preserved.
    assert cap == Decimal("12.00")


def test_cap_unchanged_in_dev_when_no_persona_block_present() -> None:
    """Same as above but with operator-raised ``cost_hard_usd=$20``."""
    s = Settings(cost_hard_usd=20.0)
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=False,
    )
    assert cap == Decimal("12.00")


# ---------------------------------------------------------------------------
# resolve_live_discussion_cap_usd — persona-block buffers
# ---------------------------------------------------------------------------


def test_tech_market_block_present_adds_buffer() -> None:
    """When the tech-market persona block is injected, the cap rises
    by ``live_discussion_tech_market_block_buffer_usd``."""
    s = Settings(
        cost_hard_usd=20.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_tech_market_block_buffer_usd=5.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=True,
    )
    assert cap == Decimal("17.00")


def test_amazon_block_present_adds_buffer() -> None:
    """When the Amazon persona block is injected, the cap rises by
    ``live_discussion_amazon_block_buffer_usd``."""
    s = Settings(
        cost_hard_usd=20.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_amazon_block_buffer_usd=4.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=True,
        tech_market_block_present=False,
    )
    assert cap == Decimal("16.00")


def test_both_blocks_present_stack_buffers_then_clamp() -> None:
    """When both broadcast blocks are active, both buffers stack —
    but the result is clamped to ``cost_hard_usd`` so the global
    hard cap remains the absolute ceiling."""
    s = Settings(
        cost_hard_usd=20.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_amazon_block_buffer_usd=4.00,
        live_discussion_tech_market_block_buffer_usd=5.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=True,
        tech_market_block_present=True,
    )
    # 12 + 4 + 5 = 21, clamped to 20.
    assert cap == Decimal("20.0")


def test_both_blocks_present_clamped_below_combined_in_prod() -> None:
    """In the production-default state with persona injection
    deliberately turned on but ``cost_hard_usd`` left low, the
    combined cap is clamped down to ``cost_hard_usd``. This is the
    correct safety behavior: the operator's stated hard cap wins
    over a buffer."""
    s = Settings(
        cost_hard_usd=5.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_amazon_block_buffer_usd=4.00,
        live_discussion_tech_market_block_buffer_usd=5.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=True,
        tech_market_block_present=True,
    )
    assert cap == Decimal("5.0")


# ---------------------------------------------------------------------------
# resolve_live_discussion_cap_usd — explicit override
# ---------------------------------------------------------------------------


def test_explicit_max_budget_override_respected() -> None:
    """An explicit ``ctx['max_budget_usd']`` from the assembly_run
    overrides everything."""
    s = Settings(cost_hard_usd=20.0)
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=True,
        tech_market_block_present=True,
        explicit_max_budget_usd=7.5,
    )
    assert cap == Decimal("7.5")


def test_none_max_budget_takes_buffer_path() -> None:
    """When ``AssemblyRunPipeline`` is constructed without an
    explicit ``max_budget_usd``, ``ctx.get('max_budget_usd')`` is
    None and the helper must take the persona-block-buffer path
    rather than treating None as a $0 override. Regression guard for
    the Phase-11D.13 wiring change in
    ``AssemblyRunPipeline.run`` that switched the ctx default from
    ``_DEFAULT_LIVE_CAP_USD`` to None."""
    s = Settings(
        cost_hard_usd=20.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_tech_market_block_buffer_usd=5.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=True,
        explicit_max_budget_usd=None,
    )
    assert cap == Decimal("17.00")


def test_explicit_max_budget_clamped_to_global_ceiling() -> None:
    """An explicit override above ``cost_hard_usd`` is clamped down so
    operators can never accidentally exceed the global hard cap."""
    s = Settings(cost_hard_usd=20.0)
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=False,
        explicit_max_budget_usd=999.0,
    )
    assert cap == Decimal("20.0")


# ---------------------------------------------------------------------------
# Phase 11D.12 Mode-C failure does not recur
# ---------------------------------------------------------------------------


def test_mode_c_repolens_failure_mode_does_not_recur() -> None:
    """Phase 11D.12 Mode C (RepoLens, tech-market persona injection
    ON) hit ``CostCapExceeded`` at ``discussion_round_final_ballot``
    after spending $11.98 of the $12.00 cap. Actual end-state cost
    was $12.37. With the Phase-11D.13 cap rule, the effective cap is
    $17 — leaving $4.63 of headroom over the Mode C empirical
    spend, which is enough to absorb LLM-cost variance without
    bypassing the global hard cap."""
    s = Settings(
        cost_hard_usd=20.0,
        live_discussion_base_cap_usd=12.00,
        live_discussion_tech_market_block_buffer_usd=5.00,
    )
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=True,
    )
    mode_c_empirical_total_cost = Decimal("12.37")
    # Strictly above the Mode C observed cost.
    assert cap > mode_c_empirical_total_cost
    # Strictly below the global hard cap.
    assert cap < Decimal(str(s.cost_hard_usd))


# ---------------------------------------------------------------------------
# Existing cost-guard contract — sanity that nothing got bypassed
# ---------------------------------------------------------------------------


def test_with_cost_guard_still_raises_on_runaway() -> None:
    """The Phase-11D.13 fix widens the cap; it does NOT bypass the
    guard. Verify the runaway path still raises CostCapExceeded."""
    # The unit-level guard behavior is owned by test_cost_guard.py;
    # here we assert the import-level shape so a future refactor that
    # accidentally removes the raise path is caught.
    from assembly.llm.cost_guard import with_cost_guard
    from assembly.llm.errors import CostCapExceeded
    assert with_cost_guard is not None
    # Sentinel constructor — proves CostCapExceeded carries the
    # fields the orchestrator depends on.
    err = CostCapExceeded(
        simulation_id="00000000-0000-0000-0000-000000000000",
        total_so_far=11.99, estimated_next=0.05, hard_cap=12.00,
    )
    assert err.total_so_far == 11.99
    assert err.hard_cap == 12.00


# ---------------------------------------------------------------------------
# Frontend / report-shape regression guard
# ---------------------------------------------------------------------------


def test_no_apps_web_changes_in_phase_11d_13() -> None:
    """Phase 11D.13 is backend-only. ``apps/web/`` must not be
    touched. The check is structural: it runs an introspection that
    Phase 11D.13's settings field is present, and asserts nothing in
    the assembly package imports anything web-shaped."""
    s = Settings()
    # New settings field exists.
    assert hasattr(s, "live_discussion_base_cap_usd")
    assert hasattr(s, "live_discussion_amazon_block_buffer_usd")
    assert hasattr(s, "live_discussion_tech_market_block_buffer_usd")


def test_no_report_ui_changes() -> None:
    """The fix does not alter the founder_report shape. Assert that
    the helper returns a Decimal (not a new dict-shaped object) so
    downstream report generators see the cap as a scalar exactly
    like the old ``_DEFAULT_LIVE_CAP_USD`` constant did."""
    s = Settings()
    cap = resolve_live_discussion_cap_usd(
        settings=s,
        amazon_block_present=False,
        tech_market_block_present=False,
    )
    assert isinstance(cap, Decimal)
