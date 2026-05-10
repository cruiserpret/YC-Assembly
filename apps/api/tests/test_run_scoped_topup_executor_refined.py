"""Phase 8.2I.1 — executor refined-plan-override path tests.

Asserts the executor's `topup_plan_override` parameter works:
the dry-run accepts a plan built directly from the refined catalog
without going through the audience-retrieval-driven query picker;
the live path will propagate `query_refinement_version` to Tavily.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from assembly.db import get_sessionmaker
from assembly.pipeline.run_scoped_topup import (
    build_amboras_refined_topup_plan,
    execute_topup_loop_dry_run,
)
from assembly.pipeline.target_society import AMBORAS_BRIEF


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
async def test_dry_run_with_refined_override_uses_refined_queries() -> None:
    """When `topup_plan_override` is supplied the dry-run echoes that
    plan without going through audience-retrieval-driven query
    selection."""
    sm = get_sessionmaker()
    refined = build_amboras_refined_topup_plan()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
        topup_plan_override=refined,
    )
    # Plan in result is exactly the refined plan we passed.
    assert result.plan == refined
    # The plan carries the 8.2I.1 refinement label.
    assert result.plan.query_refinement_version == "8.2I.1"
    # First-query in shopify_or_platform_merchant is a quoted phrase.
    qs = result.plan.queries_by_category["shopify_or_platform_merchant"]
    assert qs[0].startswith('"Shopify')


@pytest.mark.asyncio
async def test_dry_run_without_override_still_uses_audience_driven_picker() -> None:
    """Phase 8.2I behavior must be preserved when no override is
    supplied — the executor falls back to
    `build_topup_plan_from_audience_retrieval`."""
    sm = get_sessionmaker()
    result = await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
    )
    # No refinement label means audience-driven plan.
    assert result.plan.query_refinement_version is None


@pytest.mark.asyncio
async def test_dry_run_with_refined_override_writes_no_data() -> None:
    """Dry-run with the refined override still touches no DB rows."""
    from sqlalchemy import select, func
    from assembly.models.persona import (
        PersonaRecord, PersonaTrait, PersonaEvidenceLink, SourceRecord,
    )
    sm = get_sessionmaker()
    async with sm() as s:
        before = (
            (await s.execute(select(func.count()).select_from(PersonaRecord))).scalar(),
            (await s.execute(select(func.count()).select_from(PersonaTrait))).scalar(),
            (await s.execute(select(func.count()).select_from(PersonaEvidenceLink))).scalar(),
            (await s.execute(select(func.count()).select_from(SourceRecord))).scalar(),
        )
    refined = build_amboras_refined_topup_plan()
    await execute_topup_loop_dry_run(
        sessionmaker=sm,
        brief=AMBORAS_BRIEF,
        brief_label="amboras",
        topup_plan_override=refined,
    )
    async with sm() as s:
        after = (
            (await s.execute(select(func.count()).select_from(PersonaRecord))).scalar(),
            (await s.execute(select(func.count()).select_from(PersonaTrait))).scalar(),
            (await s.execute(select(func.count()).select_from(PersonaEvidenceLink))).scalar(),
            (await s.execute(select(func.count()).select_from(SourceRecord))).scalar(),
        )
    assert before == after, (
        f"dry-run with refined override must not write rows; "
        f"before={before} after={after}"
    )
