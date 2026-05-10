"""Phase 8.2C — adapter base class.

Every source adapter subclasses `SourceAdapter`. The base class:

  1. Refuses to ingest unless `adapter_compliance_status` says approved
     (with approver + approved_at populated).
  2. Validates the compliance memo file exists.
  3. Calls `fetch_mocked()` (subclass-implemented) to produce raw payloads.
     `fetch_live()` is declared but raises `NotImplementedError` in 8.2C.
  4. Normalizes each payload via `normalize_payload`.
  5. Runs the redaction-before-storage pipeline.
  6. Inserts accepted records into `source_records`.
  7. Catches the UNIQUE constraint on `(source_kind, content_hash)` to
     dedup silently (counter logged, not raised).
  8. Returns an `AdapterRunSummary` with structured rejection reasons.
  9. Sets `live_network_used=False` — no live network calls happen here.

Subclasses MUST declare class vars:
  NAME, SOURCE_KIND, COMPLIANCE_TAG, MEMO_PATH, METADATA_SCHEMA.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from assembly.models.evidence import EvidenceItem  # noqa: F401  (avoids unused-import warnings in subclasses)
from assembly.pipeline.ingestion.compliance import (
    ComplianceError,
    assert_adapter_approved,
)
from assembly.pipeline.ingestion.redaction import prepare_source_record_insert
from assembly.pipeline.ingestion.run_summary import (
    AdapterRunSummary,
    NormalizedSourcePayload,
    RawSourcePayload,
    RecordRejection,
)


logger = logging.getLogger(__name__)


class SourceAdapter(ABC):
    """Abstract base for all Population-Mode source adapters.

    Phase 8.2C ships only `fetch_mocked()`; `fetch_live()` raises until
    Phase 8.2D's first approved real adapter overrides it.

    Subclass contract (class vars are checked at construction time):

      NAME              — unique adapter identifier; matches the row in
                          `adapter_compliance_status.adapter_name`
      SOURCE_KIND       — value written into `source_records.source_kind`
      COMPLIANCE_TAG    — closed enum: 'public_api' | 'public_html' |
                          'open_dataset' | 'open_aggregate' | 'manual_seed'
      MEMO_PATH         — relative path to the compliance memo .md
      METADATA_SCHEMA   — Pydantic model that adapter metadata must validate
                          against (per-adapter shape; declared in subclass)
    """

    NAME: ClassVar[str]
    SOURCE_KIND: ClassVar[str]
    COMPLIANCE_TAG: ClassVar[str]
    MEMO_PATH: ClassVar[str]
    METADATA_SCHEMA: ClassVar[type[BaseModel]]

    def __init__(self) -> None:
        # Class-var presence check at construction; helps surface mistakes
        # immediately rather than at the first ingest call.
        for required in ("NAME", "SOURCE_KIND", "COMPLIANCE_TAG", "MEMO_PATH",
                         "METADATA_SCHEMA"):
            if not getattr(type(self), required, None):
                raise TypeError(
                    f"{type(self).__name__} must declare class var {required!r}"
                )

    # ------------------------------------------------------------------
    # Abstract — subclasses implement
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch_mocked(self) -> Sequence[RawSourcePayload]:
        """Return a sequence of raw payloads from a hardcoded / fixture
        source. Phase 8.2C uses this exclusively — no network calls."""

    @abstractmethod
    def normalize_payload(
        self, raw: RawSourcePayload,
    ) -> NormalizedSourcePayload:
        """Adapter-specific normalization: enforce the per-adapter
        METADATA_SCHEMA, set `language`, pre-truncate, etc."""

    # ------------------------------------------------------------------
    # Live fetch — overridden by approved adapters from Phase 8.2E onwards
    # ------------------------------------------------------------------

    async def fetch_live(self) -> Sequence[RawSourcePayload]:
        """Default base behavior: live ingestion not implemented.
        Subclasses for approved sources override this — and only after
        the corresponding compliance memo is signed off and the
        `adapter_compliance_status.status='approved'` row is set with
        approver + approved_at populated. Phase 8.2E ships the first
        approved override (`TavilySearchExtractAdapter`)."""
        raise NotImplementedError(
            "Live ingestion is not implemented for this adapter."
        )

    # ------------------------------------------------------------------
    # Concrete — base class
    # ------------------------------------------------------------------

    async def ingest_mocked(
        self,
        *,
        sessionmaker: async_sessionmaker,
        salt: str,
        accepted_cap: int | None = None,
    ) -> AdapterRunSummary:
        """Run the full mocked ingestion pipeline.

        Order:
          1. compliance gate (raises ComplianceError on refusal)
          2. fetch_mocked → list[RawSourcePayload]
          3. normalize each payload (subclass)
          4. redaction-before-storage (refuse-or-prepare insert)
          5. write accepted rows to source_records (UNIQUE → dedup)
          6. return AdapterRunSummary

        `live_network_used` is hard-coded `False`. Mocked-only path.
        """
        return await self._ingest_payloads(
            payloads=list(self.fetch_mocked()),
            sessionmaker=sessionmaker,
            salt=salt,
            live_network_used=False,
            accepted_cap=accepted_cap,
        )

    async def ingest_live(
        self,
        *,
        sessionmaker: async_sessionmaker,
        salt: str,
        accepted_cap: int | None = None,
    ) -> AdapterRunSummary:
        """Run the live ingestion pipeline. Same compliance gate +
        redaction-before-storage discipline as `ingest_mocked`. The
        only difference is that payloads are produced by `fetch_live`
        (which talks to the network) and `live_network_used=True`.

        Phase 8.2E introduces this path. The drift test continues to
        assert that `pipeline/ingestion/` does not import network /
        scraping libraries except via the approved adapter file.
        """
        # Compliance gate — raises on refusal. We check before doing
        # anything network-bound so unauthorized adapters never even
        # reach the network.
        await assert_adapter_approved(
            sessionmaker,
            adapter_name=self.NAME,
            memo_path=self.MEMO_PATH,
        )
        raw = list(await self.fetch_live())
        return await self._ingest_payloads(
            payloads=raw,
            sessionmaker=sessionmaker,
            salt=salt,
            live_network_used=True,
            accepted_cap=accepted_cap,
            compliance_already_checked=True,
        )

    async def _ingest_payloads(
        self,
        *,
        payloads: list[RawSourcePayload],
        sessionmaker: async_sessionmaker,
        salt: str,
        live_network_used: bool,
        accepted_cap: int | None,
        compliance_already_checked: bool = False,
    ) -> AdapterRunSummary:
        """Shared post-fetch pipeline used by both mocked and live paths."""
        if not compliance_already_checked:
            await assert_adapter_approved(
                sessionmaker,
                adapter_name=self.NAME,
                memo_path=self.MEMO_PATH,
            )

        summary = AdapterRunSummary(
            adapter_name=self.NAME,
            source_kind=self.SOURCE_KIND,
            fetched_count=len(payloads),
            compliance_status="approved",
            live_network_used=live_network_used,
        )

        from assembly.models.evidence import EvidenceItem  # noqa: F401
        from assembly.models.persona import SourceRecord  # local to avoid cycles

        for payload in payloads:
            if accepted_cap is not None and summary.accepted_count >= accepted_cap:
                break
            try:
                normalized = self.normalize_payload(payload)
            except NormalizationRejection as nrej:
                summary.rejected_count += 1
                summary.rejection_reasons.append(
                    RecordRejection(
                        reason_code=nrej.reason_code,
                        message=nrej.message,
                        source_url=payload.source_url,
                    )
                )
                continue
            except Exception as e:  # pragma: no cover  defensive
                summary.rejected_count += 1
                summary.rejection_reasons.append(
                    RecordRejection(
                        reason_code="NORMALIZATION_FAILED",
                        message=f"{type(e).__name__}: {e}",
                        source_url=payload.source_url,
                    )
                )
                continue

            if normalized is None:
                # Adapter chose to drop this payload (e.g. paywall) without
                # raising; record it as a rejection with adapter context.
                summary.rejected_count += 1
                summary.rejection_reasons.append(
                    RecordRejection(
                        reason_code="DROPPED_BY_ADAPTER",
                        message="Adapter normalize_payload returned None.",
                        source_url=payload.source_url,
                    )
                )
                continue

            insert_dict, rejection = prepare_source_record_insert(
                normalized,
                source_kind=self.SOURCE_KIND,
                compliance_tag=self.COMPLIANCE_TAG,
                ingested_by=self.NAME,
                salt=salt,
            )
            if rejection is not None:
                summary.rejected_count += 1
                summary.rejection_reasons.append(rejection)
                continue
            assert insert_dict is not None

            try:
                async with sessionmaker() as session:
                    async with session.begin():
                        session.add(SourceRecord(id=uuid.uuid4(), **insert_dict))
                summary.accepted_count += 1
            except IntegrityError as e:
                if "uq_source_records_kind_hash" in str(e.orig):
                    summary.deduped_count += 1
                    continue
                summary.rejected_count += 1
                summary.rejection_reasons.append(
                    RecordRejection(
                        reason_code="DB_INTEGRITY_ERROR",
                        message=str(e.orig)[:300],
                        source_url=insert_dict.get("source_url"),
                    )
                )

        return summary


class NormalizationRejection(Exception):
    """Raised by an adapter's `normalize_payload` to reject a payload
    with a structured reason that goes straight into the run summary."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{reason_code}: {message}")


# Backwards-compat alias used inside this module's dispatcher.
_NormalizationRejection = NormalizationRejection
