"""Phase 8.2I — executor tests (integration, real Postgres).

Exercises the dry-run path end-to-end against the live DB. The live
path is NOT exercised in tests — that requires operator approval +
flipping the Tavily compliance row.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from assembly.db import get_sessionmaker
from assembly.pipeline.run_scoped_topup import (
    TopUpComplianceCaveatUnresolved,
    TopUpReadinessAlreadySufficient,
    execute_topup_loop_dry_run,
    execute_topup_loop_live,
)
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
    WATER_BOTTLE_BRIEF,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:
            pass
    db._engine = None
    db._sessionmaker = None


@pytest.mark.asyncio
async def test_dry_run_produces_a_plan_for_amboras() -> None:
    sm = get_sessionmaker()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
    )
    assert result.dry_run is True
    assert result.ingestion is None
    assert result.persona_write is None
    assert result.reaudit is None
    assert result.plan.brief_label == "amboras"
    assert result.plan.total_queries >= 1
    assert "no Tavily live call issued" in "\n".join(result.safety_assertions)


@pytest.mark.asyncio
async def test_dry_run_does_not_create_personas() -> None:
    """Smoke check: dry-run must not create any persona/source row."""
    from sqlalchemy import select, func
    from assembly.models.persona import (
        PersonaRecord, PersonaTrait, PersonaEvidenceLink,
    )
    sm = get_sessionmaker()
    async with sm() as s:
        before_p = (await s.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar()
        before_t = (await s.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar()
        before_l = (await s.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar()

    await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
    )

    async with sm() as s:
        after_p = (await s.execute(
            select(func.count()).select_from(PersonaRecord)
        )).scalar()
        after_t = (await s.execute(
            select(func.count()).select_from(PersonaTrait)
        )).scalar()
        after_l = (await s.execute(
            select(func.count()).select_from(PersonaEvidenceLink)
        )).scalar()

    assert (before_p, before_t, before_l) == (after_p, after_t, after_l)


@pytest.mark.asyncio
async def test_dry_run_for_halal_refuses_without_approval() -> None:
    """Halal financing brief produces only sensitive top-up recs.
    Without `approve_sensitive_topup=True`, the planner's selection
    leaves nothing actionable and raises."""
    sm = get_sessionmaker()
    with pytest.raises(ValueError):
        await execute_topup_loop_dry_run(
            sessionmaker=sm,
            brief=HALAL_FINANCING_BRIEF,
            brief_label="halal_financing",
            approve_sensitive_topup=False,
        )


@pytest.mark.asyncio
async def test_dry_run_for_halal_with_approval_marks_compliance_required() -> None:
    sm = get_sessionmaker()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=HALAL_FINANCING_BRIEF,
        brief_label="halal_financing",
        approve_sensitive_topup=True,
    )
    assert result.plan.requires_compliance_approval is True
    assert result.plan.sensitive_caveats


@pytest.mark.asyncio
async def test_live_path_refuses_when_compliance_caveat_unresolved() -> None:
    """The live path refuses to run on a sensitive brief unless
    `approve_sensitive_topup=True` is explicitly passed."""
    sm = get_sessionmaker()
    with pytest.raises(
        (TopUpComplianceCaveatUnresolved, ValueError, TopUpReadinessAlreadySufficient),
    ):
        await execute_topup_loop_live(
            sessionmaker=sm,
            brief=HALAL_FINANCING_BRIEF,
            brief_label="halal_financing_test",
            approver_label="test_only",
            approve_sensitive_topup=False,
        )


@pytest.mark.asyncio
async def test_dry_run_plan_caps_match_amboras_phase_8_2i_targets() -> None:
    """Spec caps for Phase 8.2I Amboras top-up:
       * top 5 missing/thin categories
       * 3 queries / category
       * 15 total queries
       * 10 results / query
       * 100 accepted records
       * 50 personas write cap
       * $2.00 cost cap
    """
    sm = get_sessionmaker()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
    )
    p = result.plan
    assert len(p.target_categories) <= 5
    assert p.max_queries_per_category == 3
    assert p.max_total_queries == 15
    assert p.max_results_per_query == 10
    assert p.max_accepted_records == 100
    assert p.persona_write_cap == 50
    assert p.cost_cap_usd == Decimal("2.00")
