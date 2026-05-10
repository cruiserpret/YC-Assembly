"""Phase 8.3B — Firecrawl compliance gate.

The Firecrawl adapter REFUSES any live HTTP call until ALL of:

  1. The compliance memo exists at `FIRECRAWL_MEMO_PATH`.
  2. A row in `adapter_compliance_status` is registered for
     `adapter_name='firecrawl_extract'`.
  3. That row's `status='approved'` AND `approver` + `approved_at`
     are populated.

Default first-registration `status` is `'review'` (mirroring Tavily's
Phase 8.2E pattern). An operator must explicitly flip the row to
`'approved'` to authorize a live run; the flip is auditable in
`adapter_compliance_status_history`.

This module wraps the existing `assert_adapter_approved` helper so the
Firecrawl boundary is preserved (the gate raises a Firecrawl-specific
exception type rather than the generic `ComplianceError`, which lets
upstream callers catch Firecrawl failures cleanly without coupling to
internal compliance plumbing).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.pipeline.ingestion.compliance import (
    ComplianceError,
    assert_adapter_approved,
)
from assembly.pipeline.ingestion.firecrawl.errors import (
    FirecrawlComplianceNotApproved,
)


FIRECRAWL_ADAPTER_NAME = "firecrawl_extract"
FIRECRAWL_MEMO_PATH = "apps/api/docs/source_compliance/firecrawl.md"
FIRECRAWL_DEFAULT_STATUS = "review"


async def assert_firecrawl_approved(
    sessionmaker: async_sessionmaker,
) -> None:
    """Raise `FirecrawlComplianceNotApproved` if the Firecrawl adapter
    is not authorized to run a live extraction.

    On success, returns None. On any compliance failure (memo missing,
    row not registered, status != 'approved', missing approver fields,
    suspended), raises `FirecrawlComplianceNotApproved` carrying the
    underlying compliance reason.
    """
    try:
        await assert_adapter_approved(
            sessionmaker,
            adapter_name=FIRECRAWL_ADAPTER_NAME,
            memo_path=FIRECRAWL_MEMO_PATH,
        )
    except ComplianceError as e:
        raise FirecrawlComplianceNotApproved(str(e)) from e


__all__ = [
    "FIRECRAWL_ADAPTER_NAME",
    "FIRECRAWL_DEFAULT_STATUS",
    "FIRECRAWL_MEMO_PATH",
    "assert_firecrawl_approved",
]
