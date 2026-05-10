"""Phase 8.2F.5 — cleanup-fixture safety regression test.

Asserts that the safe cleanup helper (used by
`cleanup_tavily_source_records` in `test_tavily_adapter.py`) deletes
only rows whose metadata explicitly carries `test_fixture=true`.

Operator-inserted rows (e.g. from a Phase 8.2E live smoke test or a
Phase 8.2F.5 expansion run) carry `operator_run=true` and
`test_fixture=false`. Those rows MUST survive the test cleanup.

This regression locks the rule down so it can never silently regress
again.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from assembly.db import get_sessionmaker
from assembly.models.persona import SourceRecord


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_async_engine_after_each_test() -> AsyncIterator[None]:
    yield
    from assembly import db
    if db._engine is not None:
        try:
            await db._engine.dispose()
        except Exception:  # pragma: no cover
            pass
    db._engine = None
    db._sessionmaker = None


def _row(*, test_fixture: bool, operator_run: bool, content_hash: str) -> SourceRecord:
    return SourceRecord(
        id=uuid4(),
        source_kind="tavily_search_extract",
        source_url=f"https://example.test/{content_hash[:8]}",
        captured_at=datetime.now(UTC),
        content="cleanup safety regression fixture content",
        content_hash=content_hash,
        language="en",
        metadata_={
            "query": "cleanup safety regression",
            "result_rank": 0,
            "title": None,
            "domain": "example.test",
            "tavily_score": 0.5,
            "published_date": None,
            "test_fixture": test_fixture,
            "operator_run": operator_run,
            "run_purpose": (
                "phase_8_2f_5_human_signal_expansion"
                if operator_run else "test_fixture"
            ),
        },
        ingested_by="tavily_search_extract",
        compliance_tag="public_api",
        user_handle_hash=None,
        pii_redaction_status="redacted",
        sensitive_scan_status="clean",
    )


@pytest.fixture
async def isolated_cleanup_safety() -> AsyncIterator[None]:
    """Per-test isolation: insert + delete only rows with this test's
    `cleanup_safety_*` content_hash prefix so a parallel run / leftover
    row from another test cannot corrupt assertions."""
    sessionmaker = get_sessionmaker()
    yield
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(SourceRecord).where(
                    SourceRecord.content_hash.like("cleanup_safety_%")
                )
            )


@pytest.mark.asyncio
async def test_safe_cleanup_deletes_test_fixture_rows(
    isolated_cleanup_safety,
) -> None:
    """A row with `test_fixture=true` is deleted by the safe cleanup."""
    sessionmaker = get_sessionmaker()
    fixture_hash = "cleanup_safety_" + uuid4().hex[:32]
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                _row(test_fixture=True, operator_run=False, content_hash=fixture_hash)
            )

    # Invoke the same helper used by the cleanup fixture.
    from tests.test_tavily_adapter import _delete_only_test_fixture_tavily_rows
    await _delete_only_test_fixture_tavily_rows()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.content_hash == fixture_hash
                )
            )
        ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_safe_cleanup_preserves_operator_rows(
    isolated_cleanup_safety,
) -> None:
    """A row with `operator_run=true, test_fixture=false` is NOT
    deleted by the safe cleanup."""
    sessionmaker = get_sessionmaker()
    operator_hash = "cleanup_safety_op_" + uuid4().hex[:28]
    async with sessionmaker() as session:
        async with session.begin():
            session.add(
                _row(test_fixture=False, operator_run=True, content_hash=operator_hash)
            )

    from tests.test_tavily_adapter import _delete_only_test_fixture_tavily_rows
    await _delete_only_test_fixture_tavily_rows()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.content_hash == operator_hash
                )
            )
        ).scalars().all()
    assert len(rows) == 1, (
        "Safe cleanup MUST NOT delete operator-inserted rows. "
        "This regression locks the cleanup safety guarantee."
    )


@pytest.mark.asyncio
async def test_safe_cleanup_preserves_rows_without_test_fixture_flag(
    isolated_cleanup_safety,
) -> None:
    """Rows whose metadata does NOT carry an explicit `test_fixture`
    key are also preserved (defensive default)."""
    sessionmaker = get_sessionmaker()
    no_flag_hash = "cleanup_safety_nf_" + uuid4().hex[:28]
    async with sessionmaker() as session:
        async with session.begin():
            r = _row(test_fixture=False, operator_run=False, content_hash=no_flag_hash)
            # Strip the test_fixture flag entirely.
            md = dict(r.metadata_)
            md.pop("test_fixture", None)
            r.metadata_ = md
            session.add(r)

    from tests.test_tavily_adapter import _delete_only_test_fixture_tavily_rows
    await _delete_only_test_fixture_tavily_rows()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.content_hash == no_flag_hash
                )
            )
        ).scalars().all()
    assert len(rows) == 1, (
        "Rows without an explicit test_fixture=true marker MUST be "
        "preserved. The cleanup is opt-in via metadata, not opt-out."
    )


@pytest.mark.asyncio
async def test_safe_cleanup_mixed_rows_only_clears_fixtures(
    isolated_cleanup_safety,
) -> None:
    """Mixed table state: 1 fixture row + 1 operator row + 1 unflagged
    row. After cleanup, only the operator and unflagged rows remain."""
    sessionmaker = get_sessionmaker()
    fix = "cleanup_safety_mixed_fix_" + uuid4().hex[:24]
    op = "cleanup_safety_mixed_op_" + uuid4().hex[:24]
    nf = "cleanup_safety_mixed_nf_" + uuid4().hex[:24]
    async with sessionmaker() as session:
        async with session.begin():
            session.add(_row(test_fixture=True, operator_run=False, content_hash=fix))
            session.add(_row(test_fixture=False, operator_run=True, content_hash=op))
            r = _row(test_fixture=False, operator_run=False, content_hash=nf)
            md = dict(r.metadata_)
            md.pop("test_fixture", None)
            r.metadata_ = md
            session.add(r)

    from tests.test_tavily_adapter import _delete_only_test_fixture_tavily_rows
    await _delete_only_test_fixture_tavily_rows()

    async with sessionmaker() as session:
        remaining = {
            row.content_hash
            for row in (
                await session.execute(
                    select(SourceRecord).where(
                        SourceRecord.content_hash.in_([fix, op, nf])
                    )
                )
            ).scalars().all()
        }
    assert remaining == {op, nf}
