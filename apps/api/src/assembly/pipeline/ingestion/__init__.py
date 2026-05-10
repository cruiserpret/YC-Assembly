"""Phase 8.2C — safe ingestion framework.

This package implements the FRAMEWORK for source adapters without
turning on any live ingestion. Live network calls are forbidden by
construction:

  * `pipeline/ingestion/` does NOT import any network or browser-
    automation library (httpx, requests, aiohttp, urllib, playwright,
    selenium, firecrawl, tavily, brave, jina, scrapy, bs4, praw, …).
    A drift test asserts this.

  * `SourceAdapter.fetch_live()` is declared but raises
    `NotImplementedError`. Phase 8.2D — the first APPROVED real
    adapter — will subclass and override.

  * Every adapter must register in `adapter_compliance_status` with
    status='approved' AND populated approver + approved_at fields
    BEFORE any ingestion runs. Status='draft' is the default; the
    Reddit memo ships at status='draft' until human sign-off lands.

  * Every accepted SourceRecord goes through redaction +
    sensitive-attribute scan FIRST. Records that fail either are
    rejected entirely; partially-redacted rows never land.

Public surface:

    SourceAdapter          — abstract base class
    AdapterRunSummary      — Pydantic run-result shape
    RawSourcePayload       — adapter input shape
    NormalizedSourcePayload — post-normalization shape
    RecordRejection        — structured rejection record
    ComplianceError        — structured compliance gate failures
    redact_source_payload, prepare_source_record_insert,
    sanitize_content_for_storage, scan_source_payload_for_sensitive_content
                            — redaction-before-storage helpers
    MockRedditPublicAPIAdapter — mocked-only adapter; no network deps
"""
from __future__ import annotations

from assembly.pipeline.ingestion.adapter_base import (
    NormalizationRejection,
    SourceAdapter,
)
from assembly.pipeline.ingestion.compliance import (
    ComplianceError,
    ComplianceErrorCode,
    assert_adapter_approved,
    get_adapter_compliance_status,
    register_or_update_adapter_status,
    validate_compliance_memo_exists,
)
from assembly.pipeline.ingestion.mock_adapters import MockRedditPublicAPIAdapter
from assembly.pipeline.ingestion.redaction import (
    prepare_source_record_insert,
    redact_source_payload,
    sanitize_content_for_storage,
    scan_source_payload_for_sensitive_content,
)
from assembly.pipeline.ingestion.run_summary import (
    AdapterRunSummary,
    NormalizedSourcePayload,
    RawSourcePayload,
    RecordRejection,
)
from assembly.pipeline.ingestion.tavily_adapter import (
    TavilyApiKeyMissing,
    TavilyResultMetadata,
    TavilySearchExtractAdapter,
)


__all__ = [
    "AdapterRunSummary",
    "ComplianceError",
    "ComplianceErrorCode",
    "MockRedditPublicAPIAdapter",
    "NormalizationRejection",
    "NormalizedSourcePayload",
    "RawSourcePayload",
    "RecordRejection",
    "SourceAdapter",
    "TavilyApiKeyMissing",
    "TavilyResultMetadata",
    "TavilySearchExtractAdapter",
    "assert_adapter_approved",
    "get_adapter_compliance_status",
    "prepare_source_record_insert",
    "redact_source_payload",
    "register_or_update_adapter_status",
    "sanitize_content_for_storage",
    "scan_source_payload_for_sensitive_content",
    "validate_compliance_memo_exists",
]
