"""Phase 8.2C — compliance gate tests.

Verifies that the framework structurally REFUSES to run any adapter
unless `adapter_compliance_status.status='approved'` AND the approval
fields (`approver`, `approved_at`) are populated AND the memo file
exists.

Integration-marked because the gate reads from the DB.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from assembly.db import get_sessionmaker
from assembly.models.adapter_status import AdapterComplianceStatus
from assembly.pipeline.ingestion import (
    ComplianceError,
    ComplianceErrorCode,
    MockRedditPublicAPIAdapter,
    assert_adapter_approved,
    register_or_update_adapter_status,
    validate_compliance_memo_exists,
)


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


@pytest.fixture
async def isolated_adapter_name() -> AsyncIterator[str]:
    """Per-test adapter name + cleanup so re-runs don't collide on the
    PRIMARY KEY of `adapter_compliance_status`."""
    name = f"test_adapter_{uuid4().hex[:8]}"
    yield name
    # Cleanup
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(
                delete(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == name
                )
            )


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_cannot_run_without_compliance_row(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    with pytest.raises(ComplianceError) as excinfo:
        await assert_adapter_approved(
            sessionmaker, adapter_name=isolated_adapter_name,
        )
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_NOT_REGISTERED


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "review"])
async def test_adapter_cannot_run_when_status_not_approved(
    isolated_adapter_name: str, status: str,
) -> None:
    sessionmaker = get_sessionmaker()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=isolated_adapter_name,
        status=status,
        memo_path="apps/api/docs/compliance/reddit_public_api.md",
    )
    with pytest.raises(ComplianceError) as excinfo:
        await assert_adapter_approved(
            sessionmaker, adapter_name=isolated_adapter_name,
        )
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_NOT_APPROVED


@pytest.mark.asyncio
async def test_adapter_cannot_run_when_status_suspended(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=isolated_adapter_name,
        status="suspended",
        memo_path="apps/api/docs/compliance/reddit_public_api.md",
        notes="Testing suspended path",
    )
    with pytest.raises(ComplianceError) as excinfo:
        await assert_adapter_approved(
            sessionmaker, adapter_name=isolated_adapter_name,
        )
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_SUSPENDED


# ---------------------------------------------------------------------------
# Approval path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_can_run_when_approved_with_required_fields(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=isolated_adapter_name,
        status="approved",
        memo_path="apps/api/docs/compliance/reddit_public_api.md",
        approver="test-approver",
        approved_at=datetime.now(UTC),
    )
    row = await assert_adapter_approved(
        sessionmaker, adapter_name=isolated_adapter_name,
    )
    assert row.status == "approved"
    assert row.approver == "test-approver"
    assert row.approved_at is not None


@pytest.mark.asyncio
async def test_register_rejects_approved_without_approver(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    # The Python helper rejects with ValueError BEFORE the DB CHECK
    # would catch it — same defense-in-depth.
    with pytest.raises(ValueError):
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=isolated_adapter_name,
            status="approved",
            memo_path="apps/api/docs/compliance/reddit_public_api.md",
            approver=None,
            approved_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_register_rejects_approved_without_approved_at(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    with pytest.raises(ValueError):
        await register_or_update_adapter_status(
            sessionmaker,
            adapter_name=isolated_adapter_name,
            status="approved",
            memo_path="apps/api/docs/compliance/reddit_public_api.md",
            approver="test-approver",
            approved_at=None,
        )


# ---------------------------------------------------------------------------
# Memo file presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_memo_file_fails(
    isolated_adapter_name: str,
) -> None:
    sessionmaker = get_sessionmaker()
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=isolated_adapter_name,
        status="approved",
        memo_path="apps/api/docs/compliance/this_memo_does_not_exist.md",
        approver="test-approver",
        approved_at=datetime.now(UTC),
    )
    with pytest.raises(ComplianceError) as excinfo:
        await assert_adapter_approved(
            sessionmaker,
            adapter_name=isolated_adapter_name,
            memo_path="apps/api/docs/compliance/this_memo_does_not_exist.md",
        )
    assert excinfo.value.code is ComplianceErrorCode.MEMO_MISSING


def test_validate_compliance_memo_template_exists() -> None:
    """The template must exist at a stable path. Adapter authors copy
    this when registering a new source."""
    validate_compliance_memo_exists("apps/api/docs/compliance/TEMPLATE.md")


def test_reddit_memo_exists_and_is_not_approved_by_default() -> None:
    """Phase 8.2C ships the Reddit memo at status='draft'. The memo
    file MUST exist (so the gate's memo-presence check passes) AND
    it MUST clearly state that the source is not approved."""
    memo_relative = "apps/api/docs/compliance/reddit_public_api.md"
    validate_compliance_memo_exists(memo_relative)

    here = Path(__file__).resolve()
    memo_path: Path | None = None
    for parent in here.parents:
        candidate = parent / memo_relative
        if candidate.is_file():
            memo_path = candidate
            break
    assert memo_path is not None
    text = memo_path.read_text(encoding="utf-8")
    # The Status field must read draft/review — not approved.
    assert "Status:** draft" in text or "Status:** review" in text, (
        f"Reddit memo must NOT ship as approved; current top-of-file status "
        f"line is unexpected. First 400 chars:\n{text[:400]}"
    )
    # Explicit not-approved language must be present.
    assert "NOT APPROVED" in text or "not approved" in text.lower()


# ---------------------------------------------------------------------------
# Mock-adapter integration: refuses to run while at draft status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_reddit_adapter_refuses_run_while_draft(
    isolated_adapter_name: str,
) -> None:
    """End-to-end gate: the mocked Reddit adapter, registered with the
    real `reddit_public_api_mock` name at status='draft', refuses to
    write any source_records."""
    sessionmaker = get_sessionmaker()
    # Use the actual adapter NAME so the gate reads the real row.
    adapter = MockRedditPublicAPIAdapter()
    # Ensure the row is at draft (or absent — reset to draft for hygiene)
    await register_or_update_adapter_status(
        sessionmaker,
        adapter_name=adapter.NAME,
        status="draft",
        memo_path=adapter.MEMO_PATH,
        notes="Phase 8.2C test fixture; not approved.",
    )
    with pytest.raises(ComplianceError) as excinfo:
        await adapter.ingest_mocked(sessionmaker=sessionmaker, salt="test-salt")
    assert excinfo.value.code is ComplianceErrorCode.ADAPTER_NOT_APPROVED
