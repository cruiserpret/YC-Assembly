"""Phase 8.2C — Pydantic shapes for the ingestion framework."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawSourcePayload(BaseModel):
    """Raw payload an adapter produces from a (mocked) source. Goes
    through normalization + redaction before any storage decision."""

    model_config = ConfigDict(extra="forbid")

    source_url: str | None = None
    captured_at: datetime
    content: str
    raw_handle: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedSourcePayload(BaseModel):
    """Same shape as RawSourcePayload + adapter-detected language."""

    model_config = ConfigDict(extra="forbid")

    source_url: str | None = None
    captured_at: datetime
    content: str
    raw_handle: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None


class RecordRejection(BaseModel):
    """Structured rejection record. Logged into AdapterRunSummary —
    never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    reason_code: str
    message: str
    source_url: str | None = None


class AdapterRunSummary(BaseModel):
    """Result of one adapter ingestion run.

    `live_network_used` MUST be False in Phase 8.2C. The adapter base
    class sets it to False; the drift test asserts the framework cannot
    flip it true.
    """

    model_config = ConfigDict(extra="forbid")

    adapter_name: str
    source_kind: str
    fetched_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    deduped_count: int = 0
    rejection_reasons: list[RecordRejection] = Field(default_factory=list)
    compliance_status: str = "unknown"
    live_network_used: bool = False
