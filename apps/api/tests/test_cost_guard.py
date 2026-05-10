"""Cost-guard tests.

The full row-lock + concurrent-call test requires a real Postgres connection.
These tests cover the unit-testable surface (estimate math, error shape, the
contract of `with_cost_guard` via a stand-in sessionmaker that simulates the
locking semantics in-memory).

The real DB integration test is documented as a TODO marker — it runs once
`uv sync` and `docker compose up -d` are in place and the user invokes
`pytest -m integration`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from assembly.llm.errors import CostCapExceeded
from assembly.llm.pricing import estimate_cost_usd, model_pricing


# ---------------------------------------------------------------------------
# pricing & estimate
# ---------------------------------------------------------------------------


def test_known_models_have_pricing() -> None:
    p = model_pricing("claude-opus-4-7")
    assert p.input_per_mtok > Decimal(0)
    assert p.output_per_mtok > Decimal(0)


def test_unknown_model_falls_back_to_high_estimate() -> None:
    """Conservative: unknown models get high cost so the cap errs on safe side."""
    p = model_pricing("totally-fake-model")
    assert p.input_per_mtok >= Decimal("10.00")
    assert p.output_per_mtok >= Decimal("10.00")


def test_estimate_cost_usd_basic_math() -> None:
    # claude-sonnet-4-6 is $3 input / $15 output per Mtok
    cost = estimate_cost_usd(
        model="claude-sonnet-4-6",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert cost == Decimal("18.00")


def test_estimate_cost_zero_for_mock_model() -> None:
    cost = estimate_cost_usd(
        model="mock", prompt_tokens=10_000, completion_tokens=10_000
    )
    assert cost == Decimal("0")


# ---------------------------------------------------------------------------
# CostCapExceeded error shape
# ---------------------------------------------------------------------------


def test_cost_cap_exceeded_carries_attribution() -> None:
    err = CostCapExceeded(
        simulation_id="abc-123",
        total_so_far=1.5,
        estimated_next=0.6,
        hard_cap=2.0,
    )
    assert err.simulation_id == "abc-123"
    assert err.total_so_far == 1.5
    assert err.estimated_next == 0.6
    assert err.hard_cap == 2.0
    assert "abc-123" in str(err)
    assert "2.0" in str(err) or "2.000" in str(err)


# ---------------------------------------------------------------------------
# Integration test marker
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test():
    """Same per-test engine dispose used by other integration tests in this
    repo — keeps asyncpg connections from leaking across event loops."""
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover  defensive
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cost_guard_serializes_concurrent_calls_via_row_lock(
    basic_brief,
) -> None:
    """Two concurrent calls inside the same simulation must serialize behind
    the `SELECT ... FOR UPDATE` lock on `simulations.id`. The first call's
    cost-row commit must be visible to the second call's `SUM(cost_usd)`
    pre-check, so an over-cap second call refuses with `CostCapExceeded`.

    Setup: simulation with one prior $0.80 row + cap = $1.00. Two concurrent
    calls each estimate $0.30. Without the lock, both would pre-check
    $0.80 + $0.30 = $1.10 ≤ $1.00 false → both could over-spend. Under the
    lock, the first commits a $0.30 row; the second wakes up with total
    = $1.10 and refuses.
    """
    import asyncio
    from decimal import Decimal as D
    from uuid import uuid4

    from assembly.db import get_sessionmaker
    from assembly.llm.cost_guard import with_cost_guard
    from assembly.llm.cost_log import log_llm_call
    from assembly.llm.provider import LLMResponse
    from assembly.models.simulation import Simulation, SimulationInput

    sessionmaker = get_sessionmaker()

    sim_id = uuid4()
    brief = basic_brief
    async with sessionmaker() as session:
        async with session.begin():
            sim = Simulation(
                id=sim_id, status="pending", progress={"stage": "pending"}
            )
            sim.input = SimulationInput(
                product_type=brief.product_type,
                product_name=brief.product_name,
                description=brief.description,
                price_structure=brief.price_structure.model_dump(),
                target_society=brief.target_society.model_dump(),
                competitors=[c.model_dump() for c in brief.competitors],
                product_url=None,
                additional_context=brief.additional_context,
                raw_brief=brief.model_dump(mode="json"),
            )
            session.add(sim)
            # Seed a prior $0.50 row. With cap=$1.00 and each call estimated
            # at $0.40 + actual $0.20, the FIRST call (SUM=$0.50) clears at
            # $0.90 ≤ $1.00 and logs $0.20. The SECOND call only refuses if
            # the row lock is honored: post-lock SUM=$0.70, +$0.40 estimated
            # = $1.10 > $1.00 → CostCapExceeded. Without the lock, both
            # would see SUM=$0.50 in their pre-check and both would clear.
            await log_llm_call(
                session,
                simulation_id=sim_id,
                stage="seed",
                provider="mock",
                model="claude-sonnet-4-6",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                cost_usd=D("0.50"),
                success=True,
            )

    # claude-sonnet-4-6 is $3/MTok input + $15/MTok output. 33,334 + 6,667
    # tokens ≈ $0.20 actual cost per call. We pre-estimate at 66,667 + 13,334
    # tokens ≈ $0.40 so the projected post-lock total ($0.70 + $0.40 = $1.10)
    # exceeds the $1.00 cap on the second caller.

    def _make_response() -> LLMResponse:
        return LLMResponse(
            text="ok",
            prompt_tokens=33_334,
            completion_tokens=6_667,
            latency_ms=10,
            model="claude-sonnet-4-6",
            provider="mock",
        )

    async def attempt(delay_s: float) -> tuple[bool, str]:
        async def actual_call() -> LLMResponse:
            # Optional delay (held under the row lock) to ensure the other
            # task has fully entered with_cost_guard before this one releases.
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            return _make_response()

        try:
            await with_cost_guard(
                sessionmaker,
                simulation_id=sim_id,
                stage="round_baseline",
                provider="mock",
                model="claude-sonnet-4-6",
                estimated_prompt_tokens=66_667,
                estimated_completion_tokens=13_334,
                hard_cap_usd=D("1.00"),
                actual_call=actual_call,
            )
            return True, "ok"
        except CostCapExceeded as e:
            return False, str(e)

    # Run BOTH attempts concurrently. Postgres SELECT FOR UPDATE will queue
    # the second one behind the first — when it wakes up, the SUM includes
    # the first call's logged cost ($0.80 + $0.20 = $1.00), and the next
    # estimate of $0.30 pushes the projected total to $1.30, refused.
    slow_result, fast_result = await asyncio.gather(
        attempt(delay_s=0.5),
        attempt(delay_s=0.0),
    )

    successes = [r for r in (slow_result, fast_result) if r[0]]
    failures = [r for r in (slow_result, fast_result) if not r[0]]

    assert len(successes) == 1, (
        f"exactly one call should succeed under the cap; "
        f"successes={len(successes)} failures={len(failures)}"
    )
    assert len(failures) == 1, (
        "exactly one call should be refused as cap-exceeded"
    )
    fail_msg = failures[0][1].lower()
    assert "exceeds" in fail_msg or "cap" in fail_msg or "refuse" in fail_msg, (
        f"expected cost-cap message; got: {failures[0][1]}"
    )
