"""Phase 8.2C — adapter compliance gate.

Adapters MUST register in `adapter_compliance_status` with status='approved'
AND populated `approver` + `approved_at` BEFORE any ingestion runs. The
default for newly-registered adapters is `'draft'`. Phase 8.2C ships the
Reddit memo at `'draft'` — no live ingestion authorized.

Structured error codes:

  ADAPTER_NOT_REGISTERED   — no row in adapter_compliance_status
  ADAPTER_NOT_APPROVED     — status in ('draft','review')
  ADAPTER_SUSPENDED        — status='suspended'
  MEMO_MISSING             — memo_path doesn't exist on disk
  APPROVAL_FIELDS_MISSING  — status='approved' but approver / approved_at null
"""
from __future__ import annotations

import enum
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.adapter_status import (
    COMPLIANCE_STATUS_VALUES,
    AdapterComplianceStatus,
)


class ComplianceErrorCode(str, enum.Enum):
    ADAPTER_NOT_REGISTERED = "ADAPTER_NOT_REGISTERED"
    ADAPTER_NOT_APPROVED = "ADAPTER_NOT_APPROVED"
    ADAPTER_SUSPENDED = "ADAPTER_SUSPENDED"
    MEMO_MISSING = "MEMO_MISSING"
    APPROVAL_FIELDS_MISSING = "APPROVAL_FIELDS_MISSING"


class ComplianceError(Exception):
    """Raised when an adapter is not authorized to ingest. Carries a
    structured `code` so callers can decide whether to no-op + warn or
    fail the run."""

    def __init__(
        self,
        code: ComplianceErrorCode,
        message: str,
        *,
        adapter_name: str | None = None,
    ) -> None:
        self.code = code
        self.adapter_name = adapter_name
        super().__init__(f"{code.value}: {message}")


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def get_adapter_compliance_status(
    sessionmaker: async_sessionmaker, adapter_name: str,
) -> AdapterComplianceStatus | None:
    """Return the row for `adapter_name` or None if unregistered."""
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == adapter_name
                )
            )
        ).scalar_one_or_none()


async def assert_adapter_approved(
    sessionmaker: async_sessionmaker,
    *,
    adapter_name: str,
    memo_path: str | None = None,
) -> AdapterComplianceStatus:
    """Raise `ComplianceError` if the adapter cannot run.

    Checks (in order):
      1. memo_path exists on disk (when supplied).
      2. adapter_compliance_status row exists.
      3. status='approved' with approver + approved_at populated.
      4. status not 'suspended'.
      5. status not 'draft' or 'review'.

    Returns the row when all checks pass."""
    if memo_path is not None:
        validate_compliance_memo_exists(memo_path)

    row = await get_adapter_compliance_status(sessionmaker, adapter_name)
    if row is None:
        raise ComplianceError(
            ComplianceErrorCode.ADAPTER_NOT_REGISTERED,
            f"adapter {adapter_name!r} has no row in adapter_compliance_status. "
            "Register it first via `register_or_update_adapter_status`.",
            adapter_name=adapter_name,
        )

    if row.status == "suspended":
        raise ComplianceError(
            ComplianceErrorCode.ADAPTER_SUSPENDED,
            f"adapter {adapter_name!r} is suspended. "
            f"Notes: {row.notes or '<none>'}.",
            adapter_name=adapter_name,
        )

    if row.status in ("draft", "review"):
        raise ComplianceError(
            ComplianceErrorCode.ADAPTER_NOT_APPROVED,
            f"adapter {adapter_name!r} is not approved (status={row.status!r}). "
            f"Memo: {row.memo_path}. Phase 8.2C does NOT authorize live "
            "ingestion — Reddit and other public sources require human "
            "approval before status can flip to 'approved'.",
            adapter_name=adapter_name,
        )

    if row.status == "approved":
        if row.approver is None or row.approved_at is None:
            raise ComplianceError(
                ComplianceErrorCode.APPROVAL_FIELDS_MISSING,
                f"adapter {adapter_name!r} has status='approved' but "
                "approver / approved_at fields are missing. The DB CHECK "
                "should normally prevent this; treat as a corrupt row.",
                adapter_name=adapter_name,
            )
        return row

    # Should be unreachable; the migration's CHECK enumerates statuses.
    raise ComplianceError(
        ComplianceErrorCode.ADAPTER_NOT_APPROVED,
        f"adapter {adapter_name!r} has unexpected status={row.status!r}.",
        adapter_name=adapter_name,
    )


def validate_compliance_memo_exists(memo_path: str) -> None:
    """Raise `ComplianceError(MEMO_MISSING)` if the memo file is absent."""
    p = Path(memo_path)
    # Resolve relative to the repo root so adapters can reference
    # 'apps/api/docs/compliance/<name>.md' as a stable identifier.
    if not p.is_absolute():
        # Walk up from this file's location until we hit a directory that
        # contains 'apps/' (the monorepo root) — keeps tests + production
        # consistent regardless of CWD.
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / memo_path
            if candidate.is_file():
                return
        raise ComplianceError(
            ComplianceErrorCode.MEMO_MISSING,
            f"compliance memo not found at relative path {memo_path!r} "
            "(searched up from pipeline/ingestion/compliance.py).",
        )
    if not p.is_file():
        raise ComplianceError(
            ComplianceErrorCode.MEMO_MISSING,
            f"compliance memo not found at {memo_path!r}.",
        )


# ---------------------------------------------------------------------------
# Write helpers (operator-only)
# ---------------------------------------------------------------------------


async def register_or_update_adapter_status(
    sessionmaker: async_sessionmaker,
    *,
    adapter_name: str,
    status: str,
    memo_path: str,
    approver: str | None = None,
    approved_at: datetime | None = None,
    notes: str | None = None,
) -> AdapterComplianceStatus:
    """Insert or update an adapter row. Operator-only — there is no API
    surface that calls this. The DB CHECK enforces that status='approved'
    requires approver + approved_at; we re-check here so callers see a
    clean Python-side error."""
    if status not in COMPLIANCE_STATUS_VALUES:
        raise ValueError(
            f"unknown status {status!r}; allowed: {COMPLIANCE_STATUS_VALUES}"
        )
    if status == "approved" and (approver is None or approved_at is None):
        raise ValueError(
            "status='approved' requires both `approver` and `approved_at`."
        )

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(AdapterComplianceStatus).where(
                        AdapterComplianceStatus.adapter_name == adapter_name
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                row = AdapterComplianceStatus(
                    adapter_name=adapter_name,
                    status=status,
                    memo_path=memo_path,
                    approver=approver,
                    approved_at=approved_at,
                    last_reviewed_at=now,
                    notes=notes,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                existing.status = status
                existing.memo_path = memo_path
                existing.approver = approver
                existing.approved_at = approved_at
                existing.last_reviewed_at = now
                existing.notes = notes
                existing.updated_at = now
                row = existing
        # Re-fetch outside the begin() block so the caller has a usable copy.
        return (
            await session.execute(
                select(AdapterComplianceStatus).where(
                    AdapterComplianceStatus.adapter_name == adapter_name
                )
            )
        ).scalar_one()
